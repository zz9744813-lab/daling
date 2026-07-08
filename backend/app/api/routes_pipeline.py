"""Pipeline 路由 - 生成世界观 / 大纲 / 运行 / 恢复会话。"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.models.session import WorkSession
from app.model_gateway import gateway
from app.pipeline.orchestrator import PipelineOrchestrator

logger = logging.getLogger("app.api.routes_pipeline")

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


class GenerateBibleRequest(BaseModel):
    hints: Optional[dict[str, Any]] = None
    force: bool = False


class GenerateOutlineRequest(BaseModel):
    volume_count: int = Field(default=1, ge=1)
    chapters_per_volume: int = Field(default=10, ge=1)
    hints: Optional[dict[str, Any]] = None


class RunRequest(BaseModel):
    target_chapters: int = Field(default=1, ge=1)
    mode: str = "L2"
    resume: bool = False
    start_chapter: Optional[int] = None


class PipelineResult(BaseModel):
    ok: bool = True
    project_id: str
    job: str
    message: str = ""
    result: dict[str, Any] = Field(default_factory=dict)


def _make_orchestrator(
    db: AsyncSession, project_id: uuid.UUID, session_id: Optional[uuid.UUID] = None
) -> PipelineOrchestrator:
    """创建 PipelineOrchestrator 实例。"""
    return PipelineOrchestrator(
        gateway=gateway,
        db=db,
        project_id=project_id,
        session_id=session_id,
    )


async def _create_work_session(
    db: AsyncSession,
    project_id: uuid.UUID,
    title: str,
    goal: str,
    mode: str = "L2",
    session_type: str = "advance_chapters",
) -> WorkSession:
    """创建 WorkSession 记录。"""
    session = WorkSession(
        project_id=project_id,
        title=title,
        goal=goal,
        mode=mode,
        status="running",
        session_type=session_type,
        quality_threshold=80,
    )
    db.add(session)
    await db.flush()
    return session


@router.post("/{project_id}/generate-bible", response_model=PipelineResult)
async def generate_bible(
    project_id: uuid.UUID,
    payload: GenerateBibleRequest,
    db: AsyncSession = Depends(get_db),
):
    """生成世界观圣经。

    如果项目上传了详细大纲（extra["outline_text"]），
    会自动读取并作为参考传给 StoryArchitect。
    """
    # 读取项目的大纲文本
    from app.db.models.project import Project

    proj_stmt = select(Project).where(Project.id == project_id)
    proj_result = await db.execute(proj_stmt)
    project = proj_result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    hints = payload.hints or {}
    outline_text = (project.extra or {}).get("outline_text")
    if outline_text:
        hints["outline_text"] = outline_text
        logger.info("项目 %s 使用上传的大纲生成世界观 (%d 字符)", project_id, len(outline_text))

    session = await _create_work_session(
        db, project_id,
        title="生成世界观圣经",
        goal="根据用户提示生成世界观圣经",
        session_type="generate_bible",
    )
    orchestrator = _make_orchestrator(db, project_id, session.id)
    try:
        result = await orchestrator.generate_bible(hints)
        await db.commit()
        return PipelineResult(
            project_id=str(project_id),
            job="generate_bible",
            message=f"世界观圣经已生成: {result.get('world_name', '')}",
            result=result,
        )
    except Exception as exc:
        session.status = "failed"
        await db.flush()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{project_id}/generate-outline", response_model=PipelineResult)
async def generate_outline(
    project_id: uuid.UUID,
    payload: GenerateOutlineRequest,
    db: AsyncSession = Depends(get_db),
):
    """生成故事大纲。"""
    session = await _create_work_session(
        db, project_id,
        title="生成故事大纲",
        goal=f"生成 {payload.volume_count} 卷大纲，每卷 {payload.chapters_per_volume} 章",
        session_type="generate_outline",
    )
    orchestrator = _make_orchestrator(db, project_id, session.id)
    try:
        result = await orchestrator.generate_outline(
            volume_count=payload.volume_count,
            chapters_per_volume=payload.chapters_per_volume,
            hints=payload.hints,
        )
        await db.commit()
        if result.get("status") == "failed":
            session.status = "failed"
            await db.flush()
            raise HTTPException(status_code=409, detail=result.get("error", "大纲生成失败"))
        return PipelineResult(
            project_id=str(project_id),
            job="generate_outline",
            message=f"大纲已生成: {result.get('volume_count', 0)} 卷, "
            f"{result.get('total_chapters', 0)} 章",
            result=result,
        )
    except HTTPException:
        raise
    except Exception as exc:
        session.status = "failed"
        await db.flush()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{project_id}/run", response_model=PipelineResult)
async def run_pipeline(
    project_id: uuid.UUID,
    payload: RunRequest,
    db: AsyncSession = Depends(get_db),
):
    """运行章节生成 Pipeline。"""
    session = await _create_work_session(
        db, project_id,
        title=f"生成 {payload.target_chapters} 章正文",
        goal=f"连续生成 {payload.target_chapters} 章 (mode={payload.mode})",
        mode=payload.mode,
        session_type="advance_chapters",
    )
    orchestrator = _make_orchestrator(db, project_id, session.id)
    try:
        result = await orchestrator.run_pipeline(
            target_chapters=payload.target_chapters,
            mode=payload.mode,
            start_chapter=payload.start_chapter,
        )
        return PipelineResult(
            project_id=str(project_id),
            job="run",
            message=f"Pipeline 完成: 成功 {result.get('success_count', 0)} 章, "
            f"失败 {result.get('failed_count', 0)} 章",
            result=result,
        )
    except Exception as exc:
        session.status = "failed"
        await db.flush()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{project_id}/resume-session", response_model=PipelineResult)
async def resume_session(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """恢复最近的 WorkSession。"""
    # 查找最近的可恢复会话
    stmt = (
        select(WorkSession)
        .where(
            WorkSession.project_id == project_id,
            WorkSession.status.in_(["paused", "running", "planning"]),
        )
        .order_by(WorkSession.updated_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        return PipelineResult(
            ok=False,
            project_id=str(project_id),
            job="resume_session",
            message="无可恢复的会话",
        )

    # 恢复会话
    session.status = "running"
    session.paused_reason = None
    await db.flush()

    return PipelineResult(
        project_id=str(project_id),
        job="resume_session",
        message=f"会话已恢复: {session.title}",
        result={
            "session_id": str(session.id),
            "title": session.title,
            "mode": session.mode,
            "progress_percent": session.progress_percent,
        },
    )
