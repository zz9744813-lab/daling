"""Durability and ownership tests for the 24-hour production supervisor."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import app.db.models  # noqa: F401 - register every foreign-key target
import app.services.continuous_production as continuous_module
import pytest
import pytest_asyncio
from app.core.database import Base
from app.db.models.automation import ContinuousRun, ContinuousRunEvent
from app.db.models.chapter import Chapter, ChapterVersion
from app.db.models.project import Project
from app.db.models.quality import QualityAssessment
from app.db.models.session import AgentRun, ReviewQueueItem, WorkSession
from app.services.continuous_production import (
    DEFAULT_POLICY,
    ContinuousProductionService,
    ManualPipelineConflictError,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db_factory(tmp_path):
    database = tmp_path / "continuous.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _project(db_factory, title: str = "自动写作测试") -> Project:
    async with db_factory() as db:
        project = Project(title=title)
        db.add(project)
        await db.commit()
        return project


async def _run(
    db_factory,
    project_id: uuid.UUID,
    *,
    generation: int = 1,
    desired_state: str = "running",
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
) -> ContinuousRun:
    async with db_factory() as db:
        run = ContinuousRun(
            project_id=project_id,
            desired_state=desired_state,
            status="running" if desired_state == "running" else desired_state,
            generation=generation,
            policy=dict(DEFAULT_POLICY),
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
        )
        db.add(run)
        await db.commit()
        return run


def _policy(**overrides: Any) -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    policy.update(overrides)
    return policy


@pytest.mark.asyncio
async def test_status_derives_latest_metrics_from_accepted_current_versions(db_factory):
    project = await _project(db_factory, "authoritative status metrics")
    run = await _run(db_factory, project.id, desired_state="stopped")
    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        persisted.started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        persisted.metrics = {
            "last_chapter": 1,
            "last_score": 86,
            "last_word_count": 1000,
            "scored_chapters": 1,
            "average_score": 86,
        }
        for chapter_no, words, score in ((1, 1000, 86.0), (2, 2400, 91.0)):
            chapter = Chapter(
                project_id=project.id,
                chapter_no=chapter_no,
                title=f"chapter {chapter_no}",
                status="approved",
                word_count=words,
            )
            db.add(chapter)
            await db.flush()
            version = ChapterVersion(
                chapter_id=chapter.id,
                version_no=1,
                content="正文",
                word_count=words,
                status="approved",
            )
            db.add(version)
            await db.flush()
            chapter.current_version_id = version.id
            db.add(
                QualityAssessment(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    version_id=version.id,
                    idempotency_key=f"gate-{chapter_no}",
                    assessor="ChiefEditor",
                    assessment_type="deterministic_gate",
                    overall_score=score,
                    verdict="approved",
                    passed=True,
                )
            )
        await db.commit()

    status = await ContinuousProductionService().get_status(
        project.id,
        db_factory=db_factory,
    )

    assert status["metrics"]["last_chapter"] == 2
    assert status["metrics"]["last_word_count"] == 2400
    assert status["metrics"]["last_score"] == 91.0
    assert status["metrics"]["scored_chapters"] == 2
    assert status["metrics"]["average_score"] == 88.5


@pytest.mark.asyncio
async def test_duplicate_start_is_read_only_while_live_cycle_holds_sqlite(db_factory):
    project = await _project(db_factory, "重复启动幂等")
    run = await _run(db_factory, project.id, generation=4)
    service = ContinuousProductionService()
    blocker = asyncio.create_task(asyncio.Event().wait())
    service._tasks[project.id] = blocker
    try:
        status = await service.start(
            project.id,
            db_factory=db_factory,
            target_chapters=None,
            autonomy_level="L3",
            policy=dict(DEFAULT_POLICY),
        )

        assert status["run_id"] == str(run.id)
        assert status["generation"] == 4
        async with db_factory() as db:
            assert await db.scalar(select(func.count(ContinuousRunEvent.id))) == 0

        with pytest.raises(ManualPipelineConflictError):
            await service.start(
                project.id,
                db_factory=db_factory,
                autonomy_level="L3",
                policy={**DEFAULT_POLICY, "quality_threshold": 90},
            )
    finally:
        blocker.cancel()
        await asyncio.gather(blocker, return_exceptions=True)


@pytest.mark.asyncio
async def test_duplicate_start_respects_healthy_worker_lease_from_other_process(
    db_factory,
    monkeypatch,
):
    project = await _project(db_factory, "cross-process duplicate start")
    run = await _run(
        db_factory,
        project.id,
        generation=6,
        lease_owner="worker-from-another-process",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    service = ContinuousProductionService()
    spawned: list[int] = []
    monkeypatch.setattr(
        service,
        "_spawn",
        lambda project_id, run_id, generation, factory: spawned.append(generation),
    )

    status = await service.start(
        project.id,
        db_factory=db_factory,
        target_chapters=None,
        autonomy_level="L3",
        policy=dict(DEFAULT_POLICY),
    )

    assert status["run_id"] == str(run.id)
    assert status["generation"] == 6
    assert status["worker_alive"] is False
    assert spawned == []
    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        assert persisted.lease_owner == "worker-from-another-process"
        assert await db.scalar(select(func.count(ContinuousRunEvent.id))) == 0

    with pytest.raises(ManualPipelineConflictError):
        await service.start(
            project.id,
            db_factory=db_factory,
            autonomy_level="L3",
            policy={**DEFAULT_POLICY, "quality_threshold": 90},
        )


@pytest.mark.asyncio
async def test_restore_only_spawns_persisted_runs_with_recoverable_leases(
    db_factory,
    monkeypatch,
):
    expired_project = await _project(db_factory, "已过期租约")
    active_project = await _project(db_factory, "活跃租约")
    expired = await _run(
        db_factory,
        expired_project.id,
        generation=7,
        lease_owner="dead-worker",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    await _run(
        db_factory,
        active_project.id,
        generation=4,
        lease_owner="live-worker",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    service = ContinuousProductionService()
    spawned: list[tuple[uuid.UUID, uuid.UUID, int]] = []

    def capture_spawn(project_id, run_id, generation, factory):
        spawned.append((project_id, run_id, generation))

    monkeypatch.setattr(service, "_spawn", capture_spawn)

    recovered = await service.restore_active_runs(db_factory=db_factory)

    assert recovered == 1
    assert spawned == [(expired_project.id, expired.id, 7)]


@pytest.mark.asyncio
async def test_recovery_watchdog_periodically_rescans_leases(db_factory, monkeypatch):
    service = ContinuousProductionService()
    calls: list[object] = []

    async def no_wait(_seconds):
        return None

    async def capture_restore(db_factory=None):
        calls.append(db_factory)
        service._shutting_down = True
        return 0

    monkeypatch.setattr(continuous_module.asyncio, "sleep", no_wait)
    monkeypatch.setattr(service, "restore_active_runs", capture_restore)

    service.start_recovery_watchdog(db_factory, interval_seconds=1)
    assert service._recovery_watchdog_task is not None
    await service._recovery_watchdog_task

    assert calls == [db_factory]


@pytest.mark.asyncio
async def test_startup_reconciliation_closes_stale_and_duplicate_review_gates(db_factory):
    project = await _project(db_factory, "启动质量对账")
    async with db_factory() as db:
        approved = Chapter(
            project_id=project.id,
            chapter_no=1,
            title="已通过章节",
            status="approved",
        )
        unfinished = Chapter(
            project_id=project.id,
            chapter_no=2,
            title="待修章节",
            status="review",
        )
        db.add_all([approved, unfinished])
        await db.flush()
        from app.db.models.chapter import ChapterVersion

        approved_version = ChapterVersion(
            chapter_id=approved.id,
            version_no=1,
            content="已通过正文",
            status="approved",
        )
        unfinished_version = ChapterVersion(
            chapter_id=unfinished.id,
            version_no=1,
            content="待修正文",
            status="draft",
        )
        db.add_all([approved_version, unfinished_version])
        await db.flush()
        approved.current_version_id = approved_version.id
        unfinished.current_version_id = unfinished_version.id
        db.add_all(
            [
                ReviewQueueItem(
                    project_id=project.id,
                    artifact_type="chapter",
                    artifact_id=approved.id,
                    item_type="quality_gate",
                    title="旧的已通过门禁",
                    status="pending",
                ),
                ReviewQueueItem(
                    project_id=project.id,
                    artifact_type="chapter",
                    artifact_id=unfinished.id,
                    item_type="quality_gate",
                    title="待修门禁 A",
                    status="pending",
                ),
                ReviewQueueItem(
                    project_id=project.id,
                    artifact_type="chapter",
                    artifact_id=unfinished.id,
                    item_type="quality_gate",
                    title="待修门禁 B",
                    status="pending",
                ),
            ]
        )
        await db.commit()

    result = await ContinuousProductionService().reconcile_persisted_quality_state(db_factory)

    assert result["approved_reviews"] == 1
    assert result["duplicate_reviews"] == 1
    async with db_factory() as db:
        pending = list(
            (
                await db.scalars(
                    select(ReviewQueueItem).where(ReviewQueueItem.status == "pending")
                )
            ).all()
        )
        assert len(pending) == 1
        assert pending[0].artifact_id == unfinished.id


@pytest.mark.asyncio
async def test_startup_reconciliation_backfills_missing_approved_final_gate(db_factory):
    project = await _project(db_factory, "终审证据补账")
    async with db_factory() as db:
        session = WorkSession(
            project_id=project.id,
            title="人工精修复检",
            goal="补齐最终质量证据",
            status="completed",
            quality_threshold=85,
        )
        chapter = Chapter(
            project_id=project.id,
            chapter_no=3,
            title="第三章",
            status="approved",
        )
        db.add_all([session, chapter])
        await db.flush()
        from app.db.models.chapter import ChapterVersion

        version = ChapterVersion(
            chapter_id=chapter.id,
            version_no=7,
            content="通过真实复检的正文" * 100,
            word_count=900,
            status="approved",
        )
        db.add(version)
        await db.flush()
        chapter.current_version_id = version.id
        db.add_all(
            [
                QualityAssessment(
                    project_id=project.id,
                    session_id=session.id,
                    chapter_id=chapter.id,
                    version_id=version.id,
                    idempotency_key="critic-current",
                    assessor="Critic",
                    assessment_type="critic",
                    round_no=0,
                    rubric_version="critic-v1",
                    dimension_scores={"quality": 88},
                    overall_score=88,
                    verdict="revise",
                    passed=False,
                    raw_result={"overall_score": 88, "verdict": "revise", "issues": []},
                ),
                QualityAssessment(
                    project_id=project.id,
                    session_id=session.id,
                    chapter_id=chapter.id,
                    version_id=version.id,
                    idempotency_key="continuity-current",
                    assessor="ContinuityGuard",
                    assessment_type="continuity",
                    round_no=0,
                    rubric_version="continuity-v1",
                    dimension_scores={"continuity": 100},
                    overall_score=100,
                    verdict="pass",
                    passed=True,
                    raw_result={"passed": True, "conflicts": [], "warnings": []},
                ),
            ]
        )
        await db.commit()

    result = await ContinuousProductionService().reconcile_persisted_quality_state(db_factory)

    assert result["backfilled_final_gates"] == 1
    async with db_factory() as db:
        gate = await db.scalar(
            select(QualityAssessment).where(
                QualityAssessment.chapter_id == chapter.id,
                QualityAssessment.version_id == version.id,
                QualityAssessment.assessor == "ChiefEditor",
            )
        )
        assert gate is not None
        assert gate.passed is True
        assert gate.overall_score == 88


@pytest.mark.asyncio
async def test_later_rescan_recovers_lease_that_was_active_during_startup(
    db_factory,
    monkeypatch,
):
    project = await _project(db_factory, "启动后租约才过期")
    run = await _run(
        db_factory,
        project.id,
        generation=5,
        lease_owner="dead-process",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    service = ContinuousProductionService()
    spawned: list[tuple[uuid.UUID, uuid.UUID, int]] = []
    monkeypatch.setattr(
        service,
        "_spawn",
        lambda project_id, run_id, generation, factory: spawned.append(
            (project_id, run_id, generation)
        ),
    )

    assert await service.restore_active_runs(db_factory) == 0
    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        persisted.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    assert await service.restore_active_runs(db_factory) == 1
    assert spawned == [(project.id, run.id, 5)]


@pytest.mark.asyncio
async def test_fencing_token_rejects_old_worker_after_lease_takeover(db_factory):
    project = await _project(db_factory)
    run = await _run(db_factory, project.id, generation=3)
    first = ContinuousProductionService()
    second = ContinuousProductionService()

    first_token = await first._acquire_lease(run.id, 3, db_factory)
    assert first_token == 1
    assert await second._acquire_lease(run.id, 3, db_factory) is None

    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        persisted.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    second_token = await second._acquire_lease(run.id, 3, db_factory)
    assert second_token == 2

    old_result = await first._apply_outcome(
        project.id,
        run.id,
        3,
        first_token,
        db_factory,
        {"kind": "chapter", "chapter_no": 1, "score": 92, "word_count": 3000},
        _policy(chapter_delay_seconds=0),
    )
    assert old_result == (False, 0)

    new_result = await second._apply_outcome(
        project.id,
        run.id,
        3,
        second_token,
        db_factory,
        {"kind": "chapter", "chapter_no": 1, "score": 92, "word_count": 3000},
        _policy(chapter_delay_seconds=0),
    )
    assert new_result == (True, 0)

    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        assert persisted.completed_chapters == 1
        assert persisted.fencing_token == 2


@pytest.mark.asyncio
async def test_guard_epoch_does_not_compare_sqlite_naive_datetime_in_session(db_factory):
    """SQLite drops timezone info; bulk-update synchronization must stay database-side."""
    project = await _project(db_factory, "SQLite 时区租约")
    run = await _run(db_factory, project.id, generation=2)
    service = ContinuousProductionService()
    token = await service._acquire_lease(run.id, 2, db_factory)
    assert token is not None

    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        assert persisted.lease_expires_at is not None
        # Loading the row into the identity map reproduced the production crash:
        # SQLAlchemy's default evaluator compared this SQLite-naive value with
        # the UTC-aware timestamp used by the fencing predicate.
        assert persisted.lease_expires_at.tzinfo is None
        assert await service._guard_epoch(db, run.id, 2, token) is True
        await db.commit()


@pytest.mark.asyncio
async def test_guard_epoch_renews_expired_lease_when_fencing_token_is_still_owned(
    db_factory,
):
    """A slow LLM/SQLite transaction must not make its own worker disappear."""
    project = await _project(db_factory, "slow chapter")
    run = await _run(db_factory, project.id, generation=9)
    service = ContinuousProductionService()
    token = await service._acquire_lease(run.id, 9, db_factory)
    assert token is not None

    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        persisted.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.commit()

    async with db_factory() as db:
        assert await service._guard_epoch(db, run.id, 9, token) is True
        await db.commit()
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        assert persisted.lease_expires_at > datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_campaign_progress_reconciles_chapter_committed_before_acknowledgement(
    db_factory,
):
    project = await _project(db_factory, "reconcile committed chapter")
    run = await _run(db_factory, project.id, generation=4)
    async with db_factory() as db:
        persisted_run = await db.get(ContinuousRun, run.id)
        assert persisted_run is not None
        persisted_run.started_at = datetime.now(timezone.utc) - timedelta(days=1)
        chapter = Chapter(
            project_id=project.id,
            chapter_no=1,
            title="committed",
            status="approved",
            word_count=3200,
        )
        db.add(chapter)
        await db.flush()
        stale_review = ReviewQueueItem(
            project_id=project.id,
            item_type="quality_gate",
            artifact_type="chapter",
            artifact_id=chapter.id,
            title="older failed version",
            risk_level="high",
            status="pending",
            chapter_no=1,
        )
        db.add(stale_review)
        await db.commit()

    service = ContinuousProductionService()
    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        assert await service._reconcile_campaign_progress(db, persisted) == 1
        refreshed_review = await db.get(ReviewQueueItem, stale_review.id)
        assert refreshed_review is not None
        await db.refresh(refreshed_review)
        assert refreshed_review.status == "approved"
        assert refreshed_review.decided_by == "system"


@pytest.mark.asyncio
async def test_start_from_budget_hold_updates_policy_and_opens_fresh_circuit_window(
    db_factory,
    monkeypatch,
):
    project = await _project(db_factory, "resume after budget hold")
    run = await _run(db_factory, project.id, generation=6, desired_state="paused")
    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        persisted.status = "budget_hold"
        persisted.consecutive_failures = 3
        persisted.total_failures = 7
        persisted.last_error = "daily token limit reached"
        await db.commit()

    service = ContinuousProductionService()
    spawned: list[int] = []
    monkeypatch.setattr(
        service,
        "_spawn",
        lambda project_id, run_id, generation, factory: spawned.append(generation),
    )

    status = await service.start(
        project.id,
        db_factory=db_factory,
        target_chapters=3,
        policy={"daily_token_limit": 900_000},
    )

    assert status["desired_state"] == "running"
    assert status["generation"] == 7
    assert status["consecutive_failures"] == 0
    assert status["total_failures"] == 7
    assert status["last_error"] is None
    assert status["policy"]["daily_token_limit"] == 900_000
    assert spawned == [7]


@pytest.mark.asyncio
async def test_unexpected_worker_exit_restarts_from_persisted_intent(
    db_factory,
    monkeypatch,
):
    project = await _project(db_factory, "Worker 自愈")
    service = ContinuousProductionService()
    run = await _run(
        db_factory,
        project.id,
        generation=9,
        lease_owner=service._instance_id,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    async with db_factory() as db:
        session = WorkSession(
            project_id=project.id,
            title="被中断的第一章",
            goal="验证自动恢复",
            status="running",
        )
        db.add(session)
        await db.flush()
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        persisted.current_session_id = session.id
        persisted.current_chapter = 1
        await db.commit()
        stale_session_id = session.id

    async def no_wait(_seconds):
        return None

    spawned: list[tuple[uuid.UUID, uuid.UUID, int]] = []
    monkeypatch.setattr(continuous_module.asyncio, "sleep", no_wait)
    monkeypatch.setattr(
        service,
        "_spawn",
        lambda project_id, run_id, generation, factory: spawned.append(
            (project_id, run_id, generation)
        ),
    )

    await service._recover_after_worker_exit(
        project.id,
        run.id,
        9,
        db_factory,
        cancelled=True,
        error=None,
    )

    assert spawned == [(project.id, run.id, 9)]
    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        stale_session = await db.get(WorkSession, stale_session_id)
        assert persisted is not None
        assert persisted.desired_state == "running"
        assert persisted.status == "recovering"
        assert persisted.lease_owner is None
        assert persisted.current_session_id is None
        assert stale_session is not None
        assert stale_session.status == "paused"
        event = await db.scalar(
            select(ContinuousRunEvent).where(
                ContinuousRunEvent.run_id == run.id,
                ContinuousRunEvent.event_type == "worker_restarted",
            )
        )
        assert event is not None


@pytest.mark.asyncio
async def test_stale_exit_callback_cannot_clear_replacement_worker_lease(
    db_factory,
    monkeypatch,
):
    project = await _project(db_factory, "replacement worker ownership")
    service = ContinuousProductionService()
    run = await _run(
        db_factory,
        project.id,
        generation=11,
        lease_owner=service._instance_id,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    async with db_factory() as db:
        session = WorkSession(
            project_id=project.id,
            title="replacement cycle",
            goal="preserve replacement ownership",
            status="running",
        )
        db.add(session)
        await db.flush()
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        persisted.current_session_id = session.id
        persisted.current_chapter = 2
        await db.commit()
        session_id = session.id

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(continuous_module.asyncio, "sleep", no_wait)
    replacement = asyncio.create_task(asyncio.Event().wait())
    service._tasks[project.id] = replacement
    try:
        await service._recover_after_worker_exit(
            project.id,
            run.id,
            11,
            db_factory,
            cancelled=False,
            error=RuntimeError("old worker exited"),
        )
    finally:
        replacement.cancel()
        await asyncio.gather(replacement, return_exceptions=True)

    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        work_session = await db.get(WorkSession, session_id)
        assert persisted is not None
        assert persisted.lease_owner == service._instance_id
        assert persisted.current_session_id == session_id
        assert persisted.current_chapter == 2
        assert work_session is not None
        assert work_session.status == "running"
        assert (
            await db.scalar(
                select(func.count(ContinuousRunEvent.id)).where(
                    ContinuousRunEvent.run_id == run.id,
                    ContinuousRunEvent.event_type == "worker_restarted",
                )
            )
            == 0
        )


@pytest.mark.asyncio
async def test_heartbeat_retries_transient_database_error(monkeypatch):
    service = ContinuousProductionService()
    run_id = uuid.uuid4()
    calls = 0

    async def no_wait(_seconds):
        return None

    class NoOwnershipResult:
        rowcount = 0

    class FakeDb:
        async def execute(self, _statement):
            return NoOwnershipResult()

        async def commit(self):
            return None

    class FactoryContext:
        async def __aenter__(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary database lock")
            return FakeDb()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(continuous_module.asyncio, "sleep", no_wait)

    await service._heartbeat_loop(
        run_id,
        generation=3,
        fencing_token=7,
        factory=FactoryContext,
    )

    assert calls == 2


@pytest.mark.asyncio
async def test_errors_back_off_then_open_circuit_and_auto_half_open(db_factory):
    project = await _project(db_factory)
    run = await _run(db_factory, project.id, generation=1)
    service = ContinuousProductionService()
    token = await service._acquire_lease(run.id, 1, db_factory)
    assert token is not None
    policy = _policy(
        error_backoff_seconds=2,
        max_consecutive_failures=2,
        circuit_cooldown_seconds=7,
    )

    keep_running, delay = await service._apply_outcome(
        project.id,
        run.id,
        1,
        token,
        db_factory,
        {"kind": "error", "chapter_no": 1, "error": "temporary"},
        policy,
    )
    assert keep_running is True
    assert delay == 2

    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        assert persisted.status == "retry_wait"
        assert persisted.consecutive_failures == 1
        assert persisted.next_run_at is not None

    keep_running, delay = await service._apply_outcome(
        project.id,
        run.id,
        1,
        token,
        db_factory,
        {"kind": "error", "chapter_no": 1, "error": "still broken"},
        policy,
    )
    assert (keep_running, delay) == (True, 7)

    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        assert persisted.desired_state == "running"
        assert persisted.status == "circuit_open"
        assert persisted.consecutive_failures == 2
        assert persisted.next_run_at is not None
        assert persisted.metrics["circuit_open_count"] == 1
        events = list(
            (
                await db.execute(
                    select(ContinuousRunEvent).where(
                        ContinuousRunEvent.run_id == run.id,
                        ContinuousRunEvent.event_type == "circuit_open",
                    )
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].data["automatic_half_open"] is True


@pytest.mark.asyncio
async def test_review_result_never_advances_even_with_legacy_continue_policy(db_factory):
    project = await _project(db_factory)
    run = await _run(db_factory, project.id, generation=2)
    service = ContinuousProductionService()
    token = await service._acquire_lease(run.id, 2, db_factory)
    assert token is not None

    result = await service._apply_outcome(
        project.id,
        run.id,
        2,
        token,
        db_factory,
        {"kind": "quality_hold", "chapter_no": 8, "score": 79},
        _policy(quality_failure_action="continue"),
    )
    assert result == (False, 0)

    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        assert persisted.completed_chapters == 0
        assert persisted.desired_state == "paused"
        assert persisted.status == "quality_hold"


@pytest.mark.asyncio
async def test_quality_failure_retries_same_chapter_then_holds_without_advancing(db_factory):
    project = await _project(db_factory, "同章自动修复")
    run = await _run(db_factory, project.id, generation=3)
    service = ContinuousProductionService()
    token = await service._acquire_lease(run.id, 3, db_factory)
    assert token is not None
    policy = _policy(
        quality_failure_action="retry",
        max_quality_retry_cycles=2,
        quality_retry_backoff_seconds=7,
    )

    first = await service._apply_outcome(
        project.id,
        run.id,
        3,
        token,
        db_factory,
        {"kind": "quality_hold", "chapter_no": 2, "score": 81},
        policy,
    )
    second = await service._apply_outcome(
        project.id,
        run.id,
        3,
        token,
        db_factory,
        {"kind": "quality_hold", "chapter_no": 2, "score": 83},
        policy,
    )
    exhausted = await service._apply_outcome(
        project.id,
        run.id,
        3,
        token,
        db_factory,
        {"kind": "quality_hold", "chapter_no": 2, "score": 84},
        policy,
    )

    assert first == (True, 7)
    assert second == (True, 7)
    assert exhausted == (False, 0)
    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        assert persisted.completed_chapters == 0
        assert persisted.desired_state == "paused"
        assert persisted.status == "quality_hold"
        assert persisted.metrics["quality_retry_counts"]["2"] == 2
        events = list(
            (
                await db.scalars(
                    select(ContinuousRunEvent).where(
                        ContinuousRunEvent.run_id == run.id,
                        ContinuousRunEvent.event_type == "quality_retry_scheduled",
                    )
                )
            ).all()
        )
        assert [event.data["attempt"] for event in events] == [1, 2]
        assert all(event.data["strategy"] == "same_chapter_fresh_plan" for event in events)


@pytest.mark.asyncio
async def test_approved_chapter_clears_its_quality_retry_counter(db_factory):
    project = await _project(db_factory, "修复后通过")
    run = await _run(db_factory, project.id, generation=4)
    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        persisted.metrics = {"quality_retry_counts": {"2": 2, "3": 1}}
        await db.commit()
    service = ContinuousProductionService()
    token = await service._acquire_lease(run.id, 4, db_factory)
    assert token is not None

    result = await service._apply_outcome(
        project.id,
        run.id,
        4,
        token,
        db_factory,
        {"kind": "chapter", "chapter_no": 2, "score": 90, "word_count": 4200},
        _policy(chapter_delay_seconds=0),
    )

    assert result == (True, 0)
    async with db_factory() as db:
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        assert persisted.metrics["quality_retry_counts"] == {"3": 1}
        assert persisted.metrics["last_quality_retry_cycles"] == 2


@pytest.mark.asyncio
async def test_pause_resume_and_stop_invalidate_epoch_and_finish_sessions(
    db_factory,
    monkeypatch,
):
    project = await _project(db_factory)
    run = await _run(db_factory, project.id, generation=5)
    async with db_factory() as db:
        session = WorkSession(
            project_id=project.id,
            title="正在写第一章",
            goal="测试暂停",
            status="running",
        )
        db.add(session)
        await db.flush()
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        persisted.current_session_id = session.id
        persisted.current_chapter = 1
        persisted.consecutive_failures = 3
        persisted.total_failures = 5
        persisted.last_error = "previous circuit-breaker failure"
        await db.commit()
        first_session_id = session.id

    service = ContinuousProductionService()
    paused = await service.pause(project.id, db_factory=db_factory)
    assert paused["desired_state"] == "paused"
    assert paused["generation"] == 6
    async with db_factory() as db:
        first_session = await db.get(WorkSession, first_session_id)
        assert first_session is not None
        assert first_session.status == "paused"
        assert first_session.paused_reason == "用户暂停"

    spawned: list[int] = []
    monkeypatch.setattr(
        service,
        "_spawn",
        lambda project_id, run_id, generation, factory: spawned.append(generation),
    )
    resumed = await service.resume(project.id, db_factory=db_factory)
    assert resumed["desired_state"] == "running"
    assert resumed["generation"] == 7
    assert resumed["consecutive_failures"] == 0
    assert resumed["total_failures"] == 5
    assert resumed["last_error"] is None
    assert spawned == [7]

    async with db_factory() as db:
        second_session = WorkSession(
            project_id=project.id,
            title="正在写第二章",
            goal="测试停止",
            status="running",
        )
        db.add(second_session)
        await db.flush()
        persisted = await db.get(ContinuousRun, run.id)
        assert persisted is not None
        persisted.current_session_id = second_session.id
        persisted.current_chapter = 2
        await db.commit()
        second_session_id = second_session.id

    stopped = await service.stop(project.id, db_factory=db_factory)
    assert stopped["desired_state"] == "stopped"
    assert stopped["generation"] == 8
    async with db_factory() as db:
        second_session = await db.get(WorkSession, second_session_id)
        assert second_session is not None
        assert second_session.status == "failed"
        assert second_session.blocking_issues[-1]["type"] == "continuous_production_failed"


@pytest.mark.asyncio
async def test_failed_pipeline_marks_committed_work_session_failed(
    db_factory,
    monkeypatch,
):
    project = await _project(db_factory)
    run = await _run(db_factory, project.id, generation=1)
    async with db_factory() as db:
        db.add(
            Chapter(
                project_id=project.id,
                chapter_no=1,
                title="第一章",
                status="draft",
                word_count=0,
            )
        )
        await db.commit()

    class FailingOrchestrator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run_pipeline(self, **kwargs):
            return {"chapters": [{"chapter_no": 1, "status": "failed", "error": "模型不可用"}]}

    monkeypatch.setattr(continuous_module, "PipelineOrchestrator", FailingOrchestrator)
    service = ContinuousProductionService()
    token = await service._acquire_lease(run.id, 1, db_factory)
    assert token is not None

    outcome = await service._execute_cycle(
        project.id,
        run.id,
        1,
        token,
        db_factory,
        _policy(),
    )
    assert outcome["kind"] == "error"

    async with db_factory() as db:
        session = (
            (
                await db.execute(
                    select(WorkSession)
                    .where(WorkSession.project_id == project.id)
                    .order_by(WorkSession.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        assert session is not None
        assert session.status == "failed"
        assert session.blocking_issues[-1]["message"] == "模型不可用"


@pytest.mark.asyncio
async def test_resume_selects_review_chapter_even_when_it_has_words(db_factory):
    project = await _project(db_factory)
    async with db_factory() as db:
        db.add_all(
            [
                Chapter(
                    project_id=project.id,
                    chapter_no=1,
                    title="第一章",
                    status="approved",
                    word_count=3000,
                ),
                Chapter(
                    project_id=project.id,
                    chapter_no=2,
                    title="第二章",
                    status="review",
                    word_count=3100,
                ),
                Chapter(
                    project_id=project.id,
                    chapter_no=3,
                    title="第三章",
                    status="draft",
                    word_count=0,
                ),
            ]
        )
        await db.commit()

    service = ContinuousProductionService()
    async with db_factory() as db:
        assert await service._find_unfinished_chapter(db, project.id) == 2


@pytest.mark.asyncio
async def test_daily_usage_supports_cost_and_token_budget_holds(db_factory):
    project = await _project(db_factory)
    async with db_factory() as db:
        db.add(
            AgentRun(
                project_id=project.id,
                agent_name="Drafter",
                status="success",
                input_tokens=4000,
                output_tokens=6000,
                cost=1.25,
                result={"model": "priced-model"},
            )
        )
        await db.commit()

    service = ContinuousProductionService()
    async with db_factory() as db:
        usage = await service._daily_usage(db, project.id)

    assert usage["today_total_tokens"] == 10000
    assert usage["today_cost"] == 1.25
    assert "成本" in (service._budget_reason(_policy(daily_cost_limit=1.0), usage) or "")
    assert "Token" in (service._budget_reason(_policy(daily_token_limit=9000), usage) or "")
    assert service._budget_reason(_policy(daily_cost_limit=2.0), usage) is None
