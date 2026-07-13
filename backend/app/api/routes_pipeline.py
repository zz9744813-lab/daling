"""Pipeline 路由 - 生成世界观 / 大纲 / 运行 / 恢复会话。"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.production_guard import manual_production_guard
from app.db import get_db
from app.db.models.chapter import Chapter, ChapterVersion, ManuscriptBlock
from app.db.models.project import Project
from app.db.models.quality import QualityAssessment, QualityIssue, RevisionAttempt
from app.db.models.session import WorkSession
from app.db.models.storyline import StorylineBeat, StorylineVolume
from app.db.models.world import WorldBible
from app.model_gateway import gateway
from app.pipeline.orchestrator import PipelineOrchestrator
from app.services.continuous_production import continuous_production_service
from app.services.preparation_state import (
    artifact_provenance_state,
    artifact_stale_state,
    mark_artifact_fresh,
    outline_source,
)

logger = logging.getLogger("app.api.routes_pipeline")

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


class GenerateBibleRequest(BaseModel):
    hints: Optional[dict[str, Any]] = None
    force: bool = False


class GenerateOutlineRequest(BaseModel):
    volume_count: int = Field(default=1, ge=1)
    chapters_per_volume: int = Field(default=10, ge=1)
    hints: Optional[dict[str, Any]] = None
    replace_existing: bool = False
    expected_revision: Optional[int] = Field(default=None, ge=1)


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


class PreparationStatus(BaseModel):
    project_id: str
    world_bible_ready: bool
    outline_ready: bool
    world_bible_exists: bool
    outline_exists: bool
    world_bible_stale: bool = False
    outline_stale: bool = False
    preparation_stale: bool = False
    stale_reasons: list[dict[str, Any]] = Field(default_factory=list)
    requires_regeneration: list[str] = Field(default_factory=list)
    outline_source_present: bool = False
    outline_source_revision: int = 0
    outline_source_sha256: Optional[str] = None
    chapter_count: int = 0
    volume_count: int = 0


async def _outline_structure_state(
    project: Project,
    db: AsyncSession,
) -> dict[str, Any]:
    """Return the revision and destructive-replacement safety state."""
    volume_count = int(
        await db.scalar(
            select(func.count(StorylineVolume.id)).where(
                StorylineVolume.project_id == project.id
            )
        )
        or 0
    )
    beat_count = int(
        await db.scalar(
            select(func.count(StorylineBeat.id)).where(
                StorylineBeat.project_id == project.id
            )
        )
        or 0
    )
    chapters = list(
        (
            await db.scalars(
                select(Chapter)
                .where(Chapter.project_id == project.id)
                .order_by(Chapter.chapter_no.asc())
            )
        ).all()
    )
    chapter_ids = [chapter.id for chapter in chapters]
    versioned_ids: set[uuid.UUID] = set()
    blocked_ids: set[uuid.UUID] = set()
    if chapter_ids:
        versioned_ids = set(
            (
                await db.scalars(
                    select(ChapterVersion.chapter_id)
                    .where(ChapterVersion.chapter_id.in_(chapter_ids))
                    .distinct()
                )
            ).all()
        )
        blocked_ids = set(
            (
                await db.scalars(
                    select(ManuscriptBlock.chapter_id)
                    .where(ManuscriptBlock.chapter_id.in_(chapter_ids))
                    .distinct()
                )
            ).all()
        )

    locked_chapters = [
        chapter.chapter_no
        for chapter in chapters
        if (chapter.word_count or 0) > 0
        or chapter.current_version_id is not None
        or chapter.status not in {"planned", "draft"}
        or chapter.id in versioned_ids
        or chapter.id in blocked_ids
    ]
    exists = bool(volume_count or beat_count or chapters)
    provenance = artifact_provenance_state(
        dict(project.extra or {}),
        "outline",
        exists=exists,
    )
    return {
        "exists": exists,
        "volume_count": volume_count,
        "beat_count": beat_count,
        "chapter_count": len(chapters),
        "locked_chapters": locked_chapters,
        "structure_revision": int(provenance["artifact_revision"]),
    }


@router.get("/{project_id}/preparation-status", response_model=PreparationStatus)
async def preparation_status(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return persisted preparation progress so onboarding survives reloads."""
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    bible_count = int(
        await db.scalar(
            select(func.count(WorldBible.id)).where(WorldBible.project_id == project_id)
        )
        or 0
    )
    volume_count = int(
        await db.scalar(
            select(func.count(StorylineVolume.id)).where(
                StorylineVolume.project_id == project_id
            )
        )
        or 0
    )
    chapter_count = int(
        await db.scalar(select(func.count(Chapter.id)).where(Chapter.project_id == project_id))
        or 0
    )
    world_bible_exists = bible_count > 0
    outline_exists = volume_count > 0 or chapter_count > 0
    extra = dict(project.extra or {})
    bible_stale = artifact_stale_state(
        extra,
        "world_bible",
        exists=world_bible_exists,
    )
    outline_stale = artifact_stale_state(extra, "outline", exists=outline_exists)
    stale_reasons = [
        {"artifact": artifact, **state}
        for artifact, state in (
            ("world_bible", bible_stale),
            ("outline", outline_stale),
        )
        if state.get("stale")
    ]
    source = outline_source(extra)
    return PreparationStatus(
        project_id=str(project_id),
        world_bible_ready=world_bible_exists and not bible_stale["stale"],
        outline_ready=outline_exists and not outline_stale["stale"],
        world_bible_exists=world_bible_exists,
        outline_exists=outline_exists,
        world_bible_stale=bool(bible_stale["stale"]),
        outline_stale=bool(outline_stale["stale"]),
        preparation_stale=bool(stale_reasons),
        stale_reasons=stale_reasons,
        requires_regeneration=[item["artifact"] for item in stale_reasons],
        outline_source_present=bool(source["present"]),
        outline_source_revision=int(source["revision"]),
        outline_source_sha256=source["sha256"],
        chapter_count=chapter_count,
        volume_count=volume_count,
    )


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
    _manual_guard: None = Depends(manual_production_guard),
):
    """生成世界观圣经。

    如果项目上传了详细大纲（extra["outline_text"]），
    会自动读取并作为参考传给 StoryArchitect。
    """
    # 读取项目的大纲文本
    proj_stmt = select(Project).where(Project.id == project_id)
    proj_result = await db.execute(proj_stmt)
    project = proj_result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    hints = dict(payload.hints or {})
    project_extra = project.extra or {}

    # 新建项目工作区会保存完整创作蓝图。世界观生成必须消费这些已确认
    # 的决定，不能只看到一句 creative_prompt 后重新猜测一遍。
    blueprint = project_extra.get("creation_blueprint")
    if not isinstance(blueprint, dict):
        blueprint = {}
    hints.setdefault("title", project.title)
    if project.genre:
        hints.setdefault("genre", project.genre)
    if project.synopsis:
        hints.setdefault("synopsis", project.synopsis)
    for key in (
        "logline",
        "premise",
        "protagonist",
        "protagonist_goal",
        "flaw",
        "fear",
        "core_conflict",
        "story_question",
        "antagonist",
        "ability",
        "ability_cost",
        "setting",
        "world_setting",
        "world_rules",
        "themes",
        "tone",
        "pacing",
        "audience",
        "platform",
        "language",
        "pov",
        "tense",
        "ending_preference",
        "content_boundaries",
        "target_words",
        "target_chapters",
        "words_per_chapter",
        "volume_count",
    ):
        value = blueprint.get(key, project_extra.get(key))
        if value not in (None, "", [], {}):
            hints.setdefault(key, value)

    outline_text = project_extra.get("outline_text")
    if outline_text:
        hints["outline_text"] = outline_text
        logger.info("项目 %s 使用上传的大纲生成世界观 (%d 字符)", project_id, len(outline_text))

    # 从 project.extra 中读取创作灵感（从零开始模式时用户填写的）
    creative_prompt = project_extra.get("creative_prompt")
    if creative_prompt:
        hints["creative_prompt"] = creative_prompt
        logger.info("项目 %s 使用创作灵感生成世界观 (%d 字符)", project_id, len(creative_prompt))

    session = await _create_work_session(
        db,
        project_id,
        title="生成世界观圣经",
        goal="根据用户提示生成世界观圣经",
        session_type="generate_bible",
    )
    orchestrator = _make_orchestrator(db, project_id, session.id)
    try:
        result = await orchestrator.generate_bible(hints)
        project.extra = mark_artifact_fresh(dict(project.extra or {}), "world_bible")
        flag_modified(project, "extra")
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
    _manual_guard: None = Depends(manual_production_guard),
):
    """Generate a new structure or atomically replace an unstarted one."""
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    structure = await _outline_structure_state(project, db)
    replacing = bool(payload.replace_existing and structure["exists"])
    if structure["exists"] and not payload.replace_existing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "storyline_exists",
                "message": "项目已有卷章结构；如需重建，请明确选择安全替换。",
                "actual_revision": structure["structure_revision"],
                "can_replace": not bool(structure["locked_chapters"]),
                "locked_chapters": structure["locked_chapters"],
            },
        )
    if replacing and payload.expected_revision is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "storyline_revision_required",
                "message": "安全替换必须提交当前结构 revision。",
                "actual_revision": structure["structure_revision"],
            },
        )
    if payload.expected_revision is not None and (
        payload.expected_revision != structure["structure_revision"]
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "storyline_revision_conflict",
                "message": "故事线已被其他操作更新，请刷新后再重建。",
                "expected_revision": payload.expected_revision,
                "actual_revision": structure["structure_revision"],
            },
        )
    if replacing and structure["locked_chapters"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "storyline_replace_locked",
                "message": "已有章节进入写作或审校，不能替换卷章结构。",
                "locked_chapters": structure["locked_chapters"],
            },
        )

    session = await _create_work_session(
        db,
        project_id,
        title="重建故事大纲" if replacing else "生成故事大纲",
        goal=(
            f"{'安全替换' if replacing else '生成'} {payload.volume_count} 卷大纲，"
            f"每卷 {payload.chapters_per_volume} 章"
        ),
        session_type="generate_outline",
    )
    orchestrator = _make_orchestrator(db, project_id, session.id)
    try:
        result = await orchestrator.generate_outline(
            volume_count=payload.volume_count,
            chapters_per_volume=payload.chapters_per_volume,
            hints=payload.hints,
            replace_existing=replacing,
        )
        if result.get("status") == "failed":
            session.status = "failed"
            session.paused_reason = str(result.get("error", "大纲生成失败"))
            await db.commit()
            raise HTTPException(status_code=409, detail=result.get("error", "大纲生成失败"))

        project.extra = mark_artifact_fresh(dict(project.extra or {}), "outline")
        if replacing:
            project.current_chapter_no = 0
        flag_modified(project, "extra")
        provenance = artifact_provenance_state(
            dict(project.extra or {}),
            "outline",
            exists=True,
        )
        result.update(
            {
                "replaced": replacing,
                "previous_structure_revision": structure["structure_revision"],
                "structure_revision": int(provenance["artifact_revision"]),
                "source_revision": provenance.get("source_revision"),
            }
        )
        session.status = "completed"
        session.progress_percent = 100.0
        session.current_artifact_type = "outline"
        session.next_action = {
            "action": "review_storyline",
            "structure_revision": int(provenance["artifact_revision"]),
        }
        await db.commit()
        return PipelineResult(
            project_id=str(project_id),
            job="generate_outline",
            message=f"大纲已{'安全替换' if replacing else '生成'}: "
            f"{result.get('volume_count', 0)} 卷, "
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
    _manual_guard: None = Depends(manual_production_guard),
):
    """运行章节生成 Pipeline。"""
    session = await _create_work_session(
        db,
        project_id,
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


def _serialize_datetime(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


@router.get("/{project_id}/chapters/{chapter_no}/quality")
async def chapter_quality_detail(
    project_id: uuid.UUID,
    chapter_no: int,
    db: AsyncSession = Depends(get_db),
):
    """Return the complete read-only quality and revision evidence for a chapter."""
    chapter = await db.scalar(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_no == chapter_no,
        )
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="Chapter not found")

    versions = list(
        (
            await db.scalars(
                select(ChapterVersion)
                .where(ChapterVersion.chapter_id == chapter.id)
                .order_by(ChapterVersion.version_no.asc())
            )
        ).all()
    )
    assessments = list(
        (
            await db.scalars(
                select(QualityAssessment)
                .where(
                    QualityAssessment.project_id == project_id,
                    QualityAssessment.chapter_id == chapter.id,
                )
                .order_by(
                    QualityAssessment.round_no.asc(),
                    QualityAssessment.created_at.asc(),
                )
            )
        ).all()
    )
    issues = list(
        (
            await db.scalars(
                select(QualityIssue)
                .where(
                    QualityIssue.project_id == project_id,
                    QualityIssue.chapter_id == chapter.id,
                )
                .order_by(QualityIssue.created_at.asc())
            )
        ).all()
    )
    revisions = list(
        (
            await db.scalars(
                select(RevisionAttempt)
                .where(
                    RevisionAttempt.project_id == project_id,
                    RevisionAttempt.chapter_id == chapter.id,
                )
                .order_by(
                    RevisionAttempt.round_no.asc(),
                    RevisionAttempt.created_at.asc(),
                )
            )
        ).all()
    )

    issues_by_assessment: dict[uuid.UUID, list[str]] = {}
    for issue in issues:
        issues_by_assessment.setdefault(issue.assessment_id, []).append(str(issue.id))
    latest_gate = next(
        (
            assessment
            for assessment in reversed(assessments)
            if assessment.assessment_type == "deterministic_gate"
        ),
        assessments[-1] if assessments else None,
    )

    return {
        "project_id": str(project_id),
        "chapter": {
            "id": str(chapter.id),
            "chapter_no": chapter.chapter_no,
            "title": chapter.title,
            "status": chapter.status,
            "word_count": chapter.word_count,
            "current_version_id": (
                str(chapter.current_version_id) if chapter.current_version_id else None
            ),
        },
        "summary": {
            "assessment_count": len(assessments),
            "issue_count": len(issues),
            "open_issue_count": sum(
                issue.status == "open" and issue.version_id == chapter.current_version_id
                for issue in issues
            ),
            "revision_attempt_count": len(revisions),
            "latest_score": latest_gate.overall_score if latest_gate else None,
            "latest_verdict": latest_gate.verdict if latest_gate else None,
            "quality_passed": latest_gate.passed if latest_gate else None,
        },
        "version_refs": [
            {
                "id": str(version.id),
                "version_no": version.version_no,
                "status": version.status,
                "word_count": version.word_count,
                "created_by_agent": version.created_by_agent,
                "created_at": _serialize_datetime(version.created_at),
                "is_current": version.id == chapter.current_version_id,
            }
            for version in versions
        ],
        "assessments": [
            {
                "id": str(assessment.id),
                "session_id": str(assessment.session_id) if assessment.session_id else None,
                "version_id": str(assessment.version_id) if assessment.version_id else None,
                "agent_run_id": (
                    str(assessment.agent_run_id) if assessment.agent_run_id else None
                ),
                "assessor": assessment.assessor,
                "assessment_type": assessment.assessment_type,
                "round_no": assessment.round_no,
                "rubric_version": assessment.rubric_version,
                "model_name": assessment.model_name,
                "dimension_scores": assessment.dimension_scores,
                "overall_score": assessment.overall_score,
                "verdict": assessment.verdict,
                "passed": assessment.passed,
                "issue_ids": issues_by_assessment.get(assessment.id, []),
                "raw_result": assessment.raw_result,
                "created_at": _serialize_datetime(assessment.created_at),
            }
            for assessment in assessments
        ],
        "issues": [
            {
                "id": str(issue.id),
                "assessment_id": str(issue.assessment_id),
                "version_id": str(issue.version_id) if issue.version_id else None,
                "block_id": str(issue.block_id) if issue.block_id else None,
                "issue_fingerprint": issue.issue_fingerprint,
                "source": issue.source,
                "category": issue.category,
                "severity": issue.severity,
                "block_no": issue.block_no,
                "location": issue.location,
                "quoted_text": issue.quoted_text,
                "description": issue.description,
                "expected": issue.expected,
                "actual": issue.actual,
                "suggestion": issue.suggestion,
                "status": issue.status,
                "resolved_by_revision_id": (
                    str(issue.resolved_by_revision_id)
                    if issue.resolved_by_revision_id
                    else None
                ),
                "extra": issue.extra,
                "created_at": _serialize_datetime(issue.created_at),
            }
            for issue in issues
        ],
        "revision_attempts": [
            {
                "id": str(revision.id),
                "session_id": str(revision.session_id) if revision.session_id else None,
                "input_version_id": (
                    str(revision.input_version_id) if revision.input_version_id else None
                ),
                "output_version_id": (
                    str(revision.output_version_id) if revision.output_version_id else None
                ),
                "round_no": revision.round_no,
                "status": revision.status,
                "instruction_source": revision.instruction_source,
                "instruction": revision.instruction,
                "trigger_issue_ids": revision.trigger_issue_ids,
                "score_before": revision.score_before,
                "score_after": revision.score_after,
                "diff_summary": revision.diff_summary,
                "error": revision.error,
                "extra": revision.extra,
                "created_at": _serialize_datetime(revision.created_at),
            }
            for revision in revisions
        ],
    }


@router.post("/{project_id}/resume-session", response_model=PipelineResult)
async def resume_session(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """恢复最近的 WorkSession。"""
    # 持久化连续任务优先：恢复必须真正启动 Worker，不能只改一列状态。
    continuous = await continuous_production_service.get_status(project_id)
    if continuous.get("run_id") and continuous.get("desired_state") == "paused":
        resumed = await continuous_production_service.resume(project_id)
        return PipelineResult(
            project_id=str(project_id),
            job="resume_session",
            message="24 小时自动写作已恢复",
            result=resumed,
        )

    # 查找最近的普通（非连续）可恢复会话
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
