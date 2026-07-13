"""会话管理器 - 管理 WorkSession 生命周期。

状态机：
    planning → running → paused → running (恢复)
                      → completed
                      → failed

所有状态转换均经过 ``_transition()`` 校验，保证非法转换被拒绝。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.session import WorkSession

logger = logging.getLogger("app.pipeline.session_manager")


# ---------------------------------------------------------------------------
# 合法状态转换表
# ---------------------------------------------------------------------------
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "planning": {"running", "failed"},
    "running": {"paused", "completed", "failed"},
    "paused": {"running", "completed", "failed"},
    "completed": set(),  # 终态
    "failed": {"running"},  # 失败后可重试
}

# 允许作为初始状态
_VALID_INITIAL = {"planning", "running"}


class SessionStateError(Exception):
    """非法状态转换异常。"""


class SessionNotFoundError(Exception):
    """会话不存在异常。"""


class SessionManager:
    """管理 WorkSession 生命周期。

    使用方式::

        manager = SessionManager(db, project_id)
        session = await manager.create_session(title="生成第1-3章", goal="...")
        session = await manager.start_session(session.id)
        ...
        session = await manager.complete_session(session.id, summary="完成")
    """

    def __init__(self, db: AsyncSession, project_id: uuid.UUID):
        self.db = db
        self.project_id = project_id

    # ------------------------------------------------------------------
    # 创建 / 查询
    # ------------------------------------------------------------------
    async def create_session(
        self,
        title: str,
        goal: str = "",
        mode: str = "L2",
        session_type: str = "advance_chapters",
        target_params: Optional[dict[str, Any]] = None,
        participants: Optional[list[str]] = None,
        quality_threshold: int = 85,
    ) -> WorkSession:
        """创建新的工作会话（初始状态 planning）。"""
        session = WorkSession(
            project_id=self.project_id,
            title=title,
            goal=goal,
            mode=mode,
            status="planning",
            session_type=session_type,
            participants=participants or [],
            target_params=target_params or {},
            quality_threshold=quality_threshold,
            blocking_issues=[],
            policy={},
            progress_percent=0.0,
            risk_level="low",
        )
        self.db.add(session)
        await self.db.flush()
        logger.info("创建会话 session_id=%s title='%s' mode=%s", session.id, title, mode)
        return session

    async def get_session(self, session_id: uuid.UUID) -> WorkSession:
        """按 ID 获取会话，不存在则抛 ``SessionNotFoundError``。"""
        stmt = select(WorkSession).where(
            WorkSession.id == session_id,
            WorkSession.project_id == self.project_id,
        )
        result = await self.db.execute(stmt)
        session = result.scalar_one_or_none()
        if session is None:
            raise SessionNotFoundError(f"会话 {session_id} 不存在")
        return session

    async def get_active_session(self) -> Optional[WorkSession]:
        """获取当前活跃会话（status=running 或 paused）。

        如果有多个活跃会话，返回最近创建的一个。
        """
        stmt = (
            select(WorkSession)
            .where(
                WorkSession.project_id == self.project_id,
                WorkSession.status.in_(["running", "paused"]),
            )
            .order_by(WorkSession.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[WorkSession]:
        """列出项目的会话，可按状态过滤。"""
        stmt = (
            select(WorkSession)
            .where(WorkSession.project_id == self.project_id)
            .order_by(WorkSession.created_at.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(WorkSession.status == status)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # 状态转换
    # ------------------------------------------------------------------
    async def start_session(self, session_id: uuid.UUID) -> WorkSession:
        """将 planning 状态的会话转为 running。"""
        session = await self.get_session(session_id)
        self._transition(session, "running")
        await self.db.flush()
        logger.info("会话 %s 已启动", session_id)
        return session

    async def pause_session(
        self,
        session_id: uuid.UUID,
        reason: str = "",
    ) -> WorkSession:
        """暂停会话（running → paused）。"""
        session = await self.get_session(session_id)
        self._transition(session, "paused")
        session.paused_reason = reason or None
        await self.db.flush()
        logger.info("会话 %s 已暂停，原因: %s", session_id, reason)
        return session

    async def resume_session(self, session_id: uuid.UUID) -> WorkSession:
        """恢复会话（paused → running）。"""
        session = await self.get_session(session_id)
        self._transition(session, "running")
        session.paused_reason = None
        await self.db.flush()
        logger.info("会话 %s 已恢复", session_id)
        return session

    async def complete_session(
        self,
        session_id: uuid.UUID,
        summary: str = "",
    ) -> WorkSession:
        """完成会话（→ completed）。"""
        session = await self.get_session(session_id)
        self._transition(session, "completed")
        session.progress_percent = 100.0
        # 将 summary 记入 next_action 的备注中（WorkSession 没有 summary 字段）
        if summary:
            current_next = dict(session.next_action or {})
            current_next["completion_summary"] = summary
            session.next_action = current_next
        await self.db.flush()
        logger.info("会话 %s 已完成: %s", session_id, summary[:100])
        return session

    async def fail_session(
        self,
        session_id: uuid.UUID,
        error: str = "",
    ) -> WorkSession:
        """标记会话失败（→ failed）。"""
        session = await self.get_session(session_id)
        self._transition(session, "failed")
        if error:
            issues = list(session.blocking_issues or [])
            issues.append(
                {
                    "type": "fatal_error",
                    "message": error,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            session.blocking_issues = issues
        await self.db.flush()
        logger.error("会话 %s 已失败: %s", session_id, error)
        return session

    # ------------------------------------------------------------------
    # 进度与阻塞
    # ------------------------------------------------------------------
    async def update_progress(
        self,
        session_id: uuid.UUID,
        progress_percent: float,
        current_score: Optional[int] = None,
        next_action: Optional[dict[str, Any]] = None,
    ) -> WorkSession:
        """更新会话进度。

        Args:
            progress_percent: 0.0 ~ 100.0
            current_score: 当前质量分数
            next_action: 下一步动作描述
        """
        session = await self.get_session(session_id)
        session.progress_percent = max(0.0, min(100.0, progress_percent))
        if current_score is not None:
            session.current_score = current_score
            session.quality_passed = current_score >= session.quality_threshold
        if next_action is not None:
            session.next_action = next_action
        await self.db.flush()
        return session

    async def add_blocking_issue(
        self,
        session_id: uuid.UUID,
        issue: dict[str, Any],
    ) -> WorkSession:
        """添加阻塞问题。

        issue 结构示例::

            {"type": "quality_below_threshold", "score": 72,
             "message": "章节质量分数低于阈值", "chapter_no": 5}
        """
        session = await self.get_session(session_id)
        issues = list(session.blocking_issues or [])
        issue.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        issues.append(issue)
        session.blocking_issues = issues
        # 自动提升风险等级
        issue_type = issue.get("type", "")
        if issue_type in ("fatal_error", "cannon_violation", "quality_below_threshold"):
            session.risk_level = "high"
        elif session.risk_level == "low":
            session.risk_level = "medium"
        await self.db.flush()
        logger.warning("会话 %s 添加阻塞问题: %s", session_id, issue.get("type"))
        return session

    async def resolve_blocking_issues(self, session_id: uuid.UUID) -> WorkSession:
        """解决所有阻塞问题（清空列表，风险降为 low）。"""
        session = await self.get_session(session_id)
        session.blocking_issues = []
        session.risk_level = "low"
        await self.db.flush()
        logger.info("会话 %s 所有阻塞问题已解决", session_id)
        return session

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _transition(self, session: WorkSession, target: str) -> None:
        """校验并执行状态转换。

        Raises:
            SessionStateError: 非法转换
        """
        current = session.status
        if target not in _VALID_TRANSITIONS.get(current, set()):
            raise SessionStateError(
                f"非法状态转换: {current} → {target}"
                f"（允许的目标: {_VALID_TRANSITIONS.get(current, set())}）"
            )
        session.status = target

    async def get_session_status(self, session_id: uuid.UUID) -> dict[str, Any]:
        """获取会话状态摘要（用于 Cockpit 展示）。"""
        session = await self.get_session(session_id)
        return {
            "session_id": str(session.id),
            "title": session.title,
            "status": session.status,
            "mode": session.mode,
            "session_type": session.session_type,
            "progress_percent": session.progress_percent,
            "current_score": session.current_score,
            "quality_passed": session.quality_passed,
            "quality_threshold": session.quality_threshold,
            "risk_level": session.risk_level,
            "blocking_issues_count": len(session.blocking_issues or []),
            "paused_reason": session.paused_reason,
            "next_action": session.next_action,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        }
