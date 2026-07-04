"""规划反思路由（v5.0）- GET /api/planning-reflections/{project_id}。"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.models.memory import PlanningReflection

router = APIRouter(prefix="/api/planning-reflections", tags=["planning"])


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class PlanningReflectionOut(BaseModel):
    id: str
    project_id: str
    session_id: Optional[str] = None
    chapter_no: Optional[int] = None
    reflection_type: str
    content: str
    decisions: list[Any] = []
    lessons_learned: list[Any] = []

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _to_out(r: PlanningReflection) -> PlanningReflectionOut:
    return PlanningReflectionOut(
        id=str(r.id),
        project_id=str(r.project_id),
        session_id=str(r.session_id) if r.session_id else None,
        chapter_no=r.chapter_no,
        reflection_type=r.reflection_type,
        content=r.content,
        decisions=r.decisions or [],
        lessons_learned=r.lessons_learned or [],
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get("/{project_id}", response_model=list[PlanningReflectionOut])
async def list_planning_reflections(
    project_id: uuid.UUID,
    reflection_type: Optional[str] = Query(
        None, description="按类型过滤: pre_chapter/post_chapter/session_end/volume_end"
    ),
    chapter_no: Optional[int] = Query(None, description="按章节号过滤"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """查询规划反思列表，支持按类型和章节号过滤。"""
    stmt = select(PlanningReflection).where(
        PlanningReflection.project_id == project_id,
    )
    if reflection_type:
        stmt = stmt.where(PlanningReflection.reflection_type == reflection_type)
    if chapter_no is not None:
        stmt = stmt.where(PlanningReflection.chapter_no == chapter_no)
    stmt = (
        stmt.order_by(PlanningReflection.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    reflections = list(result.scalars().all())
    return [_to_out(r) for r in reflections]
