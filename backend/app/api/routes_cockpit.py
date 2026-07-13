"""驾驶舱路由 - 状态聚合 / SSE 实时流 / 指令 / 接管 / 章节 / 运行。

返回结构对齐前端 ``CockpitData`` TypeScript 类型：
- ``active_session`` (原 ``session``)
- ``agent_statuses`` (原 ``agents``，元素结构改为 ``{agent_role, status, message, current_task}``)
- ``recent_runs`` (原 ``recent_events``，匹配前端 ``AgentRun`` 类型)
- ``review_queue_count``
- ``current_chapter`` (新增，返回当前章节或最新章节)

补充前端依赖的章节 / 版本 / 运行查询路由。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.db import async_session_factory, get_db
from app.db.models.automation import ContinuousRunEvent
from app.db.models.chapter import Chapter, ChapterVersion, ManuscriptBlock
from app.db.models.session import AgentRun, ReviewQueueItem, WorkSession
from app.services.continuous_production import continuous_production_service
from app.services.quality_ledger import QualityLedger

logger = logging.getLogger("app.api.cockpit")

router = APIRouter(prefix="/api/cockpit", tags=["cockpit"])

# 全部 8 个 Agent 角色（与前端 AgentRole 枚举一致）
ALL_AGENT_ROLES: list[str] = [
    "StoryArchitect",
    "ChapterPlanner",
    "Drafter",
    "Critic",
    "ContinuityGuard",
    "Rewriter",
    "ChiefEditor",
    "MemoryKeeper",
]


# ---------------------------------------------------------------------------
# 请求 / 响应模型
# ---------------------------------------------------------------------------
class CockpitData(BaseModel):
    """对齐前端 CockpitData 类型。"""

    active_session: Optional[dict[str, Any]] = None
    agent_statuses: list[dict[str, Any]] = []
    recent_runs: list[dict[str, Any]] = []
    review_queue_count: int = 0
    current_chapter: Optional[dict[str, Any]] = None


class CommandRequest(BaseModel):
    """前端只传 ``{ command: str }``。"""

    command: str
    args: Optional[dict[str, Any]] = None
    target_chapter: Optional[int] = None


class TakeoverRequest(BaseModel):
    """takeover 路由接受空 body 或可选 body。"""

    reason: str = ""
    action: str = "pause"


class ManuscriptRequest(BaseModel):
    content: str = ""
    base_version_number: Optional[int] = None
    submit_for_review: bool = True
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------
def _map_chapter_status(status: Optional[str]) -> str:
    """将后端章节状态映射为前端 ChapterStatus。"""
    mapping = {
        "planned": "planned",
        "draft": "draft",
        "generating": "in_progress",
        "review": "in_progress",
        "in_progress": "in_progress",
        "approved": "finalized",
        "published": "finalized",
        "finalized": "finalized",
    }
    if not status:
        return "draft"
    return mapping.get(status, "draft")


def _map_run_status(status: Optional[str]) -> str:
    """将后端 AgentRun 状态映射为前端 AgentRun.status。"""
    mapping = {
        "pending": "pending",
        "running": "running",
        "success": "completed",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "failed",
    }
    if not status:
        return "pending"
    return mapping.get(status, "pending")


def serialize_chapter(ch: Chapter) -> dict[str, Any]:
    """将 Chapter ORM 对象序列化为前端 Chapter 结构。"""
    return {
        "id": str(ch.id),
        "project_id": str(ch.project_id),
        "volume_id": None,
        "beat_id": None,
        "chapter_number": ch.chapter_no,
        "title": ch.title,
        "status": _map_chapter_status(ch.status),
        "summary": None,
        "word_count": ch.word_count,
        "target_words": ch.target_words,
        "created_at": ch.created_at.isoformat() if ch.created_at else None,
        "updated_at": ch.updated_at.isoformat() if ch.updated_at else None,
    }


def serialize_chapter_version(v: ChapterVersion) -> dict[str, Any]:
    """将 ChapterVersion ORM 对象序列化为前端 ChapterVersion 结构。

    字段映射：``version_no`` → ``version_number``，``created_by_agent`` → ``created_by``。
    """
    return {
        "id": str(v.id),
        "chapter_id": str(v.chapter_id),
        "version_number": v.version_no,
        "content": v.content,
        "word_count": v.word_count,
        "created_by": v.created_by_agent,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def serialize_agent_run(r: AgentRun) -> dict[str, Any]:
    """将 AgentRun ORM 对象序列化为前端 AgentRun 结构。

    字段映射：``agent_name`` → ``agent_role``，``status`` 做值映射，
    ``tokens_used`` = ``input_tokens + output_tokens``。
    """
    return {
        "id": str(r.id),
        "project_id": str(r.project_id),
        "agent_role": r.agent_name,
        "chapter_id": None,
        "status": _map_run_status(r.status),
        "autonomy_level": None,
        "input": None,
        "output": None,
        "tokens_used": (r.input_tokens or 0) + (r.output_tokens or 0),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "error": r.error,
    }


def serialize_session(s: WorkSession) -> dict[str, Any]:
    """将 WorkSession ORM 对象序列化为前端 WorkSession 结构。

    既包含前端 WorkSession 类型要求的字段（id/project_id/started_at/ended_at/
    chapters_worked/words_written/agent_runs），也保留后端有用的扩展字段
    （title/status/mode/progress_percent/current_score/quality_passed/risk_level）。
    """
    return {
        "id": str(s.id),
        "project_id": str(s.project_id),
        "started_at": s.created_at.isoformat() if s.created_at else None,
        "ended_at": s.updated_at.isoformat() if s.updated_at else None,
        "chapters_worked": [],
        "words_written": 0,
        "agent_runs": [],
        # 扩展字段（供 UI 使用，前端类型未声明但不会破坏运行时）
        "title": s.title,
        "status": s.status,
        "mode": s.mode,
        "progress_percent": s.progress_percent,
        "current_score": s.current_score,
        "quality_passed": s.quality_passed,
        "risk_level": s.risk_level,
    }


def _build_agent_statuses(runs: list[AgentRun]) -> list[dict[str, Any]]:
    """从最近的 AgentRun 列表推导每个 Agent 的状态。

    对每个角色取最新一条 run：running → working，failed → error，其它 → idle。
    """
    latest_by_role: dict[str, AgentRun] = {}
    for r in runs:
        # runs 已按 created_at desc 排序，第一条即最新
        if r.agent_name not in latest_by_role:
            latest_by_role[r.agent_name] = r

    statuses: list[dict[str, Any]] = []
    for role in ALL_AGENT_ROLES:
        run = latest_by_role.get(role)
        if run is None:
            statuses.append(
                {
                    "agent_role": role,
                    "status": "idle",
                    "message": None,
                    "current_task": None,
                }
            )
            continue

        if run.status == "running":
            status = "working"
            message = "工作中"
        elif run.status == "failed":
            status = "error"
            message = run.error or "执行失败"
        else:
            status = "idle"
            message = None

        statuses.append(
            {
                "agent_role": role,
                "status": status,
                "message": message,
                "current_task": run.agent_name if run.status == "running" else None,
            }
        )
    return statuses


# ---------------------------------------------------------------------------
# Boss 指令处理（try/except 导入，降级到关键词匹配）
# ---------------------------------------------------------------------------
def _keyword_fallback(command: str) -> dict[str, Any]:
    """当 BossCommandProcessor 不可用时的简单关键词匹配降级。"""
    keyword_map = [
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
    ]
    intent = "unknown"
    for keyword, mapped in keyword_map:
        if keyword in command:
            intent = mapped
            break
    return {
        "ok": True,
        "intent": intent,
        "command": command,
        "message": f"已接收指令（关键词匹配: {intent}）",
    }


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("/{project_id}", response_model=CockpitData)
async def get_cockpit(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """聚合项目驾驶舱状态（对齐前端 CockpitData）。"""
    # 1. 最近 WorkSession
    session_stmt = (
        select(WorkSession)
        .where(WorkSession.project_id == project_id)
        .order_by(WorkSession.updated_at.desc())
        .limit(1)
    )
    session_result = await db.execute(session_stmt)
    session = session_result.scalar_one_or_none()
    active_session = serialize_session(session) if session else None

    # 2. 最近 AgentRun（20 条，用于推导 agent_statuses 与 recent_runs）
    run_stmt = (
        select(AgentRun)
        .where(AgentRun.project_id == project_id)
        .order_by(AgentRun.created_at.desc())
        .limit(20)
    )
    run_result = await db.execute(run_stmt)
    runs = list(run_result.scalars().all())

    agent_statuses = _build_agent_statuses(runs)
    recent_runs = [serialize_agent_run(r) for r in runs[:10]]

    # 3. 待审 ReviewQueueItem 数量
    from sqlalchemy import func

    review_count_stmt = (
        select(func.count())
        .select_from(ReviewQueueItem)
        .where(
            ReviewQueueItem.project_id == project_id,
            ReviewQueueItem.status == "pending",
        )
    )
    review_count_result = await db.execute(review_count_stmt)
    review_queue_count = review_count_result.scalar_one() or 0

    # 4. current_chapter：优先返回 in_progress/generating/review 状态的章节，
    #    否则返回最新章节
    chapter_stmt = (
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.chapter_no.desc())
    )
    chapter_result = await db.execute(chapter_stmt)
    chapters = list(chapter_result.scalars().all())

    current_chapter: Optional[dict[str, Any]] = None
    in_progress_statuses = {"in_progress", "generating", "review", "draft"}
    in_progress_chapter = next((c for c in chapters if c.status in in_progress_statuses), None)
    target_chapter = in_progress_chapter or (chapters[0] if chapters else None)
    if target_chapter is not None:
        current_chapter = serialize_chapter(target_chapter)

    return CockpitData(
        active_session=active_session,
        agent_statuses=agent_statuses,
        recent_runs=recent_runs,
        review_queue_count=review_queue_count,
        current_chapter=current_chapter,
    )


@router.get("/{project_id}/stream")
async def stream(
    project_id: uuid.UUID,
    request: Request,
):
    """推送真实持久化运行事件；heartbeat 携带 Worker 实际健康状态。"""

    async def event_generator():
        seen: set[str] = set()
        last_event_id = request.headers.get("last-event-id")
        if last_event_id:
            seen.add(last_event_id)
        tick = 0
        while True:
            if await request.is_disconnected():
                return
            async with async_session_factory() as event_db:
                result = await event_db.execute(
                    select(ContinuousRunEvent)
                    .where(ContinuousRunEvent.project_id == project_id)
                    .order_by(ContinuousRunEvent.created_at.desc())
                    .limit(100)
                )
                events = list(reversed(result.scalars().all()))
            for item in events:
                event_id = str(item.id)
                if event_id in seen:
                    continue
                seen.add(event_id)
                yield {
                    "id": event_id,
                    "event": item.event_type,
                    "data": json.dumps(
                        {
                            "id": event_id,
                            "run_id": str(item.run_id),
                            "project_id": str(project_id),
                            "severity": item.severity,
                            "chapter_no": item.chapter_no,
                            "message": item.message,
                            "data": item.data,
                            "created_at": item.created_at.isoformat(),
                        },
                        ensure_ascii=False,
                    ),
                }
            if tick % 10 == 0:
                status = await continuous_production_service.get_status(project_id)
                yield {
                    "event": "heartbeat",
                    "data": json.dumps(status, ensure_ascii=False),
                }
            tick += 1
            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


@router.post("/{project_id}/command")
async def post_command(
    project_id: uuid.UUID,
    payload: CommandRequest,
    db: AsyncSession = Depends(get_db),
):
    """下发 Boss 自然语言指令。

    优先使用 ``BossCommandProcessor``（中文命令理解），
    不可用时降级到简单的关键词匹配。
    """
    try:
        from app.pipeline.boss_command import BossCommandProcessor
    except ImportError as exc:
        logger.exception("Boss 指令执行器不可用")
        raise HTTPException(
            status_code=503,
            detail="指令执行器暂不可用，本次指令未执行。",
        ) from exc

    try:
        processor = BossCommandProcessor(db, project_id)
        result = await processor.process(payload.command)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Boss 指令执行失败，拒绝伪装为成功: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="指令执行失败，本次操作未生效。请查看运行事件后重试。",
        ) from exc

    # 完整透传执行结果；前端据此展示真实动作，不再只显示“已接收”。
    return {
        "ok": result.get("ok", False),
        "intent": result.get("intent"),
        "command": result.get("command", payload.command),
        "message": result.get("message", ""),
        "data": result.get("data", {}),
    }


@router.post("/{project_id}/takeover")
async def takeover(
    project_id: uuid.UUID,
    payload: Optional[TakeoverRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """暂停 Agent 并将控制权交还用户。

    接受空 body 或可选 body（``{ reason, action }``）。
    """
    reason = payload.reason if payload and payload.reason else ""
    action = payload.action if payload else "pause"

    if action == "stop":
        run_status = await continuous_production_service.stop(project_id)
    else:
        run_status = await continuous_production_service.pause(
            project_id,
            reason=f"用户接管: {reason}" if reason else "用户接管",
        )

    stmt = (
        select(WorkSession)
        .where(
            WorkSession.project_id == project_id,
            WorkSession.status.in_(["running", "planning", "paused"]),
        )
        .order_by(WorkSession.updated_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session:
        session.status = "paused"
        session.paused_reason = f"用户接管: {reason}" if reason else "用户接管"
        await db.flush()

    return {
        "ok": True,
        "project_id": str(project_id),
        "action": action,
        "message": "已接管，Agent 已暂停",
        "continuous_run": run_status,
    }


# ---------------------------------------------------------------------------
# 章节相关路由（前端 cockpitApi.listChapters / getChapter / getChapterVersion）
# ---------------------------------------------------------------------------
@router.get("/{project_id}/chapters")
async def list_chapters(
    project_id: uuid.UUID,
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """查询项目下所有章节。"""
    stmt = (
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_no.asc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Chapter.status == status)
    result = await db.execute(stmt)
    return [serialize_chapter(c) for c in result.scalars().all()]


@router.get("/{project_id}/chapters/{chapter_id}")
async def get_chapter(
    project_id: uuid.UUID,
    chapter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """查询单个章节。"""
    stmt = select(Chapter).where(
        Chapter.id == chapter_id,
        Chapter.project_id == project_id,
    )
    result = await db.execute(stmt)
    chapter = result.scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return serialize_chapter(chapter)


@router.get("/{project_id}/chapters/{chapter_id}/version")
async def get_chapter_version(
    project_id: uuid.UUID,
    chapter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """查询章节的最新版本（ChapterVersion 序列化）。

    字段映射：``version_no`` → ``version_number``，``created_by_agent`` → ``created_by``。
    """
    # 优先使用 chapter.current_version_id，否则取该章节最新版本
    ch_stmt = select(Chapter).where(
        Chapter.id == chapter_id,
        Chapter.project_id == project_id,
    )
    ch_result = await db.execute(ch_stmt)
    chapter = ch_result.scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    version: Optional[ChapterVersion] = None
    if chapter.current_version_id:
        version = await db.get(ChapterVersion, chapter.current_version_id)

    if version is None:
        v_stmt = (
            select(ChapterVersion)
            .where(ChapterVersion.chapter_id == chapter_id)
            .order_by(ChapterVersion.version_no.desc())
            .limit(1)
        )
        v_result = await db.execute(v_stmt)
        version = v_result.scalar_one_or_none()

    if version is None:
        # 没有版本时返回占位响应，避免前端 404
        return {
            "id": None,
            "chapter_id": str(chapter_id),
            "version_number": 0,
            "content": "",
            "word_count": 0,
            "created_by": None,
            "created_at": None,
        }

    return serialize_chapter_version(version)


@router.post("/{project_id}/chapters/{chapter_id}/manuscript")
async def save_manuscript(
    project_id: uuid.UUID,
    chapter_id: uuid.UUID,
    payload: ManuscriptRequest,
    db: AsyncSession = Depends(get_db),
):
    """Save an optimistic, immutable human revision and queue real re-review."""
    continuous = await continuous_production_service.get_status(project_id)
    if continuous.get("desired_state") == "running":
        raise HTTPException(
            status_code=409,
            detail="24 小时自动写作正在运行；请先接管或暂停后再编辑正文",
        )
    ch_stmt = select(Chapter).where(
        Chapter.id == chapter_id,
        Chapter.project_id == project_id,
    )
    ch_result = await db.execute(ch_stmt)
    chapter = ch_result.scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    # 计算新版本号
    v_stmt = (
        select(ChapterVersion)
        .where(ChapterVersion.chapter_id == chapter_id)
        .order_by(ChapterVersion.version_no.desc())
        .limit(1)
    )
    v_result = await db.execute(v_stmt)
    latest_version = v_result.scalar_one_or_none()
    if (
        payload.base_version_number is not None
        and (latest_version.version_no if latest_version else 0) != payload.base_version_number
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "chapter_version_conflict",
                "message": "正文已有更新，请重新载入最新版本后再保存",
                "expected_version": payload.base_version_number,
                "current_version": latest_version.version_no if latest_version else 0,
            },
        )
    next_version_no = (latest_version.version_no + 1) if latest_version else 1

    word_count = len(payload.content)
    new_version = ChapterVersion(
        chapter_id=chapter_id,
        version_no=next_version_no,
        content=payload.content,
        word_count=word_count,
        status="draft",
        created_by_agent="user",
    )
    db.add(new_version)
    await db.flush()

    # Keep the working manuscript in sync with the immutable version snapshot.
    existing_blocks = list(
        (
            await db.scalars(
                select(ManuscriptBlock).where(ManuscriptBlock.chapter_id == chapter_id)
            )
        ).all()
    )
    for block in existing_blocks:
        await db.delete(block)
    await db.flush()
    parts = [part.strip() for part in payload.content.split("\n\n") if part.strip()]
    for block_no, part in enumerate(parts or [payload.content], start=1):
        db.add(
            ManuscriptBlock(
                chapter_id=chapter_id,
                block_no=block_no,
                content=part,
                block_type="paragraph",
            )
        )

    # 更新 chapter 当前版本与字数
    chapter.current_version_id = new_version.id
    chapter.word_count = word_count
    chapter.status = "review" if payload.submit_for_review else "draft"
    await db.flush()
    feedback = await QualityLedger(db, project_id).record_feedback(
        idempotency_key=f"manual-edit:{new_version.id}",
        action="edit",
        chapter_id=chapter.id,
        version_id=new_version.id,
        original_text=latest_version.content if latest_version else None,
        edited_text=payload.content,
        instruction=payload.notes,
        extra={
            "base_version_number": payload.base_version_number,
            "submit_for_review": payload.submit_for_review,
        },
    )
    review_item: Optional[ReviewQueueItem] = None
    if payload.submit_for_review:
        review_item = await db.scalar(
            select(ReviewQueueItem).where(
                ReviewQueueItem.project_id == project_id,
                ReviewQueueItem.artifact_type == "chapter",
                ReviewQueueItem.artifact_id == chapter.id,
                ReviewQueueItem.status == "pending",
            )
        )
        if review_item is None:
            review_item = ReviewQueueItem(
                project_id=project_id,
                item_type="manual_edit_review",
                artifact_type="chapter",
                artifact_id=chapter.id,
                chapter_no=chapter.chapter_no,
                title=f"第 {chapter.chapter_no} 章人工修订待重新质检",
                description=(
                    "人工接管后的新版本已保存，请运行修改重审以执行 "
                    "Critic、连续性检查与终审。"
                ),
                risk_level="medium",
                status="pending",
            )
            db.add(review_item)
            await db.flush()

    response = serialize_chapter_version(new_version)
    response.update(
        {
            "feedback_id": str(feedback.id),
            "review_item_id": str(review_item.id) if review_item else None,
            "submitted_for_review": payload.submit_for_review,
        }
    )
    return response


# ---------------------------------------------------------------------------
# 运行记录路由（前端 agentRunApi.list）
# ---------------------------------------------------------------------------
@router.get("/{project_id}/runs")
async def list_runs(
    project_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """查询项目最近的 AgentRun 记录（序列化为前端 AgentRun 结构）。"""
    stmt = (
        select(AgentRun)
        .where(AgentRun.project_id == project_id)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [serialize_agent_run(r) for r in result.scalars().all()]
