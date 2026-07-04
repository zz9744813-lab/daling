"""Boss 命令处理器 - 解析并执行来自 Cockpit 的 Boss 指令。

支持两种解析模式：
1. **关键词匹配**（默认，零延迟）：通过 ``COMMAND_MAP`` 中文关键词映射
2. **LLM 理解**（可选，更智能）：当关键词匹配结果为 unknown 时，
   调用 LLM 进行意图理解（LLM 未配置时自动降级为关键词匹配）

命令意图：
    start / resume / pause / stop / rewrite / skip / modify / status / unknown
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chapter import Chapter
from app.db.models.session import AgentRun, ReviewQueueItem, WorkSession
from app.pipeline.llm_client import get_llm_client
from app.pipeline.session_manager import SessionManager, SessionNotFoundError, SessionStateError

logger = logging.getLogger("app.pipeline.boss_command")


class BossCommandProcessor:
    """处理来自 Cockpit 的 Boss 指令。

    使用方式::

        processor = BossCommandProcessor(db, project_id)
        result = await processor.process("暂停当前生成", session_id=session.id)
    """

    # 命令关键词映射（按优先级排列，先匹配的优先）
    COMMAND_MAP: list[tuple[str, str]] = [
        ("继续", "resume"),
        ("恢复", "resume"),
        ("开始", "start"),
        ("启动", "start"),
        ("暂停", "pause"),
        ("停下", "pause"),
        ("停止", "stop"),
        ("终止", "stop"),
        ("取消", "stop"),
        ("返工", "rewrite"),
        ("重写", "rewrite"),
        ("跳过", "skip"),
        ("修改", "modify"),
        ("调整", "modify"),
        ("查看", "status"),
        ("状态", "status"),
        ("当前", "status"),
    ]

    def __init__(self, db: AsyncSession, project_id: uuid.UUID):
        self.db = db
        self.project_id = project_id
        self.session_manager = SessionManager(db, project_id)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    async def process(
        self,
        command: str,
        project_id: Optional[uuid.UUID] = None,
        session_id: Optional[uuid.UUID] = None,
    ) -> dict[str, Any]:
        """解析并执行 Boss 指令。

        Args:
            command: 原始指令文本（中文）
            project_id: 项目 ID（可选，默认用 self.project_id）
            session_id: 目标会话 ID（可选，自动查找活跃会话）

        Returns:
            执行结果字典::

                {
                    "ok": bool,
                    "intent": str,       # 解析出的意图
                    "command": str,      # 原始指令
                    "message": str,      # 人类可读消息
                    "data": dict,        # 附加数据
                }
        """
        pid = project_id or self.project_id

        # 1. 解析意图
        intent = self._parse_intent(command)

        # 2. 如果关键词匹配失败，尝试 LLM 理解
        if intent == "unknown":
            intent = await self._parse_intent_with_llm(command)

        logger.info("Boss 指令解析: '%s' → intent='%s'", command, intent)

        # 3. 路由到对应处理器
        try:
            if intent == "pause":
                return await self._handle_pause(session_id, command)
            elif intent == "resume":
                return await self._handle_resume(session_id, command)
            elif intent == "start":
                return await self._handle_start(session_id, command)
            elif intent == "stop":
                return await self._handle_stop(session_id, command)
            elif intent == "rewrite":
                return await self._handle_rewrite(session_id, command)
            elif intent == "skip":
                return await self._handle_skip(session_id, command)
            elif intent == "modify":
                return await self._handle_modify(session_id, command)
            elif intent == "status":
                return await self._get_status(pid)
            else:
                return {
                    "ok": False,
                    "intent": "unknown",
                    "command": command,
                    "message": f"无法识别指令: '{command}'。支持的指令: 继续/暂停/停止/返工/跳过/修改/查看",
                    "data": {},
                }
        except SessionNotFoundError as exc:
            return {
                "ok": False,
                "intent": intent,
                "command": command,
                "message": f"会话不存在: {exc}",
                "data": {},
            }
        except SessionStateError as exc:
            return {
                "ok": False,
                "intent": intent,
                "command": command,
                "message": f"状态转换失败: {exc}",
                "data": {},
            }

    # ------------------------------------------------------------------
    # 意图解析
    # ------------------------------------------------------------------
    def _parse_intent(self, command: str) -> str:
        """通过关键词匹配解析命令意图。"""
        for keyword, intent in self.COMMAND_MAP:
            if keyword in command:
                return intent
        return "unknown"

    async def _parse_intent_with_llm(self, command: str) -> str:
        """使用 LLM 理解命令意图（LLM 未配置时返回 unknown）。"""
        client = get_llm_client()
        if not client.is_configured:
            return "unknown"

        system = (
            "你是一个指令解析器。将用户的中文指令映射为以下意图之一:\n"
            "start, resume, pause, stop, rewrite, skip, modify, status, unknown\n"
            "只输出意图词，不要输出其他内容。"
        )
        prompt = f"用户指令: {command}\n意图:"
        resp = await client.judge(prompt, system=system)
        if not resp.ok:
            return "unknown"

        result = resp.content.strip().lower()
        valid_intents = {
            "start", "resume", "pause", "stop",
            "rewrite", "skip", "modify", "status", "unknown",
        }
        if result in valid_intents:
            return result
        return "unknown"

    # ------------------------------------------------------------------
    # 各意图处理器
    # ------------------------------------------------------------------
    async def _resolve_session(
        self,
        session_id: Optional[uuid.UUID],
    ) -> Optional[WorkSession]:
        """解析目标会话：如果 session_id 为空，自动查找活跃会话。"""
        if session_id:
            return await self.session_manager.get_session(session_id)
        return await self.session_manager.get_active_session()

    async def _handle_pause(
        self,
        session_id: Optional[uuid.UUID],
        command: str,
    ) -> dict[str, Any]:
        session = await self._resolve_session(session_id)
        if session is None:
            return {
                "ok": False, "intent": "pause", "command": command,
                "message": "没有活跃的会话可以暂停", "data": {},
            }
        await self.session_manager.pause_session(session.id, reason=command)
        return {
            "ok": True, "intent": "pause", "command": command,
            "message": f"会话 '{session.title}' 已暂停",
            "data": {"session_id": str(session.id), "status": "paused"},
        }

    async def _handle_resume(
        self,
        session_id: Optional[uuid.UUID],
        command: str,
    ) -> dict[str, Any]:
        session = await self._resolve_session(session_id)
        if session is None:
            return {
                "ok": False, "intent": "resume", "command": command,
                "message": "没有可恢复的会话", "data": {},
            }
        await self.session_manager.resume_session(session.id)
        return {
            "ok": True, "intent": "resume", "command": command,
            "message": f"会话 '{session.title}' 已恢复运行",
            "data": {"session_id": str(session.id), "status": "running"},
        }

    async def _handle_start(
        self,
        session_id: Optional[uuid.UUID],
        command: str,
    ) -> dict[str, Any]:
        session = await self._resolve_session(session_id)
        if session is None:
            return {
                "ok": False, "intent": "start", "command": command,
                "message": "没有可启动的会话，请先创建会话", "data": {},
            }
        if session.status == "planning":
            await self.session_manager.start_session(session.id)
        elif session.status == "paused":
            await self.session_manager.resume_session(session.id)
        else:
            return {
                "ok": False, "intent": "start", "command": command,
                "message": f"会话当前状态为 {session.status}，无法启动",
                "data": {"session_id": str(session.id), "status": session.status},
            }
        return {
            "ok": True, "intent": "start", "command": command,
            "message": f"会话 '{session.title}' 已启动",
            "data": {"session_id": str(session.id), "status": "running"},
        }

    async def _handle_stop(
        self,
        session_id: Optional[uuid.UUID],
        command: str,
    ) -> dict[str, Any]:
        session = await self._resolve_session(session_id)
        if session is None:
            return {
                "ok": False, "intent": "stop", "command": command,
                "message": "没有活跃的会话可以停止", "data": {},
            }
        await self.session_manager.fail_session(session.id, error=f"用户停止: {command}")
        return {
            "ok": True, "intent": "stop", "command": command,
            "message": f"会话 '{session.title}' 已停止",
            "data": {"session_id": str(session.id), "status": "failed"},
        }

    async def _handle_rewrite(
        self,
        session_id: Optional[uuid.UUID],
        command: str,
    ) -> dict[str, Any]:
        """标记当前章节需要返工，创建审批队列条目。"""
        session = await self._resolve_session(session_id)
        sid = session.id if session else None

        # 查找当前章节
        current_chapter_no = 0
        if session and session.target_params:
            current_chapter_no = session.target_params.get("current_chapter_no", 0)

        # 查找最近生成的章节
        chapter = None
        if current_chapter_no:
            stmt = select(Chapter).where(
                Chapter.project_id == self.project_id,
                Chapter.chapter_no == current_chapter_no,
            )
            result = await self.db.execute(stmt)
            chapter = result.scalar_one_or_none()

        if chapter is None:
            # 查找最后一个章节
            stmt = (
                select(Chapter)
                .where(Chapter.project_id == self.project_id)
                .order_by(Chapter.chapter_no.desc())
                .limit(1)
            )
            result = await self.db.execute(stmt)
            chapter = result.scalar_one_or_none()

        review_item = ReviewQueueItem(
            project_id=self.project_id,
            session_id=sid,
            item_type="chapter",
            artifact_type="chapter",
            artifact_id=chapter.id if chapter else None,
            title=f"返工: 第{chapter.chapter_no}章" if chapter else "返工请求",
            description=f"Boss 指令: {command}",
            risk_level="medium",
            status="pending",
            chapter_no=chapter.chapter_no if chapter else None,
        )
        self.db.add(review_item)

        if session:
            await self.session_manager.add_blocking_issue(
                session.id,
                {
                    "type": "rewrite_requested",
                    "message": command,
                    "chapter_no": chapter.chapter_no if chapter else None,
                },
            )

        await self.db.flush()
        return {
            "ok": True, "intent": "rewrite", "command": command,
            "message": f"已标记第{chapter.chapter_no}章需要返工" if chapter else "已创建返工请求",
            "data": {
                "review_item_id": str(review_item.id),
                "chapter_no": chapter.chapter_no if chapter else None,
            },
        }

    async def _handle_skip(
        self,
        session_id: Optional[uuid.UUID],
        command: str,
    ) -> dict[str, Any]:
        """跳过当前章节。"""
        session = await self._resolve_session(session_id)
        sid = session.id if session else None

        review_item = ReviewQueueItem(
            project_id=self.project_id,
            session_id=sid,
            item_type="skip",
            title="跳过当前章节",
            description=f"Boss 指令: {command}",
            risk_level="low",
            status="pending",
        )
        self.db.add(review_item)
        await self.db.flush()

        return {
            "ok": True, "intent": "skip", "command": command,
            "message": "已创建跳过请求，等待确认",
            "data": {"review_item_id": str(review_item.id)},
        }

    async def _handle_modify(
        self,
        session_id: Optional[uuid.UUID],
        command: str,
    ) -> dict[str, Any]:
        """修改请求 - 创建审批条目。"""
        session = await self._resolve_session(session_id)
        sid = session.id if session else None

        review_item = ReviewQueueItem(
            project_id=self.project_id,
            session_id=sid,
            item_type="modify",
            title="修改请求",
            description=f"Boss 指令: {command}",
            risk_level="low",
            status="pending",
        )
        self.db.add(review_item)
        await self.db.flush()

        return {
            "ok": True, "intent": "modify", "command": command,
            "message": "已创建修改请求",
            "data": {"review_item_id": str(review_item.id)},
        }

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    async def _get_status(self, project_id: uuid.UUID) -> dict[str, Any]:
        """获取当前项目状态摘要。"""
        # 活跃会话
        session = await self.session_manager.get_active_session()

        # 最近 AgentRun
        stmt = (
            select(AgentRun)
            .where(AgentRun.project_id == project_id)
            .order_by(AgentRun.created_at.desc())
            .limit(10)
        )
        result = await self.db.execute(stmt)
        recent_runs = [
            {
                "agent_name": r.agent_name,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "duration_ms": r.duration_ms,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
            }
            for r in result.scalars().all()
        ]

        # 审批队列 pending 数量
        stmt = (
            select(ReviewQueueItem)
            .where(
                ReviewQueueItem.project_id == project_id,
                ReviewQueueItem.status == "pending",
            )
        )
        result = await self.db.execute(stmt)
        pending_count = len(result.scalars().all())

        # 最近章节
        stmt = (
            select(Chapter)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.chapter_no.desc())
            .limit(5)
        )
        result = await self.db.execute(stmt)
        chapters = [
            {
                "chapter_no": c.chapter_no,
                "title": c.title,
                "status": c.status,
                "word_count": c.word_count,
            }
            for c in result.scalars().all()
        ]

        return {
            "ok": True,
            "intent": "status",
            "command": "",
            "message": "状态查询成功",
            "data": {
                "session": (
                    {
                        "id": str(session.id),
                        "title": session.title,
                        "status": session.status,
                        "progress_percent": session.progress_percent,
                        "current_score": session.current_score,
                    }
                    if session
                    else None
                ),
                "recent_runs": recent_runs,
                "pending_reviews": pending_count,
                "recent_chapters": chapters,
            },
        }
