"""Persistent automation runs and their operator-visible event timeline."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class ContinuousRun(TimestampMixin, Base):
    """Durable desired state and progress for one project's autonomous run."""

    __tablename__ = "continuous_runs"
    __table_args__ = (
        CheckConstraint(
            "desired_state IN ('running', 'paused', 'stopped')",
            name="ck_continuous_runs_desired_state",
        ),
        CheckConstraint("generation >= 0", name="ck_continuous_runs_generation"),
        CheckConstraint("fencing_token >= 0", name="ck_continuous_runs_fencing_token"),
        CheckConstraint(
            "completed_chapters >= 0",
            name="ck_continuous_runs_completed_chapters",
        ),
        CheckConstraint(
            "target_chapters IS NULL OR target_chapters > 0",
            name="ck_continuous_runs_target_chapters",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # desired_state is operator intent; status is the worker's observed state.
    desired_state: Mapped[str] = mapped_column(String(20), nullable=False, default="stopped")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="stopped")
    autonomy_level: Mapped[str] = mapped_column(String(10), nullable=False, default="L3")

    # Operator intent and worker ownership use separate monotonically increasing
    # epochs.  Every chapter checks both values before and after execution.
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    target_chapters: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completed_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_chapter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("work_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recent_errors: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    # Lease fields prevent two backend workers from writing the same project.
    lease_owner: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ContinuousRunEvent(Base):
    """Append-only operational event used by the autonomous-run timeline."""

    __tablename__ = "continuous_run_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("continuous_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    chapter_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
