"""审批队列路由 - 列表 + approve/revise/reject/takeover。

提供完整的审批队列 CRUD 与决策操作：
- GET   /{project_id}                          — 查询审批队列（支持 status 过滤）
- POST  /{project_id}/items/{item_id}/approve  — 批准
- POST  /{project_id}/items/{item_id}/revise   — 修改
- POST  /{project_id}/items/{item_id}/reject   — 驳回
- POST  /{project_id}/items/{item_id}/takeover — 接管
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.models.session import ReviewQueueItem
from app.pipeline.session_manager import SessionManager

logger = logging.getLogger("app.api.review_queue")

router = APIRouter(prefix="/api/review-queue", tags=["review-queue"])


# ---------------------------------------------------------------------------
# 响应 / 请求模型
# ---------------------------------------------------------------------------
class ReviewItemOut(BaseModel):
    id: str
    project_id: str
    session_id: Optional[str] = None
    # 前端期望 ``type``（值 = item_type）
    type: str = "continuity"
    item_type: Optional[str] = None  # 兼容旧字段
    # 前端期望 ``severity``（值 = risk_level 做值映射）
    severity: str = "info"
    risk_level: Optional[str] = None  # 兼容旧字段
    # 前端期望 ``chapter_id``（当 artifact_type == "chapter" 时取 artifact_id）
    chapter_id: Optional[str] = None
    artifact_type: Optional[str] = None
    artifact_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    status: str
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    decision_notes: Optional[str] = None
    chapter_no: Optional[int] = None
    created_at: Optional[str] = None


class DecisionRequest(BaseModel):
    decision_notes: Optional[str] = None
    decided_by: str = "user"
    # revise 时可附带修改内容
    revised_content: Optional[str] = None


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
# risk_level → severity 值映射
_RISK_TO_SEVERITY: dict[str, str] = {
    "low": "info",
    "medium": "warning",
    "high": "critical",
}


def _to_out(item: ReviewQueueItem) -> ReviewItemOut:
    risk_level = item.risk_level or "low"
    severity = _RISK_TO_SEVERITY.get(risk_level, "info")
    # chapter_id：当 artifact_type == "chapter" 时取 artifact_id
    chapter_id = None
    if item.artifact_type == "chapter" and item.artifact_id:
        chapter_id = str(item.artifact_id)
    return ReviewItemOut(
        id=str(item.id),
        project_id=str(item.project_id),
        session_id=str(item.session_id) if item.session_id else None,
        type=item.item_type,
        item_type=item.item_type,
        severity=severity,
        risk_level=risk_level,
        chapter_id=chapter_id,
        artifact_type=item.artifact_type,
        artifact_id=str(item.artifact_id) if item.artifact_id else None,
        title=item.title,
        description=item.description,
        status=item.status,
        decided_by=item.decided_by,
        decided_at=item.decided_at.isoformat() if item.decided_at else None,
        decision_notes=item.decision_notes,
        chapter_no=item.chapter_no,
        created_at=item.created_at.isoformat() if item.created_at else None,
    )


async def _get_item(
    db: AsyncSession,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
) -> ReviewQueueItem:
    stmt = select(ReviewQueueItem).where(
        ReviewQueueItem.id == item_id,
        ReviewQueueItem.project_id == project_id,
    )
    result = await db.execute(stmt)
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"审批条目 {item_id} 不存在")
    return item


async def _apply_decision(
    db: AsyncSession,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    new_status: str,
    payload: Optional[DecisionRequest] = None,
) -> ReviewQueueItem:
    """通用决策应用逻辑。

    ``payload`` 可为 ``None``（前端 approve/reject/takeover 不带 body）。
    """
    item = await _get_item(db, project_id, item_id)

    if item.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"审批条目当前状态为 {item.status}，无法再次决策",
        )

    item.status = new_status
    item.decided_by = payload.decided_by if payload else "user"
    item.decided_at = datetime.now(timezone.utc)
    item.decision_notes = payload.decision_notes if payload else None

    # 如果有关联的会话，且是 takeover，恢复会话
    if new_status == "takeover" and item.session_id:
        manager = SessionManager(db, project_id)
        try:
            await manager.resume_session(item.session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("恢复会话失败: %s", exc)

    await db.flush()
    return item


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("/{project_id}", response_model=list[ReviewItemOut])
async def list_review_queue(
    project_id: uuid.UUID,
    status: Optional[str] = Query(None, description="过滤状态: pending/approved/revised/rejected/takeover"),
    item_type: Optional[str] = Query(None, description="过滤条目类型"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """查询审批队列，支持按 status 和 item_type 过滤。"""
    stmt = (
        select(ReviewQueueItem)
        .where(ReviewQueueItem.project_id == project_id)
        .order_by(ReviewQueueItem.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(ReviewQueueItem.status == status)
    if item_type:
        stmt = stmt.where(ReviewQueueItem.item_type == item_type)

    result = await db.execute(stmt)
    items = result.scalars().all()
    return [_to_out(i) for i in items]


@router.post("/{project_id}/items/{item_id}/approve", response_model=ReviewItemOut)
async def approve_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: Optional[DecisionRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """批准审批条目（前端可带空 body）。"""
    item = await _apply_decision(db, project_id, item_id, "approved", payload)
    logger.info("审批条目 %s 已批准", item_id)
    return _to_out(item)


@router.post("/{project_id}/items/{item_id}/revise", response_model=ReviewItemOut)
async def revise_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: Optional[DecisionRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """修改审批条目（用户提供了修改内容）。"""
    item = await _apply_decision(db, project_id, item_id, "revised", payload)
    # 如果有修改内容，记录到 decision_notes
    if payload and payload.revised_content:
        notes = (payload.decision_notes or "") + f"\n修改内容: {payload.revised_content}"
        item.decision_notes = notes.strip()
    logger.info("审批条目 %s 已修改", item_id)
    return _to_out(item)


@router.post("/{project_id}/items/{item_id}/reject", response_model=ReviewItemOut)
async def reject_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: Optional[DecisionRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """驳回审批条目（前端可带空 body）。"""
    item = await _apply_decision(db, project_id, item_id, "rejected", payload)
    logger.info("审批条目 %s 已驳回", item_id)
    return _to_out(item)


@router.post("/{project_id}/items/{item_id}/takeover", response_model=ReviewItemOut)
async def takeover_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: Optional[DecisionRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """接管审批条目（用户接管控制权，前端可带空 body）。"""
    item = await _apply_decision(db, project_id, item_id, "takeover", payload)
    logger.info("审批条目 %s 已接管", item_id)
    return _to_out(item)
