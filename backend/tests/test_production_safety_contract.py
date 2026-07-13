"""Production safety contracts spanning autopilot, preparation, and quality APIs."""

from __future__ import annotations

import io
import uuid

import app.db.models  # noqa: F401 - register every foreign-key target
import pytest
import pytest_asyncio
from app.api.production_guard import manual_production_guard
from app.api.routes_pipeline import chapter_quality_detail, preparation_status
from app.api.routes_projects import upload_outline
from app.core.database import Base
from app.db.models.automation import ContinuousRun
from app.db.models.chapter import Chapter, ChapterVersion
from app.db.models.project import Project
from app.db.models.quality import QualityAssessment, QualityIssue, RevisionAttempt
from app.db.models.storyline import StorylineVolume
from app.db.models.world import WorldBible
from app.services.continuous_production import (
    DEFAULT_POLICY,
    ContinuousProductionService,
    ManualPipelineConflictError,
)
from app.services.preparation_state import mark_artifact_fresh, record_outline_change
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db_factory(tmp_path):
    database = tmp_path / "production-safety.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _create_project(db_factory, title: str = "Production safety") -> Project:
    async with db_factory() as db:
        project = Project(title=title)
        db.add(project)
        await db.commit()
        return project


@pytest.mark.asyncio
async def test_manual_pipeline_and_continuous_worker_are_mutually_exclusive(db_factory):
    project = await _create_project(db_factory)
    async with db_factory() as db:
        db.add(
            ContinuousRun(
                project_id=project.id,
                desired_state="running",
                status="retry_wait",
                policy=dict(DEFAULT_POLICY),
            )
        )
        await db.commit()

    service = ContinuousProductionService()
    async with db_factory() as db:
        with pytest.raises(ManualPipelineConflictError):
            async with service.manual_pipeline_guard(project.id, db):
                pytest.fail("running autopilot must reject the manual writer")

        dependency = manual_production_guard(project.id, db)
        with pytest.raises(HTTPException) as exc_info:
            await dependency.__anext__()
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "continuous_run_active"

        run = await db.scalar(
            select(ContinuousRun).where(ContinuousRun.project_id == project.id)
        )
        assert run is not None
        run.desired_state = "paused"
        run.status = "paused"
        run.lease_owner = None
        run.lease_expires_at = None
        await db.commit()

        async with service.manual_pipeline_guard(project.id, db):
            async with db_factory() as observer:
                reserved = await observer.scalar(
                    select(ContinuousRun).where(ContinuousRun.project_id == project.id)
                )
                assert reserved is not None
                assert reserved.status == "manual_running"
                assert reserved.lease_owner is not None
                assert reserved.lease_owner.startswith("manual-")
            with pytest.raises(ManualPipelineConflictError):
                await service.start(project.id, db_factory=db_factory, target_chapters=1)

        released = await db.scalar(
            select(ContinuousRun).where(ContinuousRun.project_id == project.id)
        )
        assert released is not None
        assert released.desired_state == "paused"
        assert released.status == "paused"
        assert released.lease_owner is None


@pytest.mark.asyncio
async def test_continuous_status_separates_campaign_progress_from_real_manuscript_counts(
    db_factory,
):
    project = await _create_project(db_factory)
    async with db_factory() as db:
        db.add_all(
            [
                Chapter(project_id=project.id, chapter_no=2, title="A", status="approved"),
                Chapter(project_id=project.id, chapter_no=8, title="B", status="published"),
                Chapter(project_id=project.id, chapter_no=20, title="C", status="review"),
                Chapter(project_id=project.id, chapter_no=99, title="D", status="draft"),
                ContinuousRun(
                    project_id=project.id,
                    desired_state="paused",
                    status="paused",
                    target_chapters=5,
                    completed_chapters=2,
                    policy=dict(DEFAULT_POLICY),
                ),
            ]
        )
        await db.commit()

    status = await ContinuousProductionService().get_status(
        project.id,
        db_factory=db_factory,
    )

    assert status["target_scope"] == "current_run"
    assert status["target_chapters"] == 5
    assert status["completed_chapters"] == 2
    assert status["campaign_completed_chapters"] == 2
    assert status["remaining_chapters"] == 3
    assert status["approved_chapters"] == 1
    assert status["published_chapters"] == 1
    assert status["accepted_chapters"] == 2
    assert status["manuscript_chapter_count"] == 4


