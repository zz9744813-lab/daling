"""演进路由 - 进化系统 (Phase 4)。

提供在线学习与自改进的全部端点：
- GET  /{project_id}                     — 聚合进化数据（prompt 实验、技能测试、学习报告摘要）
- POST /{project_id}/prompt-experiment   — 运行 prompt A/B 测试
- POST /{project_id}/skill-test          — 运行技能测试
- GET  /{project_id}/learning-report     — 获取学习报告
- POST /{project_id}/reflection          — 创建反思
- GET  /{project_id}/reflections         — 列出反思记录
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context.book_memory_manager import memory_governance, visible_memory_value
from app.db import get_db
from app.db.models.memory import BookMemory
from app.db.models.quality import HumanFeedbackEvent, LearningCycle, PromptVersion
from app.learning import LearningLab, PromptLab, SkillLab
from app.services.prompt_evolution import PromptEvolutionService

logger = logging.getLogger("app.api.evolution")

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


# ---------------------------------------------------------------------------
# 响应 / 请求模型
# ---------------------------------------------------------------------------
class EvolutionView(BaseModel):
    project_id: str
    prompt_experiments: list[dict[str, Any]] = []
    skill_tests: list[dict[str, Any]] = []
    reflections_count: int = 0
    latest_suggestions: list[str] = []
    quality_report: dict[str, Any] = {}
    learning_cycles: list[dict[str, Any]] = []
    prompt_versions: list[dict[str, Any]] = []
    recent_reflections: list[dict[str, Any]] = []
    memory_entries: list[dict[str, Any]] = []
    memory_count: int = 0
    memory_status_counts: dict[str, int] = {}
    pending_feedback_count: int = 0


class PromptExperimentRequest(BaseModel):
    prompt_a: str
    prompt_b: str
    test_input: str
    judge_prompt: Optional[str] = None


class SkillTestRequest(BaseModel):
    skill_name: str
    test_cases: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description="测试用例列表",
    )


class ReflectionRequest(BaseModel):
    reflection_type: str = Field(..., description="pre_chapter/post_chapter/session_end/volume_end")
    chapter_no: Optional[int] = None
    content: str = ""
    decisions: Optional[list[Any]] = None
    lessons_learned: Optional[list[Any]] = None
    session_id: Optional[str] = None


class LearningReportResponse(BaseModel):
    period: dict[str, Any] = {}
    avg_score_trend: list[dict[str, Any]] = []
    common_issues: list[dict[str, Any]] = []
    lessons_learned: list[dict[str, Any]] = []
    suggestions: list[str] = []


class HoldoutEvaluationRequest(BaseModel):
    """An explicit, potentially billable holdout execution request."""

    force: bool = Field(
        False,
        description="重新运行已经完成的固定 holdout；默认复用相同套件的既有证据",
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("/{project_id}", response_model=EvolutionView)
async def get_evolution(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """聚合进化数据：prompt 实验列表、技能测试列表、学习报告摘要。"""
    prompt_lab = PromptLab(db, project_id)
    skill_lab = SkillLab(db, project_id)
    learning_lab = LearningLab(db, project_id)

    # prompt 实验
    experiments = await prompt_lab.list_experiments(project_id, limit=10)

    # 技能测试
    skill_tests = await skill_lab.list_skill_tests(project_id, limit=10)

    # 反思数量
    reflections = await learning_lab.list_reflections(project_id, limit=1000)

    # 最新建议（从最近的学习报告中提取）
    report = await learning_lab.generate_report(project_id)
    suggestions = report.get("suggestions", [])
    cycles = (
        (
            await db.execute(
                select(LearningCycle)
                .where(LearningCycle.project_id == project_id)
                .order_by(LearningCycle.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    prompt_versions = (
        (
            await db.execute(
                select(PromptVersion)
                .where(PromptVersion.project_id == project_id)
                .order_by(PromptVersion.created_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    memory_count = await db.scalar(
        select(func.count(BookMemory.id)).where(BookMemory.project_id == project_id)
    )
    # Governance status currently lives alongside the memory payload so legacy
    # databases do not need a destructive migration.  Load the complete set to
    # compute truthful active/inactive totals, while keeping the response list
    # bounded below.
    all_memories = (
        (
            await db.execute(
                select(BookMemory)
                .where(BookMemory.project_id == project_id)
                .order_by(BookMemory.updated_at.desc(), BookMemory.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    memories = all_memories[:100]
    memory_status_counts: dict[str, int] = {
        "active": 0,
        "rejected": 0,
        "rolled_back": 0,
    }
    for memory in all_memories:
        status = memory_governance(memory)["status"]
        normalized_status = "active" if status in {"active", "approved"} else status
        memory_status_counts[normalized_status] = (
            memory_status_counts.get(normalized_status, 0) + 1
        )
    pending_feedback_count = await db.scalar(
        select(func.count(HumanFeedbackEvent.id)).where(
            HumanFeedbackEvent.project_id == project_id,
            HumanFeedbackEvent.learning_status == "pending",
        )
    )

    return EvolutionView(
        project_id=str(project_id),
        prompt_experiments=experiments,
        skill_tests=skill_tests,
        reflections_count=len(reflections),
        latest_suggestions=suggestions,
        quality_report=report,
        learning_cycles=[
            {
                "id": str(cycle.id),
                "status": cycle.status,
                "source_from": cycle.source_from.isoformat() if cycle.source_from else None,
                "source_to": cycle.source_to.isoformat() if cycle.source_to else None,
                "feedback_count": cycle.feedback_count,
                "assessment_count": cycle.assessment_count,
                "memory_count": len(cycle.candidate_memory_ids or []),
                "prompt_candidate_count": len(cycle.candidate_prompt_version_ids or []),
                "candidate_memory_ids": cycle.candidate_memory_ids or [],
                "candidate_prompt_version_ids": cycle.candidate_prompt_version_ids or [],
                "promotion_decision": cycle.promotion_decision,
                "holdout_metrics": cycle.holdout_metrics,
                "rollback_reason": cycle.rollback_reason,
                "error": cycle.error,
                "started_at": cycle.started_at.isoformat() if cycle.started_at else None,
                "completed_at": cycle.completed_at.isoformat() if cycle.completed_at else None,
                "created_at": cycle.created_at.isoformat() if cycle.created_at else None,
            }
            for cycle in cycles
        ],
        prompt_versions=[_prompt_version_out(version) for version in prompt_versions],
        recent_reflections=[
            {
                "id": str(reflection.id),
                "reflection_type": reflection.reflection_type,
                "chapter_no": reflection.chapter_no,
                "content": reflection.content,
                "decisions": reflection.decisions,
                "lessons_learned": reflection.lessons_learned,
                "created_at": reflection.created_at.isoformat() if reflection.created_at else None,
            }
            for reflection in reflections[:20]
        ],
        memory_entries=[
            {
                "id": str(memory.id),
                "project_id": str(memory.project_id),
                "memory_type": memory.memory_type,
                "key": memory.key,
                "value": visible_memory_value(memory),
                "source": memory.source,
                "confidence": memory.confidence,
                "status": memory_governance(memory)["status"],
                "governance": memory_governance(memory),
                "created_at": memory.created_at.isoformat() if memory.created_at else None,
                "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
            }
            for memory in memories
        ],
        memory_count=int(memory_count or 0),
        memory_status_counts=memory_status_counts,
        pending_feedback_count=int(pending_feedback_count or 0),
    )


def _prompt_version_out(version: PromptVersion) -> dict[str, Any]:
    metrics = version.evaluation_metrics or {}
    return {
        "id": str(version.id),
        "agent_role": version.agent_role,
        "version_no": version.version_no,
        "status": version.status,
        "template": version.template,
        "parent_version_id": str(version.parent_version_id) if version.parent_version_id else None,
        "learning_cycle_id": str(version.learning_cycle_id) if version.learning_cycle_id else None,
        "evaluation_metrics": metrics,
        "source": {
            "type": "autonomous_learning" if version.learning_cycle_id else "manual",
            "learning_cycle_id": (
                str(version.learning_cycle_id) if version.learning_cycle_id else None
            ),
            "evidence_count": metrics.get("evidence_count"),
            "baseline_champion_id": metrics.get("baseline_champion_id"),
        },
        "activated_at": version.activated_at.isoformat() if version.activated_at else None,
        "retired_at": version.retired_at.isoformat() if version.retired_at else None,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


@router.post("/{project_id}/prompt-versions/{version_id}/holdout")
@router.post(
    "/{project_id}/prompt-versions/{version_id}/evaluate-holdout",
    include_in_schema=False,
)
async def evaluate_prompt_version_holdout(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: Optional[HoldoutEvaluationRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Run real fixed-suite generation/judging only after an explicit POST."""
    service = PromptEvolutionService(db, project_id)
    try:
        metrics = await service.evaluate_holdout(
            version_id,
            force=bool(payload.force) if payload else False,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": metrics.get("holdout_status") in {"passed", "failed"},
        "version_id": str(version_id),
        "holdout_status": metrics.get("holdout_status"),
        "gate_passed": metrics.get("gate_passed", False),
        "metrics": metrics,
    }


