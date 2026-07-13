"""设定事实路由（v5.0）- 列表 / 断言 / 确认 / 取代 / 冲突检查。"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.context.canon_manager import CanonConflictError, CanonManager
from app.db import get_db

router = APIRouter(prefix="/api/canon-facts", tags=["canon"])


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class CanonFactOut(BaseModel):
    id: str
    project_id: str
    fact_type: str
    subject_type: str
    subject_id: Optional[str] = None
    predicate: str
    object_value: str
    mutability: str
    confidence: float
    status: str
    source_chapter_no: Optional[int] = None
    last_confirmed_chapter_no: Optional[int] = None
    superseded_by_fact_id: Optional[str] = None
    tags: list[Any] = []

    model_config = {"from_attributes": True}


class AssertFactRequest(BaseModel):
    fact_type: str = Field(
        ...,
        description="事实类型: setting/character/item/rule/relationship/event/location",
    )
    subject_type: str = Field(..., description="主体类型")
    subject_id: Optional[str] = Field(None, description="主体标识（通常为名称）")
    predicate: str = Field(..., description="谓词")
    object_value: str = Field(..., description="客体值")
    mutability: str = Field("soft", description="可变性: immutable/soft/dynamic")
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    source_chapter_no: Optional[int] = None
    tags: list[str] = []


class ConfirmRequest(BaseModel):
    fact_id: str
    confirmed_chapter_no: int


class SupersedeRequest(BaseModel):
    old_fact_id: str
    new_fact_data: dict[str, Any] = Field(default_factory=dict)


class CheckConflictRequest(BaseModel):
    text: str
    chapter_no: Optional[int] = None


class ConflictInfo(BaseModel):
    has_conflict: bool
    conflicting_facts: list[dict[str, Any]]
    can_supersede: bool
    extracted_facts: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _to_out(fact: Any) -> CanonFactOut:
    return CanonFactOut(
        id=str(fact.id),
        project_id=str(fact.project_id),
        fact_type=fact.fact_type,
        subject_type=fact.subject_type,
        subject_id=fact.subject_id,
        predicate=fact.predicate,
        object_value=fact.object_value,
        mutability=fact.mutability,
        confidence=fact.confidence,
        status=fact.status,
        source_chapter_no=fact.source_chapter_no,
        last_confirmed_chapter_no=fact.last_confirmed_chapter_no,
        superseded_by_fact_id=(
            str(fact.superseded_by_fact_id) if fact.superseded_by_fact_id else None
        ),
        tags=fact.tags or [],
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/{project_id}", response_model=list[CanonFactOut])
async def list_canon_facts(
    project_id: uuid.UUID,
    status: Optional[str] = Query(None, description="按状态过滤: active/superseded/revoked"),
    fact_type: Optional[str] = Query(None, description="按事实类型过滤"),
    subject_type: Optional[str] = Query(None, description="按主体类型过滤"),
    subject_id: Optional[str] = Query(None, description="按主体标识过滤"),
    mutability: Optional[str] = Query(None, description="按可变性过滤: immutable/soft/dynamic"),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """查询事实列表，支持多维度过滤。"""
    manager = CanonManager(db, project_id)
    facts = await manager.list_facts(
        status=status,
        fact_type=fact_type,
        subject_type=subject_type,
        subject_id=subject_id,
        mutability=mutability,
        offset=offset,
        limit=limit,
    )
    return [_to_out(f) for f in facts]


@router.post("/{project_id}/assert", response_model=CanonFactOut, status_code=201)
async def assert_fact(
    project_id: uuid.UUID,
    payload: AssertFactRequest,
    db: AsyncSession = Depends(get_db),
):
    """断言新事实。

    - 与 immutable 事实冲突时返回 409。
    - 与 soft/evolving 事实冲突时自动取代旧事实。
    """
    manager = CanonManager(db, project_id)
    try:
        fact = await manager.assert_fact(
            fact_type=payload.fact_type,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            predicate=payload.predicate,
            object_value=payload.object_value,
            mutability=payload.mutability,
            confidence=payload.confidence,
            source_chapter_no=payload.source_chapter_no,
            tags=payload.tags,
        )
        return _to_out(fact)
    except CanonConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "message": e.message,
                "conflicting_facts": e.conflicting_facts,
            },
        )


@router.post("/{project_id}/confirm", response_model=CanonFactOut)
async def confirm_fact(
    project_id: uuid.UUID,
    payload: ConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    """确认事实在某章仍然成立。"""
    manager = CanonManager(db, project_id)
    try:
        fact = await manager.confirm_fact(
            uuid.UUID(payload.fact_id),
            payload.confirmed_chapter_no,
        )
        return _to_out(fact)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{project_id}/supersede", response_model=CanonFactOut, status_code=201)
async def supersede_fact(
    project_id: uuid.UUID,
    payload: SupersedeRequest,
    db: AsyncSession = Depends(get_db),
):
    """用新事实取代旧事实。"""
    manager = CanonManager(db, project_id)
    try:
        fact = await manager.supersede_fact(
            uuid.UUID(payload.old_fact_id),
            payload.new_fact_data,
        )
        return _to_out(fact)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{project_id}/check", response_model=ConflictInfo)
async def check_conflict(
    project_id: uuid.UUID,
    payload: CheckConflictRequest,
    db: AsyncSession = Depends(get_db),
):
    """检查文本冲突。

    从文本中抽取事实断言，与现有 canon facts 比对，返回冲突信息。
    LLM 未配置时 extracted_facts 为空列表。
    """
    manager = CanonManager(db, project_id)

    # 从文本中抽取事实
    extracted = await manager.extract_facts_from_text(payload.text, payload.chapter_no)

    # 检查每条抽取事实的冲突
    all_conflicts: list[dict] = []
    can_supersede = True
    for fact in extracted:
        result = await manager.check_conflict(fact)
        if result["has_conflict"]:
            all_conflicts.extend(result["conflicting_facts"])
            if not result["can_supersede"]:
                can_supersede = False

    return ConflictInfo(
        has_conflict=len(all_conflicts) > 0,
        conflicting_facts=all_conflicts,
        can_supersede=can_supersede,
        extracted_facts=extracted,
    )
