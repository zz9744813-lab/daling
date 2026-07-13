"""Professional storyline workbench API.

The overview combines the editable volume/beat tree with source provenance and
staleness.  Structure mutations are deliberately narrow, optimistic, and also
protected by the global manual/continuous production guard.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.production_guard import manual_production_guard
from app.db import get_db
from app.db.models.chapter import Chapter
from app.db.models.project import Project
from app.db.models.storyline import StorylineBeat, StorylineVolume
from app.services.preparation_state import (
    artifact_provenance_state,
    artifact_stale_state,
    outline_source,
    record_artifact_edit,
)

router = APIRouter(prefix="/api/storyline", tags=["storyline"])


class StorylineOverview(BaseModel):
    project_id: str
    volumes: list[dict[str, Any]] = Field(default_factory=list)
    chapters: list[dict[str, Any]] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    artifact: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)


class VolumePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: int = Field(ge=1)
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    summary: Optional[str] = Field(default=None, max_length=30_000)


class BeatPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: int = Field(ge=1)
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    summary: Optional[str] = Field(default=None, max_length=30_000)


def _map_chapter_status(status: Optional[str]) -> str:
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


def _chapter_is_locked(chapter: Optional[Chapter]) -> bool:
    """A planned node becomes immutable after manuscript work has begun."""
    if chapter is None:
        return False
    return bool(
        (chapter.word_count or 0) > 0
        or chapter.current_version_id is not None
        or chapter.status not in {"planned", "draft"}
    )


def serialize_chapter(
    chapter: Chapter,
    beat_by_chapter_no: dict[int, StorylineBeat],
) -> dict[str, Any]:
    beat = beat_by_chapter_no.get(chapter.chapter_no)
    return {
        "id": str(chapter.id),
        "project_id": str(chapter.project_id),
        "volume_id": str(beat.volume_id) if beat and beat.volume_id else None,
        "beat_id": str(beat.id) if beat else None,
        "chapter_number": chapter.chapter_no,
        "title": chapter.title,
        "status": _map_chapter_status(chapter.status),
        "raw_status": chapter.status,
        "summary": beat.description if beat else None,
        "word_count": chapter.word_count,
        "target_words": chapter.target_words,
        "structure_locked": _chapter_is_locked(chapter),
        "created_at": chapter.created_at.isoformat() if chapter.created_at else None,
        "updated_at": chapter.updated_at.isoformat() if chapter.updated_at else None,
    }


def serialize_beat(
    beat: StorylineBeat,
    chapters_by_no: dict[int, Chapter],
) -> dict[str, Any]:
    chapter = chapters_by_no.get(beat.chapter_no) if beat.chapter_no is not None else None
    locked = _chapter_is_locked(chapter)
    return {
        "id": str(beat.id),
        "volume_id": str(beat.volume_id) if beat.volume_id else None,
        "title": beat.title,
        "beat_index": beat.beat_no,
        "chapter_number": beat.chapter_no,
        "summary": beat.description,
        "emotional_arc": None,
        "chapter_ids": [str(chapter.id)] if chapter else [],
        "importance": beat.importance,
        "status": beat.status,
        "structure_locked": locked,
        "lock_reason": "章节已有正文或已进入生产流程" if locked else None,
    }


def serialize_volume(
    volume: StorylineVolume,
    beats: list[StorylineBeat],
    chapters_by_no: dict[int, Chapter],
) -> dict[str, Any]:
    volume_beats = [
        beat for beat in beats if beat.volume_id and str(beat.volume_id) == str(volume.id)
    ]
    nested_beats = [serialize_beat(beat, chapters_by_no) for beat in volume_beats]
    locked_chapters = [
        int(beat.chapter_no)
        for beat in volume_beats
        if beat.chapter_no is not None and _chapter_is_locked(chapters_by_no.get(beat.chapter_no))
    ]
    return {
        "id": str(volume.id),
        "project_id": str(volume.project_id),
        "title": volume.title,
        "volume_index": volume.volume_no,
        "summary": volume.summary,
        "target_chapters": volume.target_chapters,
        "status": volume.status,
        "structure_locked": bool(locked_chapters),
        "locked_chapters": locked_chapters,
        "beats": nested_beats,
    }


async def _load_structure(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[Project, list[StorylineVolume], list[StorylineBeat], list[Chapter]]:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    volumes = list(
        (
            await db.execute(
                select(StorylineVolume)
                .where(StorylineVolume.project_id == project_id)
                .order_by(StorylineVolume.volume_no.asc())
            )
        )
        .scalars()
        .all()
    )
    beats = list(
        (
            await db.execute(
                select(StorylineBeat)
                .where(StorylineBeat.project_id == project_id)
                .order_by(StorylineBeat.chapter_no.asc(), StorylineBeat.beat_no.asc())
            )
        )
        .scalars()
        .all()
    )
    chapters = list(
        (
            await db.execute(
                select(Chapter)
                .where(Chapter.project_id == project_id)
                .order_by(Chapter.chapter_no.asc())
            )
        )
        .scalars()
        .all()
    )
    return project, volumes, beats, chapters


def _revision_conflict(expected: int, actual: int) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "storyline_revision_conflict",
            "message": "故事线已被其他操作更新，请刷新后再编辑。",
            "expected_revision": expected,
            "actual_revision": actual,
        },
    )


def _node_locked(chapter_numbers: list[int]) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "storyline_node_locked",
            "message": "该结构已关联正文或正在审稿，不能直接改写。请在人工接管流程中处理。",
            "chapter_numbers": chapter_numbers,
        },
    )


@router.get("/{project_id}", response_model=StorylineOverview)
async def get_storyline(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Return structure plus uploaded-source and artifact provenance."""
    project, volumes, beats, chapters = await _load_structure(project_id, db)
    chapters_by_no = {chapter.chapter_no: chapter for chapter in chapters}
    beat_by_chapter_no = {
        int(beat.chapter_no): beat for beat in beats if beat.chapter_no is not None
    }
    extra = dict(project.extra or {})
    exists = bool(volumes or beats or chapters)
    source = outline_source(extra)
    stale = artifact_stale_state(extra, "outline", exists=exists)
    provenance = artifact_provenance_state(extra, "outline", exists=exists)
    locked_count = sum(1 for chapter in chapters if _chapter_is_locked(chapter))

    return StorylineOverview(
        project_id=str(project_id),
        volumes=[serialize_volume(volume, beats, chapters_by_no) for volume in volumes],
        chapters=[serialize_chapter(chapter, beat_by_chapter_no) for chapter in chapters],
        source={
            "type": "uploaded_outline" if source["present"] else "project_blueprint",
            "present": bool(source["present"]),
            "filename": source["filename"],
            "revision": int(source["revision"]),
            "sha256": source["sha256"],
            "updated_at": source["updated_at"],
        },
        artifact={
            "exists": exists,
            "ready": exists and not stale["stale"],
            "stale": bool(stale["stale"]),
            "stale_reason": stale.get("reason"),
            "structure_revision": int(provenance["artifact_revision"]),
            "based_on_source_revision": provenance.get("source_revision"),
            "generated_at": provenance.get("generated_at"),
            "updated_at": provenance.get("updated_at"),
            "last_change": provenance.get("last_change"),
            "can_replace": exists and locked_count == 0,
            "replace_blocked_by_chapters": [
                chapter.chapter_no for chapter in chapters if _chapter_is_locked(chapter)
            ],
        },
        stats={
            "volume_count": len(volumes),
            "beat_count": len(beats),
            "chapter_count": len(chapters),
            "locked_chapter_count": locked_count,
            "written_word_count": sum(int(chapter.word_count or 0) for chapter in chapters),
        },
    )


