"""Storyline workbench revision, lock, and atomic-replacement contracts."""

from __future__ import annotations

import app.db.models  # noqa: F401 - register every foreign-key target
import pytest
import pytest_asyncio
from app.agents.story_architect import StoryArchitect
from app.api.routes_pipeline import GenerateOutlineRequest, generate_outline
from app.api.routes_storyline import BeatPatch, get_storyline, update_beat
from app.core.database import Base
from app.db.models.chapter import Chapter
from app.db.models.project import Project
from app.db.models.storyline import StorylineBeat, StorylineVolume
from app.db.models.world import WorldBible
from app.services.preparation_state import mark_artifact_fresh, record_outline_change
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db_factory(tmp_path):
    database = tmp_path / "storyline-workbench.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_storyline(db_factory, *, with_bible: bool = False):
    async with db_factory() as db:
        extra, _ = record_outline_change(
            {},
            text="第一卷 旧世界\n第一章 旧开端",
            filename="source-v1.md",
            world_bible_exists=False,
            outline_exists=False,
        )
        extra = mark_artifact_fresh(extra, "outline")
        project = Project(title="Storyline workbench", extra=extra)
        db.add(project)
        await db.flush()
        volume = StorylineVolume(
            project_id=project.id,
            volume_no=1,
            title="旧卷",
            summary="旧卷摘要",
            target_chapters=1,
        )
        db.add(volume)
        await db.flush()
        beat = StorylineBeat(
            project_id=project.id,
            volume_id=volume.id,
            beat_no=1,
            chapter_no=1,
            title="旧章",
            description="旧节拍",
        )
        chapter = Chapter(
            project_id=project.id,
            chapter_no=1,
            title="旧章",
            status="draft",
            word_count=0,
        )
        db.add_all([beat, chapter])
        if with_bible:
            db.add(
                WorldBible(
                    project_id=project.id,
                    version=1,
                    content={"world_name": "测试世界", "setting": "测试设定"},
                    summary="测试世界观",
                )
            )
        await db.commit()
        return project.id, volume.id, beat.id, chapter.id


@pytest.mark.asyncio
async def test_storyline_overview_maps_source_revision_and_guarded_edits(db_factory):
    project_id, _volume_id, beat_id, chapter_id = await _seed_storyline(db_factory)

    async with db_factory() as db:
        overview = await get_storyline(project_id, db)
        assert overview.source["filename"] == "source-v1.md"
        assert overview.source["revision"] == 1
        assert overview.artifact["structure_revision"] == 1
        assert overview.artifact["ready"] is True
        assert overview.volumes[0]["beats"][0]["chapter_ids"] == [str(chapter_id)]
        assert overview.chapters[0]["beat_id"] == str(beat_id)

        edited = await update_beat(
            project_id,
            beat_id,
            BeatPatch(expected_revision=1, title="新章名", summary="新节拍"),
            db,
        )
        assert edited.artifact["structure_revision"] == 2
        assert edited.volumes[0]["beats"][0]["title"] == "新章名"
        assert edited.chapters[0]["title"] == "新章名"

        with pytest.raises(HTTPException) as stale_edit:
            await update_beat(
                project_id,
                beat_id,
                BeatPatch(expected_revision=1, title="过期编辑"),
                db,
            )
        assert stale_edit.value.status_code == 409
        assert stale_edit.value.detail["code"] == "storyline_revision_conflict"

        chapter = await db.get(Chapter, chapter_id)
        assert chapter is not None
        chapter.word_count = 120
        await db.flush()
        with pytest.raises(HTTPException) as locked_edit:
            await update_beat(
                project_id,
                beat_id,
                BeatPatch(expected_revision=2, title="不可覆盖正文"),
                db,
            )
        assert locked_edit.value.status_code == 409
        assert locked_edit.value.detail["code"] == "storyline_node_locked"


