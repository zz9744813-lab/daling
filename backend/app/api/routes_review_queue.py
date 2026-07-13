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
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chief_editor import ChiefEditor
from app.agents.continuity_guard import ContinuityGuard
from app.agents.critic import Critic
from app.agents.memory_keeper import MemoryKeeper
from app.agents.rewriter import Rewriter
from app.db import get_db
from app.db.models.chapter import Chapter, ChapterVersion, ManuscriptBlock
from app.db.models.session import ReviewQueueItem, WorkSession
from app.model_gateway import gateway
from app.pipeline.session_manager import SessionManager
from app.services.autonomous_learning import AutonomousLearningService
from app.services.continuous_production import continuous_production_service
from app.services.prompt_evolution import PromptEvolutionService
from app.services.quality_ledger import QualityLedger

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
    revision_instruction: Optional[str] = None
    # A quality hold can contain several immutable candidates.  Operators and
    # automation may explicitly repair the strongest historical candidate
    # instead of being forced to start from a later regressed working version.
    base_version_id: Optional[uuid.UUID] = None


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

    # takeover 必须真正暂停连续 Worker，不能“恢复会话”。
    if new_status == "takeover" and item.session_id:
        manager = SessionManager(db, project_id)
        try:
            await manager.pause_session(item.session_id, reason="用户接管")
        except Exception as exc:  # noqa: BLE001
            logger.warning("暂停会话失败: %s", exc)

    await db.flush()
    return item


async def _load_chapter_for_item(
    db: AsyncSession,
    item: ReviewQueueItem,
    base_version_id: Optional[uuid.UUID] = None,
) -> tuple[Chapter, Optional[ChapterVersion], str]:
    if item.artifact_type != "chapter" or not item.artifact_id:
        raise HTTPException(status_code=422, detail="该审阅条目未关联章节")
    chapter = await db.get(Chapter, item.artifact_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="关联章节不存在")
    requested_version_id = base_version_id or chapter.current_version_id
    version = await db.get(ChapterVersion, requested_version_id) if requested_version_id else None
    if base_version_id is not None and (
        version is None or version.chapter_id != chapter.id
    ):
        raise HTTPException(
            status_code=409,
            detail="指定的基础版本不存在或不属于当前章节",
        )
    content = version.content if version else ""
    if not content:
        result = await db.execute(
            select(ManuscriptBlock)
            .where(ManuscriptBlock.chapter_id == chapter.id)
            .order_by(ManuscriptBlock.block_no)
        )
        content = "\n\n".join(block.content for block in result.scalars().all() if block.content)
    return chapter, version, content


async def _production_prompt(db: AsyncSession, project_id: uuid.UUID, role: str):
    """Resolve the exact project prompt plus qualified champion for one role."""
    return await PromptEvolutionService(db, project_id).resolve_production_prompt(role)


async def _record_review_feedback(
    db: AsyncSession,
    item: ReviewQueueItem,
    *,
    action: str,
    payload: Optional[DecisionRequest],
    chapter: Optional[Chapter] = None,
    version: Optional[ChapterVersion] = None,
    original_text: Optional[str] = None,
    edited_text: Optional[str] = None,
) -> None:
    version_scope = f":version:{version.id}" if version is not None else ""
    await QualityLedger(db, item.project_id).record_feedback(
        idempotency_key=f"review:{item.id}:{action}{version_scope}",
        action=action,
        actor=payload.decided_by if payload else "user",
        chapter_id=chapter.id if chapter else None,
        version_id=version.id if version else None,
        session_id=item.session_id,
        review_item_id=item.id,
        original_text=original_text,
        edited_text=edited_text,
        instruction=(payload.revision_instruction or payload.decision_notes if payload else None),
        tags=["review_queue", item.item_type],
    )


async def _replace_working_blocks(
    db: AsyncSession,
    chapter: Chapter,
    blocks: list[ManuscriptBlock],
    version: ChapterVersion,
) -> None:
    existing = (
        (await db.execute(select(ManuscriptBlock).where(ManuscriptBlock.chapter_id == chapter.id)))
        .scalars()
        .all()
    )
    for block in existing:
        await db.delete(block)
    await db.flush()
    for block in blocks:
        block.chapter_id = chapter.id
        block.version_id = version.id
        db.add(block)
    await db.flush()


