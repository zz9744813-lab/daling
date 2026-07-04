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

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.learning import LearningLab, PromptLab, SkillLab

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


class PromptExperimentRequest(BaseModel):
    prompt_a: str
    prompt_b: str
    test_input: str
    judge_prompt: Optional[str] = None


class SkillTestRequest(BaseModel):
    skill_name: str
    test_cases: list[dict[str, Any]] = Field(
        ..., min_length=1,
        description="测试用例列表",
    )


class ReflectionRequest(BaseModel):
    reflection_type: str = Field(
        ..., description="pre_chapter/post_chapter/session_end/volume_end"
    )
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

    return EvolutionView(
        project_id=str(project_id),
        prompt_experiments=experiments,
        skill_tests=skill_tests,
        reflections_count=len(reflections),
        latest_suggestions=suggestions,
    )


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
