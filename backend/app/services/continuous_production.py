"""Durable autonomous production supervisor.

This service stores operator intent, progress, heartbeats, leases, failures and
an append-only timeline in the database. A backend restart can therefore
recover runs whose desired state is still running.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.database import async_session_factory
from app.db.models.automation import ContinuousRun, ContinuousRunEvent
from app.db.models.chapter import Chapter
from app.db.models.project import Project
from app.db.models.session import AgentRun, ReviewQueueItem, WorkSession
from app.db.models.world import WorldBible
from app.model_gateway import gateway
from app.pipeline.orchestrator import PipelineOrchestrator

logger = logging.getLogger("app.services.continuous_production")

HEARTBEAT_SECONDS = 15
LEASE_SECONDS = 120
RECOVERY_SCAN_SECONDS = 15
RECENT_ERROR_LIMIT = 20

DEFAULT_POLICY: dict[str, Any] = {
    "quality_threshold": 85,
    "max_rewrite_rounds": 2,
    "chapter_delay_seconds": 5,
    "error_backoff_seconds": 30,
    "max_consecutive_failures": 3,
    # Transient-provider circuit breaker: keep the operator's 24H intent and
    # make a half-open probe after cooldown instead of remaining paused forever.
    "circuit_cooldown_seconds": 300,
    # A quality retry always regenerates the same unfinished chapter.  It
    # never advances without an approved, canonical version.
    "quality_failure_action": "retry",
    "max_quality_retry_cycles": 2,
    "quality_retry_backoff_seconds": 30,
    "learning_interval_chapters": 1,
    "daily_cost_limit": None,
    "daily_token_limit": None,
}


class ManualPipelineConflictError(RuntimeError):
    """Raised when autonomous and manual production would write concurrently."""

    def __init__(self, message: str, *, status: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _policy(raw: Optional[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(DEFAULT_POLICY)
    if raw:
        merged.update({key: value for key, value in raw.items() if key in merged})
    # A non-approved chapter has no canonical memory.  Legacy "continue"
    # meant advancing to the next chapter and is therefore converted into a
    # same-chapter repair cycle rather than preserved.
    action = str(merged.get("quality_failure_action") or "retry").lower()
    merged["quality_failure_action"] = action if action in {"pause", "retry"} else "retry"
    return merged


class ContinuousProductionService:
    """Supervise one durable autonomous production run per project."""

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._factories: dict[uuid.UUID, Callable[[], Any]] = {}
        self._task_generations: dict[uuid.UUID, int] = {}
        self._recovery_tasks: set[asyncio.Task[None]] = set()
        self._recovery_watchdog_task: Optional[asyncio.Task[None]] = None
        self._suppress_recovery: set[uuid.UUID] = set()
        self._shutting_down = False
        self._instance_id = f"worker-{uuid.uuid4().hex}"

    @asynccontextmanager
    async def manual_pipeline_guard(
        self,
        project_id: uuid.UUID,
        db: Any,
    ) -> AsyncIterator[None]:
        """Exclusively reserve a project for one manual production request.

        The reservation is persisted before an LLM call starts.  This makes it
        visible to other backend processes and also prevents autopilot from
        starting halfway through a manual bible, outline, or chapter run.
        """
        owner = await self._claim_manual_pipeline(project_id, db)
        try:
            yield
        except BaseException:
            await db.rollback()
            raise
        finally:
            await self._release_manual_pipeline(project_id, owner, db)

    async def _claim_manual_pipeline(self, project_id: uuid.UUID, db: Any) -> str:
        owner = f"manual-{uuid.uuid4().hex}"
        now = _utcnow()
        expires_at = now + timedelta(hours=12)

        for _ in range(2):
            project = await db.get(Project, project_id)
            if project is None:
                await db.rollback()
                raise ValueError("项目不存在")

            run = await db.scalar(
                select(ContinuousRun)
                .where(ContinuousRun.project_id == project_id)
                .with_for_update()
            )
            if run is None:
                run = ContinuousRun(
                    project_id=project_id,
                    desired_state="stopped",
                    status="stopped",
                    policy=dict(DEFAULT_POLICY),
                )
                db.add(run)
                try:
                    await db.commit()
                except IntegrityError:
                    # Another process created the one-per-project row first.
                    await db.rollback()
                continue

            if run.desired_state == "running":
                run_status = run.status
                await db.rollback()
                raise ManualPipelineConflictError(
                    "24 小时自动写作正在运行；请先暂停或停止后再执行手动 Pipeline",
                    status=run_status,
                )

            lease_expires = run.lease_expires_at
            if lease_expires is not None and lease_expires.tzinfo is None:
                lease_expires = lease_expires.replace(tzinfo=timezone.utc)
            manual_lease_active = bool(
                run.lease_owner
                and run.lease_owner.startswith("manual-")
                and (lease_expires is None or lease_expires >= now)
            )
            if manual_lease_active:
                await db.rollback()
                raise ManualPipelineConflictError(
                    "另一个手动 Pipeline 正在运行；请等待其完成",
                    status="manual_running",
                )

            run.status = "manual_running"
            run.lease_owner = owner
            run.lease_expires_at = expires_at
            run.last_heartbeat_at = now
            await db.commit()
            return owner

        raise RuntimeError("无法取得项目生产锁")

    async def _release_manual_pipeline(
        self,
        project_id: uuid.UUID,
        owner: str,
        db: Any,
    ) -> None:
        try:
            run = await db.scalar(
                select(ContinuousRun)
                .where(ContinuousRun.project_id == project_id)
                .with_for_update()
            )
            if run is not None and run.lease_owner == owner:
                run.status = run.desired_state
                run.lease_owner = None
                run.lease_expires_at = None
                await db.commit()
            else:
                await db.rollback()
        except Exception:  # noqa: BLE001
            await db.rollback()
            logger.exception("释放手动 Pipeline 生产锁失败: project_id=%s", project_id)
            raise

    async def start(
        self,
        project_id: uuid.UUID,
        db_factory: Optional[Callable[[], Any]] = None,
        target_chapters: Optional[int] = None,
        *,
        autonomy_level: str = "L3",
        policy: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Create or restart a durable autonomous run."""
        factory = db_factory or async_session_factory
        now = _utcnow()
        run_id: uuid.UUID
        generation: int
        already_running = False

        async with factory() as db:
            project = await db.get(Project, project_id)
            if project is None:
                raise ValueError("项目不存在")

            run = await db.scalar(
                select(ContinuousRun)
                .where(ContinuousRun.project_id == project_id)
                .with_for_update()
            )
            task = self._tasks.get(project_id)
            already_running = bool(
                run and run.desired_state == "running" and task is not None and not task.done()
            )

            if run is None:
                run = ContinuousRun(project_id=project_id)
                db.add(run)
                await db.flush()

            lease_expires = run.lease_expires_at
            if lease_expires is not None and lease_expires.tzinfo is None:
                lease_expires = lease_expires.replace(tzinfo=timezone.utc)
            if (
                run.lease_owner
                and run.lease_owner.startswith("manual-")
                and (lease_expires is None or lease_expires >= now)
            ):
                await db.rollback()
                raise ManualPipelineConflictError(
                    "手动 Pipeline 正在运行；请等待其完成后再启动 24 小时自动写作",
                    status="manual_running",
                )

            requested_policy = _policy(policy or run.policy)
            requested_target = (
                target_chapters if target_chapters is not None else run.target_chapters
            )
            healthy_persisted_worker = bool(
                run.desired_state == "running"
                and run.lease_owner
                and lease_expires is not None
                and lease_expires >= now
            )
            if already_running or healthy_persisted_worker:
                unchanged = (
                    requested_policy == _policy(run.policy)
                    and requested_target == run.target_chapters
                    and autonomy_level == run.autonomy_level
                )
                active_status = run.status
                await db.rollback()
                if unchanged:
                    return await self.get_status(project_id, db_factory=factory)
                raise ManualPipelineConflictError(
                    "当前章节周期正在执行；为保证策略快照一致，请先暂停再修改 24H 契约。",
                    status=active_status,
                )

            previous_state = run.desired_state
            new_campaign = previous_state == "stopped"
            if new_campaign:
                run.completed_chapters = 0
                run.consecutive_failures = 0
                run.total_failures = 0
                run.recent_errors = []
                run.last_error = None
                run.metrics = {}
                run.started_at = now
                run.generation += 1
            elif previous_state == "paused":
                # Resuming invalidates every worker from the prior execution
                # epoch while preserving already approved progress.
                run.generation += 1
                run.consecutive_failures = 0
                run.last_error = None

            run.desired_state = "running"
            if already_running:
                run.status = "running"
                event_type = "policy_updated"
                event_message = "自动写作策略已更新"
            elif previous_state == "running":
                run.status = "recovering"
                event_type = "run_recovering"
                event_message = "正在接管持久化的自动写作任务"
            elif previous_state == "paused":
                run.status = "recovering"
                event_type = "run_resumed"
                event_message = "自动写作已恢复"
            else:
                run.status = "starting"
                event_type = "run_started"
                event_message = "24 小时自动写作已启动"
            run.autonomy_level = autonomy_level
            if new_campaign or target_chapters is not None:
                run.target_chapters = target_chapters
            run.policy = requested_policy
            run.stopped_at = None
            run.next_run_at = now
            if previous_state in {"paused", "stopped"}:
                run.lease_owner = None
                run.lease_expires_at = None
            await self._event(
                db,
                run,
                event_type,
                event_message,
                data={
                    "target_chapters": run.target_chapters,
                    "policy": run.policy,
                    "generation": run.generation,
                },
            )
            await db.commit()
            run_id = run.id
            generation = run.generation

        self._factories[project_id] = factory
        if not already_running:
            self._spawn(project_id, run_id, generation, factory)
        return await self.get_status(project_id, db_factory=factory)

    async def pause(
        self,
        project_id: uuid.UUID,
        reason: str = "用户暂停",
        db_factory: Optional[Callable[[], Any]] = None,
    ) -> dict[str, Any]:
        factory = db_factory or self._factories.get(project_id) or async_session_factory
        # SQLite permits only one writer.  Cancelling the local chapter first
        # releases any open write transaction so the durable pause can commit.
        self._suppress_recovery.add(project_id)
        await self._cancel_task(project_id)
        session_id: Optional[uuid.UUID] = None
        async with factory() as db:
            run = await self._get_run(db, project_id)
            if run is None:
                self._suppress_recovery.discard(project_id)
                return self._empty_status(project_id)
            if run.desired_state == "paused":
                self._suppress_recovery.discard(project_id)
                return await self.get_status(project_id, db_factory=factory)
            if run.desired_state == "stopped":
                self._suppress_recovery.discard(project_id)
                return await self.get_status(project_id, db_factory=factory)
            session_id = run.current_session_id
            run.generation += 1
            run.desired_state = "paused"
            run.status = "paused"
            run.current_session_id = None
            run.current_chapter = None
            run.next_run_at = None
            run.lease_owner = None
            run.lease_expires_at = None
            await self._event(
                db,
                run,
                "run_paused",
                reason,
                severity="warning",
                data={"generation": run.generation},
            )
            await db.commit()
        await self._finish_work_session(
            factory,
            session_id,
            status="paused",
            reason=reason,
        )
        self._suppress_recovery.discard(project_id)
        return await self.get_status(project_id, db_factory=factory)

    async def resume(
        self,
        project_id: uuid.UUID,
        db_factory: Optional[Callable[[], Any]] = None,
    ) -> dict[str, Any]:
        factory = db_factory or self._factories.get(project_id) or async_session_factory
        task = self._tasks.get(project_id)
        if task is not None and not task.done():
            return await self.get_status(project_id, db_factory=factory)
        async with factory() as db:
            run = await self._get_run(db, project_id)
            if run is None:
                raise ValueError("连续写作任务不存在，请先启动")
            if run.desired_state == "stopped":
                raise ValueError("连续写作已停止，请重新启动新任务")
            if run.desired_state == "running":
                generation = run.generation
                run_id = run.id
                await db.commit()
                self._factories[project_id] = factory
                self._spawn(project_id, run_id, generation, factory)
                return await self.get_status(project_id, db_factory=factory)
            run.generation += 1
            run.desired_state = "running"
            run.status = "recovering"
            run.stopped_at = None
            run.next_run_at = _utcnow()
            # A deliberate operator resume starts a fresh circuit-breaker window.
            # Keep the immutable error/event history and total failure counter for
            # audit, but do not let the failure that caused the previous pause make
            # the very next transient error trip the breaker immediately.
            run.consecutive_failures = 0
            run.last_error = None
            run.lease_owner = None
            run.lease_expires_at = None
            await self._event(
                db,
                run,
                "run_resumed",
                "自动写作已恢复",
                data={"generation": run.generation},
            )
            await db.commit()
            run_id = run.id
            generation = run.generation
        self._factories[project_id] = factory
        self._spawn(project_id, run_id, generation, factory)
        return await self.get_status(project_id, db_factory=factory)

    async def stop(
        self,
        project_id: uuid.UUID,
        db_factory: Optional[Callable[[], Any]] = None,
    ) -> dict[str, Any]:
        factory = db_factory or self._factories.get(project_id) or async_session_factory
        self._suppress_recovery.add(project_id)
        await self._cancel_task(project_id)
        session_id: Optional[uuid.UUID] = None
        async with factory() as db:
            run = await self._get_run(db, project_id)
            if run is None:
                self._suppress_recovery.discard(project_id)
                return self._empty_status(project_id)
            if run.desired_state == "stopped":
                self._suppress_recovery.discard(project_id)
                return await self.get_status(project_id, db_factory=factory)
            session_id = run.current_session_id
            run.generation += 1
            run.desired_state = "stopped"
            run.status = "stopped"
            run.current_chapter = None
            run.current_session_id = None
            run.next_run_at = None
            run.stopped_at = _utcnow()
            run.lease_owner = None
            run.lease_expires_at = None
            await self._event(
                db,
                run,
                "run_stopped",
                "自动写作已停止",
                data={"generation": run.generation},
            )
            await db.commit()
        await self._finish_work_session(
            factory,
            session_id,
            status="failed",
            reason="用户停止自动写作",
        )
        self._suppress_recovery.discard(project_id)
        return await self.get_status(project_id, db_factory=factory)

    async def get_status(
        self,
        project_id: uuid.UUID,
        *,
        db_factory: Optional[Callable[[], Any]] = None,
    ) -> dict[str, Any]:
        factory = db_factory or self._factories.get(project_id) or async_session_factory
        async with factory() as db:
            chapter_counts = await self._chapter_status_counts(db, project_id)
            run = await self._get_run(db, project_id)
            if run is None:
                return self._empty_status(project_id, chapter_counts)
            metrics = dict(run.metrics or {})
            # Chapter publication and supervisor acknowledgement intentionally use
            # separate transactions.  A later review can also promote a stronger
            # immutable version after the worker has stopped.  Always overlay the
            # operator-facing summary with the accepted current versions so stale
            # worker metrics never misreport the latest chapter, score, or length.
            metrics.update(await self._accepted_quality_metrics(db, run))
            task = self._tasks.get(project_id)
            worker_alive = task is not None and not task.done()
            heartbeat_stale = False
            if run.desired_state == "running" and run.last_heartbeat_at:
                heartbeat = run.last_heartbeat_at
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                heartbeat_stale = (_utcnow() - heartbeat).total_seconds() > LEASE_SECONDS
            remaining_chapters = (
                max(0, run.target_chapters - run.completed_chapters)
                if run.target_chapters is not None
                else None
            )
            return {
                "run_id": str(run.id),
                "project_id": str(project_id),
                "running": run.desired_state == "running"
                and run.status
                in {
                    "starting",
                    "recovering",
                    "running",
                    "retry_wait",
                    "circuit_open",
                    "half_open",
                },
                "desired_state": run.desired_state,
                "status": run.status,
                "worker_alive": worker_alive,
                "heartbeat_stale": heartbeat_stale,
                "current_chapter": run.current_chapter,
                # Backward-compatible campaign counter.  It is intentionally
                # separate from manuscript-wide approved/published totals.
                "completed_chapters": run.completed_chapters,
                "campaign_completed_chapters": run.completed_chapters,
                "target_chapters": run.target_chapters,
                "target_scope": "current_run",
                "remaining_chapters": remaining_chapters,
                **chapter_counts,
                "autonomy_level": run.autonomy_level,
                "generation": run.generation,
                "fencing_token": run.fencing_token,
                "consecutive_failures": run.consecutive_failures,
                "total_failures": run.total_failures,
                "last_error": run.last_error,
                "errors": list(run.recent_errors or [])[-10:],
                "policy": _policy(run.policy),
                "metrics": metrics,
                "started_at": _iso(run.started_at),
                "stopped_at": _iso(run.stopped_at),
                "last_heartbeat_at": _iso(run.last_heartbeat_at),
                "next_run_at": _iso(run.next_run_at),
            }

    async def list_events(
        self,
        project_id: uuid.UUID,
        limit: int = 100,
        *,
        db_factory: Optional[Callable[[], Any]] = None,
    ) -> list[dict[str, Any]]:
        factory = db_factory or self._factories.get(project_id) or async_session_factory
        async with factory() as db:
            result = await db.execute(
                select(ContinuousRunEvent)
                .where(ContinuousRunEvent.project_id == project_id)
                .order_by(ContinuousRunEvent.created_at.desc())
                .limit(limit)
            )
            events = result.scalars().all()
            return [
                {
                    "id": str(event.id),
                    "run_id": str(event.run_id),
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "chapter_no": event.chapter_no,
                    "message": event.message,
                    "data": event.data,
                    "created_at": _iso(event.created_at),
                }
                for event in events
            ]

    async def reconcile_persisted_quality_state(
        self,
        db_factory: Optional[Callable[[], Any]] = None,
    ) -> dict[str, int]:
        """Repair stale review/issue state left by older application versions.

        The ledger remains append-only; only lifecycle status is reconciled.
        Approved chapters close their pending gates, old-version open issues are
        superseded, and at most one pending gate remains per unfinished chapter.
        """
        from app.db.models.quality import QualityAssessment
        from app.services.quality_ledger import QualityLedger

        factory = db_factory or async_session_factory
        counts = {
            "approved_reviews": 0,
            "duplicate_reviews": 0,
            "superseded_issues": 0,
            "reactivated_issues": 0,
            "resolved_issues": 0,
            "backfilled_final_gates": 0,
        }
        now = _utcnow()
        async with factory() as db:
            chapters = list(
                (
                    await db.scalars(
                        select(Chapter).where(Chapter.current_version_id.is_not(None))
                    )
                ).all()
            )
            chapter_by_id = {chapter.id: chapter for chapter in chapters}
            for chapter in chapters:
                ledger = QualityLedger(db, chapter.project_id)
                lifecycle = await ledger.sync_chapter_issue_statuses(
                    chapter_id=chapter.id,
                    current_version_id=chapter.current_version_id,
                    approved=chapter.status in {"approved", "published"},
                )
                counts["superseded_issues"] += lifecycle["superseded"]
                counts["reactivated_issues"] += lifecycle["reactivated"]
                counts["resolved_issues"] += lifecycle["resolved"]
                if chapter.status not in {"approved", "published"}:
                    continue
                final_gate_exists = await db.scalar(
                    select(QualityAssessment.id)
                    .where(
                        QualityAssessment.chapter_id == chapter.id,
                        QualityAssessment.version_id == chapter.current_version_id,
                        QualityAssessment.assessor == "ChiefEditor",
                        QualityAssessment.assessment_type == "deterministic_gate",
                    )
                    .limit(1)
                )
                if final_gate_exists is not None:
                    continue
                evidence = list(
                    (
                        await db.scalars(
                            select(QualityAssessment)
                            .where(
                                QualityAssessment.chapter_id == chapter.id,
                                QualityAssessment.version_id == chapter.current_version_id,
                                QualityAssessment.assessment_type.in_(
                                    ["critic", "continuity"]
                                ),
                            )
                            .order_by(QualityAssessment.created_at.desc())
                        )
                    ).all()
                )
                latest_by_type: dict[str, Any] = {}
                for assessment in evidence:
                    latest_by_type.setdefault(assessment.assessment_type, assessment)
                critic = latest_by_type.get("critic")
                continuity = latest_by_type.get("continuity")
                if critic is None or continuity is None:
                    continue
                threshold = 85
                if critic.session_id is not None:
                    work_session = await db.get(WorkSession, critic.session_id)
                    if work_session is not None:
                        threshold = int(work_session.quality_threshold or threshold)
                score = float(critic.overall_score or 0)
                verdict = str(critic.verdict or "rewrite").lower()
                gate_passed = score >= threshold and verdict != "rewrite" and bool(
                    continuity.passed
                )
                if not gate_passed:
                    # A human override is valid evidence of its own, but must
                    # never be relabelled as an automatic deterministic pass.
                    continue
                await ledger.record_assessment(
                    idempotency_key=(
                        f"reconcile:chapter:{chapter.id}:"
                        f"version:{chapter.current_version_id}:final-gate"
                    ),
                    assessor="ChiefEditor",
                    assessment_type="deterministic_gate",
                    dimension_scores={"final": score},
                    overall_score=score,
                    verdict="approved",
                    passed=True,
                    issues=[],
                    raw_result={
                        "approved": True,
                        "final_score": score,
                        "chapter_id": str(chapter.id),
                        "chapter_no": chapter.chapter_no,
                        "reconciled_from": [str(critic.id), str(continuity.id)],
                    },
                    chapter_id=chapter.id,
                    version_id=chapter.current_version_id,
                    session_id=critic.session_id,
                    round_no=max(int(critic.round_no or 0), int(continuity.round_no or 0)) + 1,
                    rubric_version=f"threshold-{threshold}",
                )
                counts["backfilled_final_gates"] += 1

            pending = list(
                (
                    await db.scalars(
                        select(ReviewQueueItem)
                        .where(
                            ReviewQueueItem.status == "pending",
                            ReviewQueueItem.artifact_type == "chapter",
                            ReviewQueueItem.artifact_id.is_not(None),
                        )
                        .order_by(
                            ReviewQueueItem.created_at.desc(),
                            ReviewQueueItem.id.desc(),
                        )
                    )
                ).all()
            )
            newest_unfinished: set[uuid.UUID] = set()
            for item in pending:
                chapter = chapter_by_id.get(item.artifact_id)
                if chapter is not None and chapter.status in {"approved", "published"}:
                    item.status = "approved"
                    item.decided_by = "system"
                    item.decided_at = now
                    item.decision_notes = "章节当前版本已通过终审；启动对账自动关闭旧审阅项。"
                    counts["approved_reviews"] += 1
                    continue
                if item.artifact_id in newest_unfinished:
                    item.status = "revised"
                    item.decided_by = "system"
                    item.decided_at = now
                    item.decision_notes = "已由同一章节更新的审阅项取代。"
                    counts["duplicate_reviews"] += 1
                    continue
                newest_unfinished.add(item.artifact_id)
            await db.commit()
        return counts

    async def restore_active_runs(
        self,
        db_factory: Optional[Callable[[], Any]] = None,
    ) -> int:
        """Recover every run whose persisted operator intent is still running."""
        factory = db_factory or async_session_factory
        recovered: list[tuple[uuid.UUID, uuid.UUID, int, Optional[uuid.UUID]]] = []
        now = _utcnow()
        async with factory() as db:
            result = await db.execute(
                select(ContinuousRun).where(
                    ContinuousRun.desired_state == "running",
                    or_(
                        ContinuousRun.lease_owner.is_(None),
                        ContinuousRun.lease_expires_at.is_(None),
                        ContinuousRun.lease_expires_at < now,
                    ),
                )
            )
            for run in result.scalars().all():
                local_task = self._tasks.get(run.project_id)
                if local_task is not None and not local_task.done():
                    # A long SQLite write can delay heartbeat renewal. Never
                    # invalidate our own live worker; fencing is only needed
                    # when no local task owns the project anymore.
                    continue
                stale_session_id = run.current_session_id
                if run.status not in {"circuit_open", "retry_wait"}:
                    run.status = "recovering"
                run.current_session_id = None
                run.lease_owner = None
                run.lease_expires_at = None
                scheduled = run.next_run_at
                if scheduled is not None and scheduled.tzinfo is None:
                    scheduled = scheduled.replace(tzinfo=timezone.utc)
                if scheduled is None or scheduled <= now:
                    run.next_run_at = now
                await self._event(
                    db,
                    run,
                    "worker_recovering",
                    "检测到未完成的持久任务，正在安全接管",
                    severity="warning",
                    data={"generation": run.generation},
                )
                recovered.append((run.project_id, run.id, run.generation, stale_session_id))
            await db.commit()

        for project_id, run_id, generation, stale_session_id in recovered:
            await self._finish_work_session(
                factory,
                stale_session_id,
                status="paused",
                reason="写作进程中断；已回滚未发布内容并从最近安全点恢复",
            )
            self._factories[project_id] = factory
            self._spawn(project_id, run_id, generation, factory)
        if recovered:
            logger.info("已恢复 %d 个连续写作任务", len(recovered))
        return len(recovered)

    def start_recovery_watchdog(
        self,
        db_factory: Optional[Callable[[], Any]] = None,
        *,
        interval_seconds: float = RECOVERY_SCAN_SECONDS,
    ) -> None:
        """Continuously reclaim persisted runs after an old lease expires.

        A process may restart while the crashed process' lease still has a few
        seconds left.  A one-shot lifespan restore would skip that run forever;
        this lightweight watchdog closes that recovery gap without stealing a
        live local worker.
        """
        existing = self._recovery_watchdog_task
        if existing is not None and not existing.done():
            return
        self._shutting_down = False
        factory = db_factory or async_session_factory
        self._recovery_watchdog_task = asyncio.create_task(
            self._recovery_watchdog(
                factory,
                interval_seconds=max(1.0, float(interval_seconds)),
            )
        )

    async def _recovery_watchdog(
        self,
        factory: Callable[[], Any],
        *,
        interval_seconds: float,
    ) -> None:
        while not self._shutting_down:
            await asyncio.sleep(interval_seconds)
            if self._shutting_down:
                return
            try:
                await self.restore_active_runs(db_factory=factory)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("连续写作恢复巡检失败；下一周期将自动重试")

    async def shutdown(self) -> None:
        """Cancel local workers without changing persisted operator intent."""
        self._shutting_down = True
        watchdog = self._recovery_watchdog_task
        self._recovery_watchdog_task = None
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
        tasks = list(self._tasks.items())
        epochs = {project_id: self._task_generations.get(project_id) for project_id, _ in tasks}
        for _, task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        self._tasks.clear()
        self._task_generations.clear()
        for recovery_task in list(self._recovery_tasks):
            if not recovery_task.done():
                recovery_task.cancel()
        if self._recovery_tasks:
            await asyncio.gather(*self._recovery_tasks, return_exceptions=True)
        self._recovery_tasks.clear()

        for project_id, generation in epochs.items():
            if generation is None:
                continue
            factory = self._factories.get(project_id) or async_session_factory
            async with factory() as db:
                run = await db.scalar(
                    select(ContinuousRun).where(
                        ContinuousRun.project_id == project_id,
                        ContinuousRun.desired_state == "running",
                        ContinuousRun.generation == generation,
                        ContinuousRun.lease_owner.is_(None),
                    )
                )
                if run is not None:
                    if run.status not in {"circuit_open", "retry_wait"}:
                        run.status = "recovering"
                    scheduled = run.next_run_at
                    if scheduled is not None and scheduled.tzinfo is None:
                        scheduled = scheduled.replace(tzinfo=timezone.utc)
                    if scheduled is None or scheduled <= _utcnow():
                        run.next_run_at = _utcnow()
                    await self._event(
                        db,
                        run,
                        "worker_shutdown",
                        "写作服务已关闭，任务将在下次启动时恢复",
                        severity="warning",
                        data={"generation": generation},
                    )
                await db.commit()

    def _spawn(
        self,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        generation: int,
        factory: Callable[[], Any],
    ) -> None:
        existing = self._tasks.get(project_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._run_loop(project_id, run_id, generation, factory))
        self._tasks[project_id] = task
        self._task_generations[project_id] = generation

        def cleanup(done: asyncio.Task[None]) -> None:
            if self._tasks.get(project_id) is done:
                self._tasks.pop(project_id, None)
                self._task_generations.pop(project_id, None)
            if self._shutting_down or project_id in self._suppress_recovery:
                return
            recovery_task = asyncio.create_task(
                self._recover_after_worker_exit(
                    project_id,
                    run_id,
                    generation,
                    factory,
                    cancelled=done.cancelled(),
                    error=None if done.cancelled() else done.exception(),
                )
            )
            self._recovery_tasks.add(recovery_task)
            recovery_task.add_done_callback(self._recovery_tasks.discard)

        task.add_done_callback(cleanup)

    async def _recover_after_worker_exit(
        self,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        generation: int,
        factory: Callable[[], Any],
        *,
        cancelled: bool,
        error: Optional[BaseException],
    ) -> None:
        """Restart a vanished local worker when durable operator intent remains running."""
        await asyncio.sleep(1)
        if self._shutting_down or project_id in self._suppress_recovery:
            return
        # A replacement can be installed after the old task finishes but
        # before its done-callback gets CPU time.  Never let that stale
        # callback clear the replacement worker's lease/current session.  The
        # database fencing token protects cross-process ownership; this guard
        # closes the corresponding same-process lifecycle race.
        replacement = self._tasks.get(project_id)
        if replacement is not None and not replacement.done():
            return
        stale_session_id: Optional[uuid.UUID] = None
        async with factory() as db:
            run = await db.get(ContinuousRun, run_id)
            replacement = self._tasks.get(project_id)
            if replacement is not None and not replacement.done():
                return
            if (
                run is None
                or run.project_id != project_id
                or run.generation != generation
                or run.desired_state != "running"
            ):
                return
            lease_expires = run.lease_expires_at
            if lease_expires is not None and lease_expires.tzinfo is None:
                lease_expires = lease_expires.replace(tzinfo=timezone.utc)
            if (
                run.lease_owner
                and run.lease_owner != self._instance_id
                and lease_expires is not None
                and lease_expires > _utcnow()
            ):
                # A different process owns a healthy lease; it is responsible
                # for this run and the local worker must stay down.
                return
            stale_session_id = run.current_session_id
            if run.status not in {"circuit_open", "retry_wait"}:
                run.status = "recovering"
            run.current_session_id = None
            run.lease_owner = None
            run.lease_expires_at = None
            scheduled = run.next_run_at
            if scheduled is not None and scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            if scheduled is None or scheduled <= _utcnow():
                run.next_run_at = _utcnow()
            detail = (
                str(error)[:1000]
                if error
                else ("worker cancelled" if cancelled else "worker exited")
            )
            await self._event(
                db,
                run,
                "worker_restarted",
                "写作 Worker 意外退出，系统已自动从安全点重启",
                severity="warning",
                data={"generation": generation, "reason": detail},
            )
            await db.commit()
        await self._finish_work_session(
            factory,
            stale_session_id,
            status="paused",
            reason="Worker 意外退出；未发布事务已回滚，系统自动重试",
        )
        if self._shutting_down or project_id in self._suppress_recovery:
            return
        self._spawn(project_id, run_id, generation, factory)

    async def _cancel_task(self, project_id: uuid.UUID) -> None:
        task = self._tasks.get(project_id)
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_loop(
        self,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        generation: int,
        factory: Callable[[], Any],
    ) -> None:
        heartbeat_task: Optional[asyncio.Task[None]] = None
        fencing_token: Optional[int] = None
        try:
            fencing_token = await self._acquire_lease(run_id, generation, factory)
            if fencing_token is None:
                logger.warning("项目 %s 的连续写作租约已由其他 worker 持有", project_id)
                return
            acquired = await self._record_worker_acquired(
                run_id,
                generation,
                fencing_token,
                factory,
            )
            if not acquired:
                return
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(run_id, generation, fencing_token, factory)
            )
            await self._wait_for_persisted_schedule(
                run_id,
                generation,
                fencing_token,
                factory,
            )

            while True:
                async with factory() as db:
                    if not await self._guard_epoch(db, run_id, generation, fencing_token):
                        await db.rollback()
                        break
                    run = await db.get(ContinuousRun, run_id)
                    if run is None:
                        break
                    reconciled = await self._reconcile_campaign_progress(db, run)
                    if reconciled > run.completed_chapters:
                        previous = run.completed_chapters
                        run.completed_chapters = reconciled
                        await self._event(
                            db,
                            run,
                            "chapter_progress_reconciled",
                            "已从已通过终审的章节恢复连续写作进度",
                            severity="warning",
                            data={
                                "previous_completed_chapters": previous,
                                "reconciled_completed_chapters": reconciled,
                            },
                        )
                    if (
                        run.target_chapters is not None
                        and run.completed_chapters >= run.target_chapters
                    ):
                        run.desired_state = "stopped"
                        run.status = "completed"
                        run.current_chapter = None
                        run.current_session_id = None
                        run.next_run_at = None
                        run.stopped_at = _utcnow()
                        await self._event(
                            db,
                            run,
                            "target_reached",
                            f"已完成目标 {run.target_chapters} 章",
                        )
                        await db.commit()
                        break
                    was_circuit_open = run.status == "circuit_open"
                    run.status = "half_open" if was_circuit_open else "running"
                    run.next_run_at = None
                    policy = _policy(run.policy)
                    usage = await self._daily_usage(db, project_id)
                    metrics = dict(run.metrics or {})
                    metrics.update(usage)
                    run.metrics = metrics
                    budget_reason = self._budget_reason(policy, usage)
                    if budget_reason:
                        run.desired_state = "paused"
                        run.status = "budget_hold"
                        run.last_error = budget_reason
                        run.next_run_at = None
                        await self._event(
                            db,
                            run,
                            "budget_hold",
                            budget_reason,
                            severity="warning",
                            data=usage,
                        )
                        await db.commit()
                        return
                    if was_circuit_open:
                        await self._event(
                            db,
                            run,
                            "circuit_half_open",
                            "熔断冷却结束，正在执行一次受控恢复探测",
                            severity="warning",
                            data={
                                "consecutive_failures": run.consecutive_failures,
                                "circuit_open_count": int(
                                    metrics.get("circuit_open_count", 0) or 0
                                ),
                            },
                        )
                    await db.commit()

                outcome = await self._execute_cycle(
                    project_id,
                    run_id,
                    generation,
                    fencing_token,
                    factory,
                    policy,
                )
                keep_running, delay = await self._apply_outcome(
                    project_id,
                    run_id,
                    generation,
                    fencing_token,
                    factory,
                    outcome,
                    policy,
                )
                if not keep_running:
                    break
                if delay > 0:
                    await asyncio.sleep(delay)
        except asyncio.CancelledError:
            logger.info("项目 %s 的连续写作 worker 已取消", project_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("项目 %s 连续写作 supervisor 崩溃", project_id)
            if fencing_token is not None:
                await self._record_supervisor_failure(
                    run_id,
                    generation,
                    fencing_token,
                    factory,
                    exc,
                )
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            if fencing_token is not None:
                await self._release_lease(
                    run_id,
                    generation,
                    fencing_token,
                    factory,
                )

    async def _execute_cycle(
        self,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        generation: int,
        fencing_token: int,
        factory: Callable[[], Any],
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        session_id: Optional[uuid.UUID] = None
        chapter_no: Optional[int] = None
        try:
            async with factory() as db:
                if not await self._guard_epoch(
                    db,
                    run_id,
                    generation,
                    fencing_token,
                ):
                    await db.rollback()
                    return {"kind": "stopped"}
                run = await db.get(ContinuousRun, run_id)
                if run is None:
                    return {"kind": "stopped"}

                chapter_no = await self._find_unfinished_chapter(db, project_id)
                work_session = WorkSession(
                    project_id=project_id,
                    title="24h 自动写作",
                    goal="规划、起草、审校、纠错、终审并更新长期记忆",
                    mode=run.autonomy_level,
                    status="running",
                    session_type="continuous_production",
                    quality_threshold=int(policy["quality_threshold"]),
                    policy=policy,
                    target_params={"chapter_no": chapter_no},
                )
                db.add(work_session)
                await db.flush()
                session_id = work_session.id
                run.current_session_id = session_id
                run.current_chapter = chapter_no
                await db.commit()

                orchestrator = PipelineOrchestrator(
                    gateway=gateway,
                    db=db,
                    project_id=project_id,
                    session_id=session_id,
                    quality_threshold=int(policy["quality_threshold"]),
                    max_rewrite_rounds=int(policy["max_rewrite_rounds"]),
                    learning_interval_chapters=int(policy["learning_interval_chapters"]),
                    agent_run_db_factory=factory,
                )

                if chapter_no is None:
                    if not await self._has_world_bible(db, project_id):
                        await orchestrator.generate_bible(hints={})
                        generated_volumes = 0
                        next_action = "generate_outline"
                    else:
                        outline = await orchestrator.generate_outline(
                            volume_count=1,
                            chapters_per_volume=10,
                        )
                        generated_volumes = len(outline.get("volume_ids", []))
                        next_action = "write_next_chapter"
                    if not await self._guard_epoch(
                        db,
                        run_id,
                        generation,
                        fencing_token,
                    ):
                        await db.rollback()
                        await self._finish_work_session(
                            factory,
                            session_id,
                            status="paused",
                            reason="运行权已变更，放弃未发布的结构生成结果",
                        )
                        return {"kind": "stopped"}
                    work_session.status = "completed"
                    work_session.progress_percent = 100.0
                    work_session.next_action = {
                        "action": next_action,
                        "generated_volumes": generated_volumes,
                    }
                    await db.commit()
                    return {"kind": "prepared"}

                result = await orchestrator.run_pipeline(
                    target_chapters=1,
                    mode=run.autonomy_level,
                    start_chapter=chapter_no,
                )
                chapter = (result.get("chapters") or [{}])[0]
                if chapter.get("status") == "failed":
                    error = str(chapter.get("error", "章节生成失败"))
                    await db.rollback()
                    await self._finish_work_session(
                        factory,
                        session_id,
                        status="failed",
                        reason=error,
                    )
                    return {
                        "kind": "error",
                        "chapter_no": chapter_no,
                        "error": error,
                    }

                score = int(chapter.get("score") or 0)
                approved = chapter.get("status") == "approved"
                if not await self._guard_epoch(
                    db,
                    run_id,
                    generation,
                    fencing_token,
                ):
                    await db.rollback()
                    await self._finish_work_session(
                        factory,
                        session_id,
                        status="paused",
                        reason="运行权已变更，放弃未发布的章节结果",
                    )
                    return {"kind": "stopped"}

                work_session.status = "completed" if approved else "paused"
                work_session.progress_percent = 100.0
                work_session.current_score = score
                work_session.quality_passed = approved
                work_session.paused_reason = None if approved else "章节未通过自动质量闸门"
                work_session.current_artifact_type = "chapter"
                work_session.next_action = {
                    "action": "write_next_chapter" if approved else "quality_review",
                    "chapter_no": chapter_no,
                }
                await db.commit()
                return {
                    "kind": "chapter" if approved else "quality_hold",
                    "chapter_no": chapter_no,
                    "score": score,
                    "word_count": int(chapter.get("word_count") or 0),
                    "issues_count": int(chapter.get("issues_count") or 0),
                    "continuity_passed": bool(chapter.get("continuity_passed", False)),
                    "notes": chapter.get("notes", ""),
                }
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("项目 %s 自动写作周期失败", project_id)
            await self._finish_work_session(
                factory,
                session_id,
                status="failed",
                reason=f"{type(exc).__name__}: {exc}",
            )
            return {
                "kind": "error",
                "chapter_no": chapter_no,
                "session_id": str(session_id) if session_id else None,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def _apply_outcome(
        self,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        generation: int,
        fencing_token: int,
        factory: Callable[[], Any],
        outcome: dict[str, Any],
        policy: dict[str, Any],
    ) -> tuple[bool, float]:
        kind = outcome.get("kind")
        now = _utcnow()
        async with factory() as db:
            if not await self._guard_epoch(
                db,
                run_id,
                generation,
                fencing_token,
            ):
                await db.rollback()
                return False, 0
            run = await db.get(ContinuousRun, run_id)
            if run is None:
                return False, 0
            run.current_session_id = None
            run.current_chapter = None

            if kind == "prepared":
                delay = float(policy["chapter_delay_seconds"])
                run.consecutive_failures = 0
                run.last_error = None
                metrics = dict(run.metrics or {})
                metrics["circuit_open_count"] = 0
                run.metrics = metrics
                run.status = "running"
                run.next_run_at = now + timedelta(seconds=delay)
                await self._event(
                    db,
                    run,
                    "story_structure_extended",
                    "已生成新的卷章骨架，准备写下一章",
                )
                await db.commit()
                return True, delay

            if kind == "chapter":
                run.completed_chapters += 1
                run.consecutive_failures = 0
                run.last_error = None
                metrics = dict(run.metrics or {})
                retry_counts = dict(metrics.get("quality_retry_counts") or {})
                chapter_key = str(outcome.get("chapter_no"))
                completed_retry_cycles = int(retry_counts.pop(chapter_key, 0) or 0)
                previous_count = int(metrics.get("scored_chapters", 0))
                previous_average = float(metrics.get("average_score", 0))
                score = int(outcome.get("score", 0))
                metrics.update(
                    {
                        "last_chapter": outcome.get("chapter_no"),
                        "last_score": score,
                        "last_word_count": outcome.get("word_count", 0),
                        "scored_chapters": previous_count + 1,
                        "average_score": round(
                            (previous_average * previous_count + score) / (previous_count + 1),
                            2,
                        ),
                        "quality_retry_counts": retry_counts,
                        "last_quality_retry_cycles": completed_retry_cycles,
                        "circuit_open_count": 0,
                    }
                )
                metrics.update(await self._daily_usage(db, project_id))
                run.metrics = metrics
                delay = float(policy["chapter_delay_seconds"])
                run.status = "running"
                run.next_run_at = now + timedelta(seconds=delay)
                await self._event(
                    db,
                    run,
                    "chapter_completed",
                    f"第 {outcome.get('chapter_no')} 章已通过终审",
                    chapter_no=outcome.get("chapter_no"),
                    data=outcome,
                )
                await db.commit()
                return True, delay

            if kind == "quality_hold":
                # Keep repairing this exact unfinished chapter.  The next
                # chapter is never selected until ChiefEditor has approved the
                # current one and MemoryKeeper has committed its canon.
                run.consecutive_failures = 0
                action = str(policy.get("quality_failure_action") or "retry")
                max_cycles = max(0, int(policy.get("max_quality_retry_cycles") or 0))
                metrics = dict(run.metrics or {})
                retry_counts = dict(metrics.get("quality_retry_counts") or {})
                chapter_no = int(outcome.get("chapter_no") or 0)
                chapter_key = str(chapter_no)
                attempt = int(retry_counts.get(chapter_key, 0) or 0) + 1
                if action == "retry" and attempt <= max_cycles:
                    retry_counts[chapter_key] = attempt
                    metrics["quality_retry_counts"] = retry_counts
                    metrics["last_score"] = int(outcome.get("score") or 0)
                    metrics["last_chapter"] = chapter_no
                    metrics.update(await self._daily_usage(db, project_id))
                    run.metrics = metrics
                    delay = max(
                        0.0,
                        float(policy.get("quality_retry_backoff_seconds") or 0),
                    )
                    run.consecutive_failures = 0
                    run.last_error = (
                        f"第 {chapter_no} 章终审分数 {outcome.get('score')}；"
                        f"正在安排自动修复 {attempt}/{max_cycles}"
                    )
                    run.status = "retry_wait"
                    run.next_run_at = now + timedelta(seconds=delay)
                    await self._event(
                        db,
                        run,
                        "quality_retry_scheduled",
                        run.last_error,
                        severity="warning",
                        chapter_no=chapter_no,
                        data={
                            **outcome,
                            "attempt": attempt,
                            "max_attempts": max_cycles,
                            "strategy": "same_chapter_fresh_plan",
                        },
                    )
                    await db.commit()
                    return True, delay

                run.desired_state = "paused"
                run.status = "quality_hold"
                run.last_error = (
                    f"第 {outcome.get('chapter_no')} 章终审分数 "
                    f"{outcome.get('score')}；自动修复已用尽，需要人工确认"
                    if action == "retry"
                    else (
                        f"第 {outcome.get('chapter_no')} 章终审分数 "
                        f"{outcome.get('score')}，需要人工确认"
                    )
                )
                run.next_run_at = None
                await self._event(
                    db,
                    run,
                    "quality_hold",
                    run.last_error,
                    severity="warning",
                    chapter_no=outcome.get("chapter_no"),
                    data=outcome,
                )
                await db.commit()
                return False, 0

            if kind == "stopped":
                return False, 0

            error = str(outcome.get("error") or "未知错误")
            run.consecutive_failures += 1
            run.total_failures += 1
            run.last_error = error
            errors = list(run.recent_errors or [])
            errors.append(
                {
                    "at": now.isoformat(),
                    "chapter_no": outcome.get("chapter_no"),
                    "message": error,
                }
            )
            run.recent_errors = errors[-RECENT_ERROR_LIMIT:]
            max_failures = int(policy["max_consecutive_failures"])
            if run.consecutive_failures >= max_failures:
                metrics = dict(run.metrics or {})
                circuit_count = int(metrics.get("circuit_open_count", 0) or 0) + 1
                metrics["circuit_open_count"] = circuit_count
                run.metrics = metrics
                base_cooldown = max(1, float(policy["circuit_cooldown_seconds"]))
                cooldown = min(base_cooldown * (2 ** (circuit_count - 1)), 3600)
                run.status = "circuit_open"
                run.next_run_at = now + timedelta(seconds=cooldown)
                await self._event(
                    db,
                    run,
                    "circuit_open",
                    (
                        f"连续失败 {run.consecutive_failures} 次，熔断保护已开启；"
                        f"将在 {int(cooldown)} 秒后自动半开探测"
                    ),
                    severity="error",
                    chapter_no=outcome.get("chapter_no"),
                    data={
                        "error": error,
                        "cooldown_seconds": cooldown,
                        "circuit_open_count": circuit_count,
                        "automatic_half_open": True,
                    },
                )
                await db.commit()
                return True, cooldown

            delay = float(policy["error_backoff_seconds"]) * (2 ** (run.consecutive_failures - 1))
            delay = min(delay, 3600)
            run.status = "retry_wait"
            run.next_run_at = now + timedelta(seconds=delay)
            await self._event(
                db,
                run,
                "retry_scheduled",
                f"写作失败，将在 {int(delay)} 秒后重试",
                severity="warning",
                chapter_no=outcome.get("chapter_no"),
                data={"error": error, "attempt": run.consecutive_failures},
            )
            await db.commit()
            return True, delay

    async def _acquire_lease(
        self,
        run_id: uuid.UUID,
        generation: int,
        factory: Callable[[], Any],
    ) -> Optional[int]:
        now = _utcnow()
        expires = now + timedelta(seconds=LEASE_SECONDS)
        async with factory() as db:
            result = await db.execute(
                update(ContinuousRun)
                .execution_options(synchronize_session=False)
                .where(
                    ContinuousRun.id == run_id,
                    ContinuousRun.desired_state == "running",
                    ContinuousRun.generation == generation,
                    or_(
                        ContinuousRun.lease_owner.is_(None),
                        ContinuousRun.lease_owner == self._instance_id,
                        ContinuousRun.lease_expires_at < now,
                    ),
                )
                .values(
                    lease_owner=self._instance_id,
                    lease_expires_at=expires,
                    last_heartbeat_at=now,
                    fencing_token=ContinuousRun.fencing_token + 1,
                )
            )
            if not result.rowcount:
                await db.rollback()
                return None
            token = await db.scalar(
                select(ContinuousRun.fencing_token).where(
                    ContinuousRun.id == run_id,
                    ContinuousRun.generation == generation,
                    ContinuousRun.lease_owner == self._instance_id,
                )
            )
            await db.commit()
            return int(token) if token is not None else None

    async def _guard_epoch(
        self,
        db: Any,
        run_id: uuid.UUID,
        generation: int,
        fencing_token: int,
    ) -> bool:
        """Renew the run row only when this worker still owns the fenced epoch.

        The expiry timestamp is intentionally not part of this predicate.  A
        long SQLite write transaction can delay the heartbeat past the lease
        deadline.  Ownership is still safe because any legitimate takeover
        increments ``fencing_token``; whichever worker writes first wins, and
        the stale token is rejected afterwards.
        """
        now = _utcnow()
        result = await db.execute(
            update(ContinuousRun)
            .execution_options(synchronize_session=False)
            .where(
                ContinuousRun.id == run_id,
                ContinuousRun.desired_state == "running",
                ContinuousRun.generation == generation,
                ContinuousRun.fencing_token == fencing_token,
                ContinuousRun.lease_owner == self._instance_id,
            )
            .values(
                last_heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            )
        )
        return bool(result.rowcount)

    async def _record_worker_acquired(
        self,
        run_id: uuid.UUID,
        generation: int,
        fencing_token: int,
        factory: Callable[[], Any],
    ) -> bool:
        async with factory() as db:
            if not await self._guard_epoch(db, run_id, generation, fencing_token):
                await db.rollback()
                return False
            run = await db.get(ContinuousRun, run_id)
            if run is None:
                await db.rollback()
                return False
            if run.status not in {"circuit_open", "retry_wait"}:
                run.status = "running"
            await self._event(
                db,
                run,
                "worker_acquired",
                "写作 Worker 已取得运行权",
                data={
                    "generation": generation,
                    "fencing_token": fencing_token,
                    "worker": self._instance_id,
                },
            )
            await db.commit()
            return True

    async def _wait_for_persisted_schedule(
        self,
        run_id: uuid.UUID,
        generation: int,
        fencing_token: int,
        factory: Callable[[], Any],
    ) -> None:
        """Honor durable backoff/circuit timing after a process restart."""
        async with factory() as db:
            if not await self._guard_epoch(db, run_id, generation, fencing_token):
                await db.rollback()
                return
            run = await db.get(ContinuousRun, run_id)
            scheduled = run.next_run_at if run is not None else None
            if scheduled is not None and scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            delay = max(0.0, (scheduled - _utcnow()).total_seconds()) if scheduled else 0.0
            await db.commit()
        if delay > 0:
            await asyncio.sleep(delay)

    async def _heartbeat_loop(
        self,
        run_id: uuid.UUID,
        generation: int,
        fencing_token: int,
        factory: Callable[[], Any],
    ) -> None:
        transient_failures = 0
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            now = _utcnow()
            try:
                async with factory() as db:
                    result = await db.execute(
                        update(ContinuousRun)
                        .execution_options(synchronize_session=False)
                        .where(
                            ContinuousRun.id == run_id,
                            ContinuousRun.desired_state == "running",
                            ContinuousRun.generation == generation,
                            ContinuousRun.fencing_token == fencing_token,
                            ContinuousRun.lease_owner == self._instance_id,
                        )
                        .values(
                            last_heartbeat_at=now,
                            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                        )
                    )
                    await db.commit()
                    if not result.rowcount:
                        return
                    if transient_failures:
                        logger.info(
                            "连续写作心跳续租已恢复: run_id=%s generation=%s failures=%s",
                            run_id,
                            generation,
                            transient_failures,
                        )
                        transient_failures = 0
            except asyncio.CancelledError:
                raise
            except OperationalError as exc:
                # A short competing commit can still make SQLite reject one
                # heartbeat.  Retry, but do not flood a 24H production log with
                # the same multi-page traceback every 15 seconds.
                transient_failures += 1
                if transient_failures == 1 or transient_failures % 10 == 0:
                    logger.warning(
                        (
                            "连续写作心跳续租暂时失败，将自动重试: "
                            "run_id=%s generation=%s failures=%s error=%s"
                        ),
                        run_id,
                        generation,
                        transient_failures,
                        exc.orig if getattr(exc, "orig", None) else exc,
                    )
            except Exception:  # noqa: BLE001
                # Unknown connection/runtime failures retain a traceback for
                # diagnosis, while the loop remains alive for self-healing.
                transient_failures += 1
                logger.exception(
                    "连续写作心跳续租异常，将自动重试: run_id=%s generation=%s",
                    run_id,
                    generation,
                )

    async def _release_lease(
        self,
        run_id: uuid.UUID,
        generation: int,
        fencing_token: int,
        factory: Callable[[], Any],
    ) -> None:
        try:
            async with factory() as db:
                await db.execute(
                    update(ContinuousRun)
                    .execution_options(synchronize_session=False)
                    .where(
                        ContinuousRun.id == run_id,
                        ContinuousRun.generation == generation,
                        ContinuousRun.fencing_token == fencing_token,
                        ContinuousRun.lease_owner == self._instance_id,
                    )
                    .values(lease_owner=None, lease_expires_at=None)
                )
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("释放连续写作租约失败: run_id=%s", run_id)

    async def _finish_work_session(
        self,
        factory: Callable[[], Any],
        session_id: Optional[uuid.UUID],
        *,
        status: str,
        reason: str,
    ) -> None:
        """Persist the terminal state of a previously committed WorkSession."""
        if session_id is None:
            return
        async with factory() as db:
            session = await db.get(WorkSession, session_id)
            if session is None:
                return
            session.status = status
            session.quality_passed = False
            session.paused_reason = reason if status == "paused" else None
            next_action = dict(session.next_action or {})
            next_action.update(
                {
                    "action": "resume" if status == "paused" else "retry",
                    "reason": reason,
                }
            )
            session.next_action = next_action
            if status == "failed":
                issues = list(session.blocking_issues or [])
                issues.append(
                    {
                        "type": "continuous_production_failed",
                        "message": reason,
                        "timestamp": _utcnow().isoformat(),
                    }
                )
                session.blocking_issues = issues
            await db.commit()

    async def _record_supervisor_failure(
        self,
        run_id: uuid.UUID,
        generation: int,
        fencing_token: int,
        factory: Callable[[], Any],
        exc: Exception,
    ) -> None:
        session_id: Optional[uuid.UUID] = None
        try:
            async with factory() as db:
                if not await self._guard_epoch(
                    db,
                    run_id,
                    generation,
                    fencing_token,
                ):
                    await db.rollback()
                    return
                run = await db.get(ContinuousRun, run_id)
                if run is None:
                    return
                session_id = run.current_session_id
                run.status = "failed"
                run.desired_state = "paused"
                run.current_session_id = None
                run.current_chapter = None
                run.next_run_at = None
                run.last_error = f"{type(exc).__name__}: {exc}"
                await self._event(
                    db,
                    run,
                    "supervisor_failed",
                    run.last_error,
                    severity="error",
                )
                await db.commit()
            await self._finish_work_session(
                factory,
                session_id,
                status="failed",
                reason=f"{type(exc).__name__}: {exc}",
            )
        except Exception:  # noqa: BLE001
            logger.exception("记录连续写作 supervisor 错误失败")

    async def _event(
        self,
        db: Any,
        run: ContinuousRun,
        event_type: str,
        message: str,
        *,
        severity: str = "info",
        chapter_no: Optional[int] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        db.add(
            ContinuousRunEvent(
                run_id=run.id,
                project_id=run.project_id,
                event_type=event_type,
                severity=severity,
                chapter_no=chapter_no,
                message=message,
                data=data or {},
                created_at=_utcnow(),
            )
        )

    async def _get_run(self, db: Any, project_id: uuid.UUID) -> Optional[ContinuousRun]:
        result = await db.execute(
            select(ContinuousRun).where(ContinuousRun.project_id == project_id)
        )
        return result.scalar_one_or_none()

    async def _chapter_status_counts(
        self,
        db: Any,
        project_id: uuid.UUID,
    ) -> dict[str, int]:
        """Count persisted manuscript states instead of inferring from chapter numbers."""
        rows = (
            await db.execute(
                select(Chapter.status, func.count(Chapter.id))
                .where(Chapter.project_id == project_id)
                .group_by(Chapter.status)
            )
        ).all()
        counts = {str(status): int(count) for status, count in rows}
        approved = counts.get("approved", 0)
        published = counts.get("published", 0)
        return {
            "approved_chapters": approved,
            "published_chapters": published,
            "accepted_chapters": approved + published,
            "manuscript_chapter_count": sum(counts.values()),
        }

    async def _accepted_quality_metrics(
        self,
        db: Any,
        run: ContinuousRun,
    ) -> dict[str, Any]:
        """Derive campaign quality metrics from accepted current versions.

        The quality ledger is authoritative.  This covers both crash recovery
        between publication and acknowledgement and manual best-version promotion.
        """
        from app.db.models.quality import QualityAssessment

        predicates = [
            Chapter.project_id == run.project_id,
            Chapter.status.in_(("approved", "published")),
            Chapter.current_version_id.is_not(None),
        ]
        if run.started_at is not None:
            predicates.append(Chapter.updated_at >= run.started_at)
        chapters = list(
            (
                await db.scalars(
                    select(Chapter).where(*predicates).order_by(Chapter.chapter_no)
                )
            ).all()
        )
        if not chapters:
            return {}

        scores: list[float] = []
        latest_score: Optional[float] = None
        for chapter in chapters:
            score = await db.scalar(
                select(QualityAssessment.overall_score)
                .where(
                    QualityAssessment.chapter_id == chapter.id,
                    QualityAssessment.version_id == chapter.current_version_id,
                    QualityAssessment.assessor == "ChiefEditor",
                    QualityAssessment.assessment_type == "deterministic_gate",
                    QualityAssessment.passed.is_(True),
                )
                .order_by(QualityAssessment.created_at.desc())
                .limit(1)
            )
            if score is not None:
                numeric_score = float(score)
                scores.append(numeric_score)
                if chapter is chapters[-1]:
                    latest_score = numeric_score

        latest = chapters[-1]
        derived: dict[str, Any] = {
            "last_chapter": latest.chapter_no,
            "last_word_count": latest.word_count,
            "scored_chapters": len(scores),
        }
        if scores:
            derived["average_score"] = round(sum(scores) / len(scores), 2)
        if latest_score is not None:
            derived["last_score"] = latest_score
        return derived

    async def _reconcile_campaign_progress(
        self,
        db: Any,
        run: ContinuousRun,
    ) -> int:
        """Recover a chapter committed just before its supervisor acknowledgement.

        Chapter production and the durable run counter are separate transactions
        by design.  A process can therefore stop after the chapter is approved
        but before ``_apply_outcome`` increments the counter.  The campaign start
        timestamp fences out chapters accepted before this run.
        """
        await self._archive_stale_review_items(db, run.project_id)
        if run.started_at is None:
            return int(run.completed_chapters or 0)
        accepted = await db.scalar(
            select(func.count(Chapter.id)).where(
                Chapter.project_id == run.project_id,
                Chapter.status.in_(("approved", "published")),
                Chapter.updated_at >= run.started_at,
            )
        )
        return max(int(run.completed_chapters or 0), int(accepted or 0))

    async def _archive_stale_review_items(self, db: Any, project_id: uuid.UUID) -> int:
        """Resolve pending gates whose chapter already passed a newer final gate."""
        approved_chapters = select(Chapter.id).where(
            Chapter.project_id == project_id,
            Chapter.status.in_(("approved", "published")),
        )
        result = await db.execute(
            update(ReviewQueueItem)
            .execution_options(synchronize_session=False)
            .where(
                ReviewQueueItem.project_id == project_id,
                ReviewQueueItem.artifact_type == "chapter",
                ReviewQueueItem.status == "pending",
                ReviewQueueItem.artifact_id.in_(approved_chapters),
            )
            .values(
                status="approved",
                decided_by="system",
                decided_at=_utcnow(),
                decision_notes=(
                    "A newer chapter version passed the final gate; "
                    "the obsolete pending review was archived automatically."
                ),
            )
        )
        return int(result.rowcount or 0)

    async def _find_unfinished_chapter(
        self,
        db: Any,
        project_id: uuid.UUID,
    ) -> Optional[int]:
        result = await db.execute(
            select(Chapter.chapter_no)
            .where(
                Chapter.project_id == project_id,
                Chapter.status.notin_(["approved", "published"]),
            )
            .order_by(Chapter.chapter_no.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _has_world_bible(self, db: Any, project_id: uuid.UUID) -> bool:
        result = await db.execute(
            select(func.count(WorldBible.id)).where(WorldBible.project_id == project_id)
        )
        return result.scalar_one() > 0

    async def _daily_usage(self, db: Any, project_id: uuid.UUID) -> dict[str, Any]:
        start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        row = (
            await db.execute(
                select(
                    func.coalesce(func.sum(AgentRun.input_tokens), 0),
                    func.coalesce(func.sum(AgentRun.output_tokens), 0),
                    func.coalesce(func.sum(AgentRun.cost), 0.0),
                    func.count(AgentRun.id),
                ).where(
                    AgentRun.project_id == project_id,
                    AgentRun.created_at >= start,
                )
            )
        ).one()
        input_tokens = int(row[0] or 0)
        output_tokens = int(row[1] or 0)
        return {
            "today_input_tokens": input_tokens,
            "today_output_tokens": output_tokens,
            "today_total_tokens": input_tokens + output_tokens,
            "today_cost": round(float(row[2] or 0.0), 6),
            "today_requests": int(row[3] or 0),
            "usage_updated_at": _utcnow().isoformat(),
        }

    @staticmethod
    def _budget_reason(policy: dict[str, Any], usage: dict[str, Any]) -> Optional[str]:
        cost_limit = policy.get("daily_cost_limit")
        if cost_limit is not None and float(usage["today_cost"]) >= float(cost_limit):
            return f"今日成本 {usage['today_cost']:.4f} 已达到预算上限 {float(cost_limit):.4f}"
        token_limit = policy.get("daily_token_limit")
        if token_limit is not None and int(usage["today_total_tokens"]) >= int(token_limit):
            return f"今日 Token {usage['today_total_tokens']} 已达到上限 {int(token_limit)}"
        return None

    def _empty_status(
        self,
        project_id: uuid.UUID,
        chapter_counts: Optional[dict[str, int]] = None,
    ) -> dict[str, Any]:
        counts = chapter_counts or {
            "approved_chapters": 0,
            "published_chapters": 0,
            "accepted_chapters": 0,
            "manuscript_chapter_count": 0,
        }
        return {
            "run_id": None,
            "project_id": str(project_id),
            "running": False,
            "desired_state": "stopped",
            "status": "stopped",
            "worker_alive": False,
            "heartbeat_stale": False,
            "current_chapter": None,
            "completed_chapters": 0,
            "campaign_completed_chapters": 0,
            "target_chapters": None,
            "target_scope": "current_run",
            "remaining_chapters": None,
            **counts,
            "autonomy_level": "L3",
            "generation": 0,
            "fencing_token": 0,
            "consecutive_failures": 0,
            "total_failures": 0,
            "last_error": None,
            "errors": [],
            "policy": dict(DEFAULT_POLICY),
            "metrics": {},
            "started_at": None,
            "stopped_at": None,
            "last_heartbeat_at": None,
            "next_run_at": None,
        }


continuous_production_service = ContinuousProductionService()