async def _revise_and_recheck(
    db: AsyncSession,
    item: ReviewQueueItem,
    payload: DecisionRequest,
) -> tuple[Chapter, ChapterVersion, bool]:
    chapter, previous_version, original_text = await _load_chapter_for_item(
        db,
        item,
        base_version_id=payload.base_version_id,
    )
    if not original_text.strip():
        raise HTTPException(status_code=409, detail="章节没有可修订的正文")
    prompt_service = PromptEvolutionService(db, item.project_id)
    rewriter_prompt = await prompt_service.resolve_production_prompt("Rewriter")
    rewriter = Rewriter(gateway, db, item.project_id, item.session_id)
    rewriter.custom_system_prompt = rewriter_prompt.text
    rewriter.prompt_provenance = rewriter_prompt.audit_payload()
    old_blocks = [
        {"content": paragraph, "block_type": "paragraph", "block_no": index}
        for index, paragraph in enumerate(
            [part.strip() for part in original_text.split("\n\n") if part.strip()],
            start=1,
        )
    ]

    if payload.revised_content and payload.revised_content.strip():
        revised_text = payload.revised_content.strip()
        if len(revised_text) < 100:
            raise HTTPException(status_code=422, detail="修订后的正文不能少于 100 字")
        new_blocks = rewriter._split_into_blocks(revised_text, chapter.id)
    else:
        instruction = (payload.revision_instruction or payload.decision_notes or "").strip()
        if not instruction:
            raise HTTPException(status_code=422, detail="请提供修订正文或重写要求")
        new_blocks = await rewriter.rewrite_texts(
            old_blocks,
            [
                {
                    "source": "user",
                    "category": "human_feedback",
                    "severity": "high",
                    "description": instruction,
                }
            ],
            chapter_id=chapter.id,
        )
    revised_text = "\n\n".join(block.content for block in new_blocks if block.content)

    max_version = await db.scalar(
        select(ChapterVersion.version_no)
        .where(ChapterVersion.chapter_id == chapter.id)
        .order_by(ChapterVersion.version_no.desc())
        .limit(1)
    )
    revision = ChapterVersion(
        chapter_id=chapter.id,
        version_no=(max_version or 0) + 1,
        content=revised_text,
        word_count=len(revised_text.replace("\n", "").replace(" ", "")),
        status="revision",
        created_by_agent="user" if payload.revised_content else "Rewriter:user-feedback",
    )
    db.add(revision)
    await db.flush()
    await _replace_working_blocks(db, chapter, new_blocks, revision)
    chapter.current_version_id = revision.id
    chapter.word_count = revision.word_count
    chapter.status = "review"

    critic_prompt = await prompt_service.resolve_production_prompt("Critic")
    guard_prompt = await prompt_service.resolve_production_prompt("ContinuityGuard")
    critic = Critic(gateway, db, item.project_id, item.session_id)
    guard = ContinuityGuard(gateway, db, item.project_id, item.session_id)
    critic.custom_system_prompt = critic_prompt.text
    critic.prompt_provenance = critic_prompt.audit_payload()
    guard.custom_system_prompt = guard_prompt.text
    guard.prompt_provenance = guard_prompt.audit_payload()
    snapshot = [
        {"content": block.content, "block_type": block.block_type, "block_no": block.block_no}
        for block in new_blocks
    ]
    critic_result = await critic.review_texts(snapshot)
    continuity_result = await guard.check_texts(snapshot, chapter.chapter_no)
    critic_result = {
        **critic_result,
        "prompt_provenance": critic_prompt.audit_payload(),
    }
    continuity_result = {
        **continuity_result,
        "prompt_provenance": guard_prompt.audit_payload(),
    }
    ledger = QualityLedger(db, item.project_id)
    await ledger.record_critic_assessment(
        idempotency_key=f"review:{item.id}:version:{revision.id}:critic",
        result=critic_result,
        chapter_id=chapter.id,
        version_id=revision.id,
        session_id=item.session_id,
    )
    await ledger.record_continuity_assessment(
        idempotency_key=f"review:{item.id}:version:{revision.id}:continuity",
        result=continuity_result,
        chapter_id=chapter.id,
        version_id=revision.id,
        session_id=item.session_id,
    )

    threshold = 85
    if item.session_id:
        work_session = await db.get(WorkSession, item.session_id)
        if work_session:
            threshold = work_session.quality_threshold
    editor = ChiefEditor(gateway, db, item.project_id, item.session_id)
    editor_prompt = await prompt_service.resolve_production_prompt("ChiefEditor")
    editor.custom_system_prompt = editor_prompt.text
    editor.prompt_provenance = editor_prompt.audit_payload()
    final = await editor.finalize(
        chapter.id,
        critic_result,
        continuity_result,
        quality_threshold=threshold,
    )
    approved = bool(final.get("approved"))
    final_score = float(final.get("final_score") or 0)
    final_version = await db.get(ChapterVersion, chapter.current_version_id)
    evidence_version = final_version or revision
    await ledger.record_assessment(
        idempotency_key=f"review:{item.id}:version:{evidence_version.id}:final-gate",
        assessor="ChiefEditor",
        assessment_type="deterministic_gate",
        dimension_scores={"final": final_score},
        overall_score=final_score,
        verdict="approved" if approved else "review",
        passed=approved,
        issues=(
            []
            if approved
            else [
                {
                    "source": "ChiefEditor",
                    "category": "quality_gate",
                    "severity": "high",
                    "description": final.get("notes") or "章节未通过最终质量闸门",
                }
            ]
        ),
        raw_result={
            **final,
            "production_prompt": editor_prompt.audit_payload(),
            "prompt_provenance": editor_prompt.audit_payload(),
        },
        chapter_id=chapter.id,
        version_id=evidence_version.id,
        session_id=item.session_id,
        round_no=1,
        rubric_version=f"threshold-{threshold}",
    )
    await _record_review_feedback(
        db,
        item,
        action="revise",
        payload=payload,
        chapter=chapter,
        version=final_version or revision,
        original_text=original_text,
        edited_text=revised_text,
    )

    if approved:
        keeper = MemoryKeeper(gateway, db, item.project_id, item.session_id)
        keeper_prompt = await prompt_service.resolve_production_prompt("MemoryKeeper")
        keeper.custom_system_prompt = keeper_prompt.text
        keeper.prompt_provenance = keeper_prompt.audit_payload()
        await keeper.update_state(chapter.id, snapshot)
        await AutonomousLearningService(db, item.project_id).run_post_chapter_cycle(
            chapter_no=chapter.chapter_no,
            session_id=item.session_id,
        )
    else:
        item.title = f"第{chapter.chapter_no}章修订后仍未通过质量闸门"
        item.description = (
            f"版本 {final_version.version_no if final_version else revision.version_no}；"
            f"复检得分 {final.get('final_score', 0)}/{threshold}；"
            f"{final.get('notes') or '仍需继续修订'}"
        )
        item.risk_level = "high"
        item.status = "pending"
    await db.flush()
    return chapter, final_version or revision, approved


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("/{project_id}", response_model=list[ReviewItemOut])
async def list_review_queue(
    project_id: uuid.UUID,
    status: Optional[str] = Query(
        None,
        description="过滤状态: pending/approved/revised/rejected/takeover",
    ),
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
    """人工批准当前版本，并真实更新章节记忆与学习证据。"""
    item = await _apply_decision(db, project_id, item_id, "approved", payload)
    if item.artifact_type == "chapter" and item.artifact_id:
        chapter, version, content = await _load_chapter_for_item(db, item)
        chapter.status = "approved"
        if version:
            version.status = "approved"
            await QualityLedger(db, project_id).sync_chapter_issue_statuses(
                chapter_id=chapter.id,
                current_version_id=version.id,
                approved=True,
            )
        await _record_review_feedback(
            db,
            item,
            action="approve",
            payload=payload,
            chapter=chapter,
            version=version,
            original_text=content,
        )
        if content.strip():
            blocks = [
                {"content": part, "block_type": "paragraph", "block_no": index}
                for index, part in enumerate(
                    [part.strip() for part in content.split("\n\n") if part.strip()],
                    start=1,
                )
            ]
            prompt = await _production_prompt(db, project_id, "MemoryKeeper")
            keeper = MemoryKeeper(gateway, db, project_id, item.session_id)
            keeper.custom_system_prompt = prompt.text
            keeper.prompt_provenance = prompt.audit_payload()
            await keeper.update_state(chapter.id, blocks)
            await AutonomousLearningService(db, project_id).run_post_chapter_cycle(
                chapter_no=chapter.chapter_no,
                session_id=item.session_id,
            )
    logger.info("审批条目 %s 已批准", item_id)
    return _to_out(item)


@router.post("/{project_id}/items/{item_id}/revise", response_model=ReviewItemOut)
async def revise_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: Optional[DecisionRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """应用人工正文或调用 Rewriter，再运行 Critic/Continuity/ChiefEditor。"""
    if payload is None:
        raise HTTPException(status_code=422, detail="请提供修订正文或重写要求")
    pending = await _get_item(db, project_id, item_id)
    if pending.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"审批条目当前状态为 {pending.status}，无法再次决策",
        )
    _chapter, version, approved = await _revise_and_recheck(db, pending, payload)
    if approved:
        item = await _apply_decision(db, project_id, item_id, "revised", payload)
        item.decision_notes = (
            f"修订已生成版本 {version.version_no}；重新质检结果：通过。"
            f"{payload.decision_notes or ''}"
        ).strip()
        logger.info("审批条目 %s 修订后已通过复检", item_id)
    else:
        item = pending
        item.decision_notes = (
            f"修订已生成版本 {version.version_no}；重新质检仍未通过，条目保持待审。"
            f"{payload.decision_notes or ''}"
        ).strip()
        await db.flush()
        logger.info("审批条目 %s 修订后仍待审", item_id)
    return _to_out(item)


@router.post("/{project_id}/items/{item_id}/reject", response_model=ReviewItemOut)
async def reject_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: Optional[DecisionRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """驳回并阻断关联章节与连续任务。"""
    pending = await _get_item(db, project_id, item_id)
    if pending.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"审批条目当前状态为 {pending.status}，无法再次决策",
        )
    await continuous_production_service.pause(project_id, reason="人工驳回审阅项")
    item = await _apply_decision(db, project_id, item_id, "rejected", payload)
    if pending.artifact_type == "chapter" and pending.artifact_id:
        chapter, version, content = await _load_chapter_for_item(db, pending)
        chapter.status = "blocked"
        await _record_review_feedback(
            db,
            item,
            action="reject",
            payload=payload,
            chapter=chapter,
            version=version,
            original_text=content,
        )
    logger.info("审批条目 %s 已驳回", item_id)
    return _to_out(item)


@router.post("/{project_id}/items/{item_id}/takeover", response_model=ReviewItemOut)
async def takeover_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: Optional[DecisionRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """接管审批条目并真正暂停 24 小时 Worker。"""
    pending = await _get_item(db, project_id, item_id)
    if pending.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"审批条目当前状态为 {pending.status}，无法再次决策",
        )
    await continuous_production_service.pause(project_id, reason="用户接管审阅章节")
    item = await _apply_decision(db, project_id, item_id, "takeover", payload)
    if pending.artifact_type == "chapter" and pending.artifact_id:
        chapter, version, content = await _load_chapter_for_item(db, pending)
        await _record_review_feedback(
            db,
            item,
            action="takeover",
            payload=payload,
            chapter=chapter,
            version=version,
            original_text=content,
        )
        manual_item = ReviewQueueItem(
            project_id=project_id,
            session_id=item.session_id,
            item_type="manual_edit_review",
            artifact_type="chapter",
            artifact_id=chapter.id,
            chapter_no=chapter.chapter_no,
            title=f"第 {chapter.chapter_no} 章已人工接管",
            description="编辑完整正文后，使用“修改重审”提交 Critic、连续性检查与终审。",
            risk_level="medium",
            status="pending",
        )
        db.add(manual_item)
        await db.flush()
    logger.info("审批条目 %s 已接管", item_id)
    return _to_out(item)
