"""作品大脑路由 - GET /api/brain/{project_id}。

聚合角色 / 关系 / 情节线 / 当前故事状态 / 章节摘要，返回结构对齐前端
``BrainOverview`` TypeScript 类型。

字段映射：
- ``Relationship.relationship_type`` → ``relation_type``
- ``PlotThread.name`` → ``title``，``status`` 做值映射
- ``Character.attributes`` 拆分为 ``aliases/appearance/personality/background/motivation/arc``
- ``CurrentStoryState.state`` JSON 拆分为前端各字段
- ``StorylineVolume.volume_no`` → ``volume_index``
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.models.character import Character, Relationship
from app.db.models.plot import CurrentStoryState, PlotThread
from app.db.models.summary import ChapterSummary

router = APIRouter(prefix="/api/brain", tags=["brain"])


class BrainOverview(BaseModel):
    """对齐前端 BrainOverview 类型。"""

    characters: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    plot_threads: list[dict[str, Any]] = []
    current_state: Optional[dict[str, Any]] = None
    summaries: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------
def _map_plot_status(status: Optional[str]) -> str:
    """将后端 PlotThread.status 映射为前端 'open' | 'resolved' | 'abandoned'。"""
    mapping = {
        "planned": "open",
        "active": "open",
        "resolved": "resolved",
        "abandoned": "abandoned",
    }
    if not status:
        return "open"
    return mapping.get(status, "open")


def serialize_character(c: Character) -> dict[str, Any]:
    """将 Character ORM 对象序列化为前端 Character 结构。

    ``attributes`` JSON 拆分为 ``aliases/appearance/personality/background/motivation/arc``。
    """
    attrs = c.attributes or {}
    if not isinstance(attrs, dict):
        attrs = {}
    aliases = attrs.get("aliases") or attrs.get("别名") or []
    if not isinstance(aliases, list):
        aliases = [str(aliases)] if aliases else []

    return {
        "id": str(c.id),
        "project_id": str(c.project_id),
        "name": c.name,
        "aliases": aliases,
        "role": c.role,
        "description": c.description,
        "appearance": attrs.get("appearance") or attrs.get("外貌"),
        "personality": attrs.get("personality") or attrs.get("性格"),
        "background": attrs.get("background") or attrs.get("背景"),
        "motivation": attrs.get("motivation") or attrs.get("动机"),
        "arc": attrs.get("arc") or attrs.get("弧线"),
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def serialize_relationship(r: Relationship) -> dict[str, Any]:
    """将 Relationship ORM 对象序列化为前端 Relationship 结构。

    字段映射：``relationship_type`` → ``relation_type``。
    """
    return {
        "id": str(r.id),
        "project_id": str(r.project_id),
        "from_character_id": str(r.from_character_id),
        "to_character_id": str(r.to_character_id),
        "relation_type": r.relationship_type,
        "description": r.description,
    }


def serialize_plot_thread(p: PlotThread) -> dict[str, Any]:
    """将 PlotThread ORM 对象序列化为前端 PlotThread 结构。

    字段映射：``name`` → ``title``，``status`` 做值映射。
    """
    return {
        "id": str(p.id),
        "project_id": str(p.project_id),
        "title": p.name,
        "description": p.description,
        "status": _map_plot_status(p.status),
        "introduced_chapter": p.introduced_chapter,
        "resolved_chapter": p.resolved_chapter,
    }


def serialize_current_state(s: CurrentStoryState) -> dict[str, Any]:
    """将 CurrentStoryState ORM 对象序列化为前端 CurrentStoryState 结构。

    从 ``state`` JSON 中拆分 location/time_of_day/mood/present_characters/
    active_threads/last_events 等字段。
    """
    state = s.state or {}
    if not isinstance(state, dict):
        state = {}
    return {
        "project_id": str(s.project_id),
        "current_chapter": s.chapter_no,
        "current_scene": state.get("current_scene") or state.get("scene"),
        "time_of_day": state.get("time_of_day") or state.get("time"),
        "location": state.get("location"),
        "present_characters": state.get("present_characters") or [],
        "active_threads": state.get("active_threads") or [],
        "mood": state.get("mood"),
        "last_events": state.get("last_events") or [],
    }


def serialize_chapter_summary(cs: ChapterSummary) -> dict[str, Any]:
    """将 ChapterSummary ORM 对象序列化为前端 ChapterSummary 结构。"""
    key_events = cs.facts_asserted or []
    if not isinstance(key_events, list):
        key_events = []
    character_changes = cs.entities_involved or []
    if not isinstance(character_changes, list):
        character_changes = []
    return {
        "id": str(cs.id),
        "chapter_id": str(cs.chapter_id) if cs.chapter_id else None,
        "summary": cs.summary,
        "key_events": [str(e) for e in key_events],
        "character_changes": [str(e) for e in character_changes],
        "created_at": cs.created_at.isoformat() if cs.created_at else None,
    }


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("/{project_id}", response_model=BrainOverview)
async def get_brain(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """聚合项目大脑信息（角色/关系/情节线/当前状态/摘要）。"""
    # 角色
    char_stmt = (
        select(Character)
        .where(Character.project_id == project_id)
        .order_by(Character.created_at.asc())
    )
    char_result = await db.execute(char_stmt)
    characters = [serialize_character(c) for c in char_result.scalars().all()]

    # 关系
    rel_stmt = select(Relationship).where(Relationship.project_id == project_id)
    rel_result = await db.execute(rel_stmt)
    relationships = [serialize_relationship(r) for r in rel_result.scalars().all()]

    # 情节线
    pt_stmt = select(PlotThread).where(PlotThread.project_id == project_id)
    pt_result = await db.execute(pt_stmt)
    plot_threads = [serialize_plot_thread(p) for p in pt_result.scalars().all()]

    # 当前故事状态（取最新一条）
    css_stmt = (
        select(CurrentStoryState)
        .where(CurrentStoryState.project_id == project_id)
        .order_by(CurrentStoryState.chapter_no.desc())
        .limit(1)
    )
    css_result = await db.execute(css_stmt)
    css_obj = css_result.scalar_one_or_none()
    current_state = serialize_current_state(css_obj) if css_obj else None

    # 章节摘要
    sum_stmt = (
        select(ChapterSummary)
        .where(ChapterSummary.project_id == project_id)
        .order_by(ChapterSummary.chapter_no.asc())
    )
    sum_result = await db.execute(sum_stmt)
    summaries = [serialize_chapter_summary(s) for s in sum_result.scalars().all()]

    return BrainOverview(
        characters=characters,
        relationships=relationships,
        plot_threads=plot_threads,
        current_state=current_state,
        summaries=summaries,
    )
