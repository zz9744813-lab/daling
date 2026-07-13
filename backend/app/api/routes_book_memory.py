"""作品记忆路由（v5.0）- 查询 / 添加 / 提取文风。"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.context.book_memory_manager import (
    BookMemoryManager,
    memory_governance,
    visible_memory_value,
)
from app.db import get_db

router = APIRouter(prefix="/api/book-memory", tags=["book-memory"])


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class BookMemoryOut(BaseModel):
    id: str
    project_id: str
    memory_type: str
    key: str
    value: dict[str, Any]
    source: Optional[str] = None
    confidence: float
    status: str = "active"
    governance: dict[str, Any] = {}
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = {"from_attributes": True}


class AddMemoryRequest(BaseModel):
    memory_type: str = Field(..., description="记忆类型: style/tone/convention/preference/lesson")
    key: str = Field(..., description="记忆键名")
    value: dict[str, Any] | str = Field(..., description="记忆值（dict 或 str）")
    source: Optional[str] = Field(None, description="来源标识")


class ExtractStyleRequest(BaseModel):
    chapter_count: int = Field(5, ge=1, le=20, description="分析的章节数量")


class ExtractStyleResponse(BaseModel):
    ok: bool = True
    style: dict[str, Any] = {}


class MemoryGovernanceRequest(BaseModel):
    actor: str = Field("user", min_length=1, max_length=100)
    reason: Optional[str] = Field(None, max_length=2000)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _to_out(memory: Any) -> BookMemoryOut:
    value = visible_memory_value(memory)
    governance = memory_governance(memory)
    return BookMemoryOut(
        id=str(memory.id),
        project_id=str(memory.project_id),
        memory_type=memory.memory_type,
        key=memory.key,
        value=value,
        source=memory.source,
        confidence=memory.confidence,
        status=governance["status"],
        governance=governance,
        created_at=memory.created_at.isoformat() if memory.created_at else None,
        updated_at=memory.updated_at.isoformat() if memory.updated_at else None,
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/{project_id}", response_model=list[BookMemoryOut])
async def list_book_memory(
    project_id: uuid.UUID,
    memory_type: Optional[str] = Query(
        None,
        description="按类型过滤: style/tone/convention/preference/lesson",
    ),
    db: AsyncSession = Depends(get_db),
):
    """查询作品记忆列表。"""
    manager = BookMemoryManager(db, project_id)
    memories = await manager.get_memory(memory_type=memory_type)
    return [_to_out(m) for m in memories]


@router.post("/{project_id}", response_model=BookMemoryOut, status_code=201)
async def add_memory(
    project_id: uuid.UUID,
    payload: AddMemoryRequest,
    db: AsyncSession = Depends(get_db),
):
    """添加记忆条目。"""
    manager = BookMemoryManager(db, project_id)
    memory = await manager.add_memory(
        memory_type=payload.memory_type,
        key=payload.key,
        value=payload.value,
        source=payload.source,
    )
    return _to_out(memory)


async def _govern_memory(
    project_id: uuid.UUID,
    memory_id: uuid.UUID,
    action: str,
    payload: MemoryGovernanceRequest,
    db: AsyncSession,
) -> BookMemoryOut:
    manager = BookMemoryManager(db, project_id)
    try:
        memory = await manager.review_memory(
            memory_id,
            action=action,
            actor=payload.actor,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_out(memory)


@router.post("/{project_id}/memories/{memory_id}/approve", response_model=BookMemoryOut)
async def approve_memory(
    project_id: uuid.UUID,
    memory_id: uuid.UUID,
    payload: MemoryGovernanceRequest,
    db: AsyncSession = Depends(get_db),
):
    """Explicitly activate or restore a reviewed memory rule."""
    return await _govern_memory(project_id, memory_id, "approve", payload, db)


@router.post("/{project_id}/memories/{memory_id}/reject", response_model=BookMemoryOut)
async def reject_memory(
    project_id: uuid.UUID,
    memory_id: uuid.UUID,
    payload: MemoryGovernanceRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reject a learned memory so it no longer enters production context."""
    return await _govern_memory(project_id, memory_id, "reject", payload, db)


@router.post("/{project_id}/memories/{memory_id}/rollback", response_model=BookMemoryOut)
async def rollback_memory(
    project_id: uuid.UUID,
    memory_id: uuid.UUID,
    payload: MemoryGovernanceRequest,
    db: AsyncSession = Depends(get_db),
):
    """Deactivate an active memory while preserving its evidence and audit history."""
    return await _govern_memory(project_id, memory_id, "rollback", payload, db)


@router.post("/{project_id}/extract-style", response_model=ExtractStyleResponse)
async def extract_style(
    project_id: uuid.UUID,
    payload: ExtractStyleRequest,
    db: AsyncSession = Depends(get_db),
):
    """从已完成章节提取文风特征。

    分析已完成章节的文风，存入 book_memory。
    LLM 未配置时返回空 style。
    """
    manager = BookMemoryManager(db, project_id)
    result = await manager.extract_style_from_chapters(chapter_count=payload.chapter_count)
    return ExtractStyleResponse(ok=True, style=result)