@router.post("/{project_id}/prompt-versions/{version_id}/promote")
async def promote_prompt_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Promote only a candidate whose holdout explicitly passed all gates."""
    service = PromptEvolutionService(db, project_id)
    try:
        version, previous = await service.promote(version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "version": _prompt_version_out(version),
        "previous_champion_id": (
            str(previous.id) if previous is not None and previous.id != version.id else None
        ),
    }


@router.post("/{project_id}/prompt-versions/{version_id}/rollback")
async def rollback_prompt_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Roll back and atomically restore the preceding qualified champion."""
    service = PromptEvolutionService(db, project_id)
    try:
        version, restored = await service.rollback(version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "ok": True,
        "version": _prompt_version_out(version),
        "restored_champion": _prompt_version_out(restored) if restored else None,
    }


@router.post("/{project_id}/prompt-experiment")
async def run_prompt_experiment(
    project_id: uuid.UUID,
    payload: PromptExperimentRequest,
    db: AsyncSession = Depends(get_db),
):
    """运行 prompt A/B 测试。"""
    lab = PromptLab(db, project_id)
    result = await lab.run_experiment(
        project_id=project_id,
        prompt_a=payload.prompt_a,
        prompt_b=payload.prompt_b,
        test_input=payload.test_input,
        judge_prompt=payload.judge_prompt,
    )
    return result