@pytest.mark.asyncio
async def test_generate_outline_requires_explicit_revisioned_replace(db_factory):
    project_id, _volume_id, _beat_id, chapter_id = await _seed_storyline(db_factory)

    async with db_factory() as db:
        with pytest.raises(HTTPException) as implicit_append:
            await generate_outline(
                project_id,
                GenerateOutlineRequest(volume_count=1, chapters_per_volume=1),
                db,
            )
        assert implicit_append.value.status_code == 409
        assert implicit_append.value.detail["code"] == "storyline_exists"

        with pytest.raises(HTTPException) as missing_revision:
            await generate_outline(
                project_id,
                GenerateOutlineRequest(
                    volume_count=1,
                    chapters_per_volume=1,
                    replace_existing=True,
                ),
                db,
            )
        assert missing_revision.value.status_code == 422
        assert missing_revision.value.detail["code"] == "storyline_revision_required"

        with pytest.raises(HTTPException) as stale_revision:
            await generate_outline(
                project_id,
                GenerateOutlineRequest(
                    volume_count=1,
                    chapters_per_volume=1,
                    replace_existing=True,
                    expected_revision=9,
                ),
                db,
            )
        assert stale_revision.value.status_code == 409
        assert stale_revision.value.detail["code"] == "storyline_revision_conflict"

        chapter = await db.get(Chapter, chapter_id)
        assert chapter is not None
        chapter.status = "review"
        await db.flush()
        with pytest.raises(HTTPException) as written_lock:
            await generate_outline(
                project_id,
                GenerateOutlineRequest(
                    volume_count=1,
                    chapters_per_volume=1,
                    replace_existing=True,
                    expected_revision=1,
                ),
                db,
            )
        assert written_lock.value.status_code == 409
        assert written_lock.value.detail["code"] == "storyline_replace_locked"
        assert written_lock.value.detail["locked_chapters"] == [1]


@pytest.mark.asyncio
async def test_generate_outline_atomically_replaces_unstarted_structure(
    db_factory,
    monkeypatch,
):
    project_id, old_volume_id, old_beat_id, old_chapter_id = await _seed_storyline(
        db_factory,
        with_bible=True,
    )

    async def fake_outline_json(*_args, **_kwargs):
        return {
            "volumes": [
                {
                    "title": "新卷",
                    "summary": "新卷摘要",
                    "beats": [
                        {
                            "title": "新开端",
                            "description": "新节拍",
                            "importance": "high",
                            "plot_threads": ["main"],
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(StoryArchitect, "_llm_json", fake_outline_json)
    async with db_factory() as db:
        response = await generate_outline(
            project_id,
            GenerateOutlineRequest(
                volume_count=1,
                chapters_per_volume=1,
                replace_existing=True,
                expected_revision=1,
            ),
            db,
        )
        assert response.result["replaced"] is True
        assert response.result["previous_structure_revision"] == 1
        assert response.result["structure_revision"] == 2
        assert await db.get(StorylineVolume, old_volume_id) is None
        assert await db.get(StorylineBeat, old_beat_id) is None
        assert await db.get(Chapter, old_chapter_id) is None
        new_chapter = await db.scalar(
            select(Chapter).where(Chapter.project_id == project_id)
        )
        assert new_chapter is not None
        assert new_chapter.chapter_no == 1
        assert new_chapter.title == "新开端"


@pytest.mark.asyncio
async def test_replace_savepoint_restores_old_structure_on_persistence_error(
    db_factory,
    monkeypatch,
):
    project_id, old_volume_id, old_beat_id, old_chapter_id = await _seed_storyline(
        db_factory,
        with_bible=True,
    )

    async def malformed_outline_json(*_args, **_kwargs):
        return {
            "volumes": [
                {
                    "title": "会失败的新卷",
                    "beats": [None],
                }
            ]
        }

    monkeypatch.setattr(StoryArchitect, "_llm_json", malformed_outline_json)
    async with db_factory() as db:
        world_bible = await db.scalar(
            select(WorldBible).where(WorldBible.project_id == project_id)
        )
        assert world_bible is not None
        architect = StoryArchitect(
            gateway=None,  # type: ignore[arg-type] - LLM call is patched above
            db=db,
            project_id=project_id,
        )
        with pytest.raises(AttributeError):
            await architect.generate_outline(
                world_bible,
                volume_count=1,
                chapters_per_volume=1,
                replace_existing=True,
            )

        assert await db.get(StorylineVolume, old_volume_id) is not None
        assert await db.get(StorylineBeat, old_beat_id) is not None
        assert await db.get(Chapter, old_chapter_id) is not None
        assert (
            await db.scalar(
                select(func.count(StorylineVolume.id)).where(
                    StorylineVolume.project_id == project_id
                )
            )
            == 1
        )
