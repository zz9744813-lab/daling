"""作品记忆路由（v5.0）- 查询 / 添加 / 提取文风。"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.context.book_memory_manager import BookMemoryManager
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


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _to_out(memory: Any) -> BookMemoryOut:
    value = memory.value
    if not isinstance(value, dict):
        value = {"text": str(value)}
    return BookMemoryOut(
        id=str(memory.id),
        project_id=str(memory.project_id),
        memory_type=memory.memory_type,
        key=memory.key,
        value=value,
        source=memory.source,
        confidence=memory.confidence,
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get("/{project_id}", response_model=list[BookMemoryOut])
async def list_book_memory(
    project_id: uuid.UUID,
    memory_type: Optional[str] = Query(None, description="按类型过滤: style/tone/convention/preference/lesson"),
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
    result = await manager.extract_style_from_chapters(
        chapter_count=payload.chapter_count
    )
    return ExtractStyleResponse(ok=True, style=result)