@router.post("/{project_id}/skill-test")
async def run_skill_test(
    project_id: uuid.UUID,
    payload: SkillTestRequest,
    db: AsyncSession = Depends(get_db),
):
    """运行技能测试。"""
    lab = SkillLab(db, project_id)
    result = await lab.run_skill_test(
        project_id=project_id,
        skill_name=payload.skill_name,
        test_cases=payload.test_cases,
    )
    return result


@router.get("/{project_id}/learning-report", response_model=LearningReportResponse)
async def get_learning_report(
    project_id: uuid.UUID,
    start_chapter: Optional[int] = Query(None, description="起始章节号"),
    end_chapter: Optional[int] = Query(None, description="结束章节号"),
    enhance_with_llm: bool = Query(
        False,
        description="显式启用模型增强建议；默认关闭以避免只读页面产生模型费用",
    ),
    db: AsyncSession = Depends(get_db),
):
    """获取学习报告。

    可通过 start_chapter / end_chapter 指定章节范围。
    """
    lab = LearningLab(db, project_id)
    chapter_range = None
    if start_chapter is not None and end_chapter is not None:
        chapter_range = (start_chapter, end_chapter)

    report = await lab.generate_report(
        project_id=project_id,
        chapter_range=chapter_range,
        enhance_with_llm=enhance_with_llm,
    )
    return LearningReportResponse(**report)


@router.post("/{project_id}/reflection")
async def create_reflection(
    project_id: uuid.UUID,
    payload: ReflectionRequest,
    db: AsyncSession = Depends(get_db),
):
    """创建规划反思。"""
    lab = LearningLab(db, project_id)

    session_id = None
    if payload.session_id:
        session_id = uuid.UUID(payload.session_id)

    reflection = await lab.create_reflection(
        project_id=project_id,
        reflection_type=payload.reflection_type,
        chapter_no=payload.chapter_no,
        content=payload.content,
        decisions=payload.decisions,
        lessons_learned=payload.lessons_learned,
        session_id=session_id,
    )
    return {
        "ok": True,
        "reflection_id": str(reflection.id),
        "reflection_type": reflection.reflection_type,
        "chapter_no": reflection.chapter_no,
        "created_at": reflection.created_at.isoformat() if reflection.created_at else None,
    }


@router.get("/{project_id}/reflections")
async def list_reflections(
    project_id: uuid.UUID,
    reflection_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """列出反思记录。"""
    lab = LearningLab(db, project_id)
    reflections = await lab.list_reflections(
        project_id=project_id,
        reflection_type=reflection_type,
        limit=limit,
    )
    return [
        {
            "id": str(r.id),
            "reflection_type": r.reflection_type,
            "chapter_no": r.chapter_no,
            "content": r.content[:200] if r.content else "",
            "decisions": r.decisions,
            "lessons_learned": r.lessons_learned,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reflections
    ]
