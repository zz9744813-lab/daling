"""故事线路由 - GET /api/storyline/{project_id}。

返回结构对齐前端 ``StorylineOverview`` TypeScript 类型：
- ``volumes``: 卷宗列表（含嵌套 ``beats``）
- ``chapters``: 章节列表

字段映射：
- ``StorylineVolume.volume_no`` → ``volume_index``
- ``StorylineBeat.beat_no`` → ``beat_index``，``description`` → ``summary``
- ``Chapter.chapter_no`` → ``chapter_number``，``status`` 做值映射
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.models.chapter import Chapter
from app.db.models.storyline import StorylineBeat, StorylineVolume

router = APIRouter(prefix="/api/storyline", tags=["storyline"])


class StorylineOverview(BaseModel):
    """对齐前端 StorylineOverview 类型。"""

    volumes: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []


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


def serialize_beat(b: StorylineBeat) -> dict[str, Any]:
    """将 StorylineBeat ORM 对象序列化为前端 StorylineBeat 结构。

    字段映射：``beat_no`` → ``beat_index``，``description`` → ``summary``。
    """
    plot_threads = b.plot_threads or []
    if not isinstance(plot_threads, list):
        plot_threads = []
    return {
        "id": str(b.id),
        "volume_id": str(b.volume_id) if b.volume_id else None,
        "title": b.title,
        "beat_index": b.beat_no,
        "summary": b.description,
        "emotional_arc": None,
        "chapter_ids": [str(c) for c in plot_threads],
    }


def serialize_volume(
    v: StorylineVolume, beats: list[StorylineBeat]
) -> dict[str, Any]:
    """将 StorylineVolume ORM 对象序列化为前端 StorylineVolume 结构。

    字段映射：``volume_no`` → ``volume_index``，并嵌套 ``beats``。
    """
    nested_beats = [
        serialize_beat(b) for b in beats if b.volume_id and str(b.volume_id) == str(v.id)
    ]
    return {
        "id": str(v.id),
        "project_id": str(v.project_id),
        "title": v.title,
        "volume_index": v.volume_no,
        "summary": v.summary,
        "beats": nested_beats,
    }


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("/{project_id}", response_model=StorylineOverview)
async def get_storyline(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """聚合项目故事线（卷宗 + 节拍 + 章节）。"""
    # 卷宗
    vol_stmt = (
        select(StorylineVolume)
        .where(StorylineVolume.project_id == project_id)
        .order_by(StorylineVolume.volume_no.asc())
    )
    vol_result = await db.execute(vol_stmt)
    volumes = list(vol_result.scalars().all())

    # 节拍
    beat_stmt = (
        select(StorylineBeat)
        .where(StorylineBeat.project_id == project_id)
        .order_by(StorylineBeat.beat_no.asc())
    )
    beat_result = await db.execute(beat_stmt)
    beats = list(beat_result.scalars().all())

    # 章节
    ch_stmt = (
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_no.asc())
    )
    ch_result = await db.execute(ch_stmt)
    chapters = [serialize_chapter(c) for c in ch_result.scalars().all()]

    return StorylineOverview(
        volumes=[serialize_volume(v, beats) for v in volumes],
        chapters=chapters,
    )