@router.patch("/{project_id}/volumes/{volume_id}", response_model=StorylineOverview)
async def update_volume(
    project_id: uuid.UUID,
    volume_id: uuid.UUID,
    payload: VolumePatch,
    db: AsyncSession = Depends(get_db),
    _manual_guard: None = Depends(manual_production_guard),
):
    """Edit a planned volume using an optimistic structure revision."""
    if payload.title is None and payload.summary is None:
        raise HTTPException(status_code=422, detail="至少提交 title 或 summary")
    project, volumes, beats, chapters = await _load_structure(project_id, db)
    volume = next((item for item in volumes if item.id == volume_id), None)
    if volume is None:
        raise HTTPException(status_code=404, detail="Volume not found")
    extra = dict(project.extra or {})
    actual_revision = int(
        artifact_provenance_state(extra, "outline", exists=True)["artifact_revision"]
    )
    if payload.expected_revision != actual_revision:
        raise _revision_conflict(payload.expected_revision, actual_revision)
    chapters_by_no = {chapter.chapter_no: chapter for chapter in chapters}
    locked = [
        int(beat.chapter_no)
        for beat in beats
        if beat.volume_id == volume.id
        and beat.chapter_no is not None
        and _chapter_is_locked(chapters_by_no.get(beat.chapter_no))
    ]
    if locked:
        raise _node_locked(locked)
    if payload.title is not None:
        volume.title = payload.title
    if payload.summary is not None:
        volume.summary = payload.summary
    project.extra = record_artifact_edit(extra, "outline", exists=True)
    flag_modified(project, "extra")
    await db.flush()
    return await get_storyline(project_id, db)


@router.patch("/{project_id}/beats/{beat_id}", response_model=StorylineOverview)
async def update_beat(
    project_id: uuid.UUID,
    beat_id: uuid.UUID,
    payload: BeatPatch,
    db: AsyncSession = Depends(get_db),
    _manual_guard: None = Depends(manual_production_guard),
):
    """Edit one unstarted beat and keep its planned chapter title synchronized."""
    if payload.title is None and payload.summary is None:
        raise HTTPException(status_code=422, detail="至少提交 title 或 summary")
    project, _volumes, beats, chapters = await _load_structure(project_id, db)
    beat = next((item for item in beats if item.id == beat_id), None)
    if beat is None:
        raise HTTPException(status_code=404, detail="Beat not found")
    extra = dict(project.extra or {})
    actual_revision = int(
        artifact_provenance_state(extra, "outline", exists=True)["artifact_revision"]
    )
    if payload.expected_revision != actual_revision:
        raise _revision_conflict(payload.expected_revision, actual_revision)
    chapter = next(
        (
            item
            for item in chapters
            if beat.chapter_no is not None and item.chapter_no == beat.chapter_no
        ),
        None,
    )
    if _chapter_is_locked(chapter):
        raise _node_locked([int(beat.chapter_no)] if beat.chapter_no is not None else [])
    if payload.title is not None:
        beat.title = payload.title
        if chapter is not None:
            chapter.title = payload.title
    if payload.summary is not None:
        beat.description = payload.summary
    project.extra = record_artifact_edit(extra, "outline", exists=True)
    flag_modified(project, "extra")
    await db.flush()
    return await get_storyline(project_id, db)