@pytest.mark.asyncio
async def test_replacing_outline_marks_existing_artifacts_stale_without_deleting_them(
    db_factory,
):
    project = await _create_project(db_factory)
    async with db_factory() as db:
        extra, _ = record_outline_change(
            {},
            text="第一版大纲\n第一章 旧开端",
            filename="v1.md",
            world_bible_exists=False,
            outline_exists=False,
        )
        extra = mark_artifact_fresh(extra, "world_bible")
        extra = mark_artifact_fresh(extra, "outline")
        persisted = await db.get(Project, project.id)
        assert persisted is not None
        persisted.extra = extra
        db.add_all(
            [
                WorldBible(
                    project_id=project.id,
                    version=1,
                    content={"world_name": "保留的旧世界"},
                ),
                StorylineVolume(
                    project_id=project.id,
                    volume_no=1,
                    title="保留的旧卷",
                    target_chapters=10,
                ),
                Chapter(
                    project_id=project.id,
                    chapter_no=1,
                    title="保留的旧章节",
                    status="draft",
                ),
            ]
        )
        await db.commit()

        upload = UploadFile(
            file=io.BytesIO("第二版大纲\n第一章 全新开端".encode()),
            filename="v2.md",
        )
        result = await upload_outline(project.id, upload, db)
        status = await preparation_status(project.id, db)

        assert result["outline_changed"] is True
        assert result["preparation_stale"] is True
        assert set(result["stale_artifacts"]) == {"world_bible", "outline"}
        assert status.world_bible_exists is True
        assert status.outline_exists is True
        assert status.world_bible_ready is False
        assert status.outline_ready is False
        assert status.preparation_stale is True
        assert set(status.requires_regeneration) == {"world_bible", "outline"}
        assert status.outline_source_revision == 2
        assert await db.scalar(select(func.count(WorldBible.id))) == 1
        assert await db.scalar(select(func.count(StorylineVolume.id))) == 1
        assert await db.scalar(select(func.count(Chapter.id))) == 1


@pytest.mark.asyncio
async def test_chapter_quality_detail_exposes_versions_assessments_issues_and_revisions(
    db_factory,
):
    project = await _create_project(db_factory)
    async with db_factory() as db:
        chapter = Chapter(
            project_id=project.id,
            chapter_no=3,
            title="证据链",
            status="approved",
            word_count=1200,
        )
        db.add(chapter)
        await db.flush()
        v1 = ChapterVersion(
            chapter_id=chapter.id,
            version_no=1,
            content="draft",
            word_count=5,
            status="draft",
            created_by_agent="Drafter",
        )
        v2 = ChapterVersion(
            chapter_id=chapter.id,
            version_no=2,
            content="approved",
            word_count=8,
            status="approved",
            created_by_agent="ChiefEditor",
        )
        db.add_all([v1, v2])
        await db.flush()
        chapter.current_version_id = v2.id
        assessment = QualityAssessment(
            project_id=project.id,
            chapter_id=chapter.id,
            version_id=v2.id,
            idempotency_key="chapter-3-final",
            assessor="ChiefEditor",
            assessment_type="deterministic_gate",
            round_no=2,
            dimension_scores={"final": 91},
            overall_score=91,
            verdict="approved",
            passed=True,
            raw_result={"notes": "passed"},
        )
        db.add(assessment)
        await db.flush()
        issue = QualityIssue(
            assessment_id=assessment.id,
            project_id=project.id,
            chapter_id=chapter.id,
            version_id=v1.id,
            issue_fingerprint="a" * 64,
            source="critic",
            category="pacing",
            severity="medium",
            description="节奏偏慢",
            status="resolved",
            resolved_by_revision_id=uuid.uuid4(),
        )
        historical_open_issue = QualityIssue(
            assessment_id=assessment.id,
            project_id=project.id,
            chapter_id=chapter.id,
            version_id=v1.id,
            issue_fingerprint="b" * 64,
            source="continuity",
            category="timeline",
            severity="high",
            description="旧版本仍留有历史问题",
            status="open",
        )
        revision = RevisionAttempt(
            project_id=project.id,
            chapter_id=chapter.id,
            input_version_id=v1.id,
            output_version_id=v2.id,
            idempotency_key="chapter-3-revision-1",
            round_no=1,
            status="completed",
            trigger_issue_ids=["issue-1"],
            score_before=72,
            score_after=91,
        )
        db.add_all([issue, historical_open_issue, revision])
        await db.commit()

        detail = await chapter_quality_detail(project.id, 3, db)

        assert detail["chapter"]["current_version_id"] == str(v2.id)
        assert detail["summary"] == {
            "assessment_count": 1,
            "issue_count": 2,
            "open_issue_count": 0,
            "revision_attempt_count": 1,
            "latest_score": 91.0,
            "latest_verdict": "approved",
            "quality_passed": True,
        }
        assert [item["version_no"] for item in detail["version_refs"]] == [1, 2]
        # Assessment provenance remains complete even when its old-version open
        # issue no longer counts as an active blocker for the current version.
        assert set(detail["assessments"][0]["issue_ids"]) == {
            str(issue.id),
            str(historical_open_issue.id),
        }
        assert {item["status"] for item in detail["issues"]} == {"resolved", "open"}
        assert detail["revision_attempts"][0]["input_version_id"] == str(v1.id)
        assert detail["revision_attempts"][0]["output_version_id"] == str(v2.id)
