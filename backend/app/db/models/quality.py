"""Structured quality, revision, feedback, and learning ledger models.

These tables keep the evidence required for an auditable improvement loop.  They
deliberately store references to immutable chapter versions where possible so a
later learning cycle can reconstruct exactly what was assessed and changed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class QualityAssessment(TimestampMixin, Base):
    """One structured Critic, continuity, or deterministic assessment."""

    __tablename__ = "quality_assessments"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_quality_assessment_project_idempotency",
        ),
        Index("ix_quality_assessment_chapter_round", "chapter_id", "round_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("work_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    chapter_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("chapter_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    assessor: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    assessment_type: Mapped[str] = mapped_column(String(50), nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rubric_version: Mapped[str] = mapped_column(String(100), nullable=False, default="v1")
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    dimension_scores: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    overall_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    verdict: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class QualityIssue(TimestampMixin, Base):
    """A stable, addressable issue emitted by one quality assessment."""

    __tablename__ = "quality_issues"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "issue_fingerprint",
            name="uq_quality_issue_assessment_fingerprint",
        ),
        Index("ix_quality_issue_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("quality_assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("chapter_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    block_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("manuscript_blocks.id", ondelete="SET NULL"),
        nullable=True,
    )

    issue_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="quality")
    severity: Mapped[str] = mapped_column(String(30), nullable=False, default="medium")
    block_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quoted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actual: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    suggestion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open", index=True)
    resolved_by_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class RevisionAttempt(TimestampMixin, Base):
    """One attempted transition from an input version to a revised version."""

    __tablename__ = "revision_attempts"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_revision_attempt_project_idempotency",
        ),
        Index("ix_revision_attempt_chapter_round", "chapter_id", "round_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("work_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    chapter_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    input_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("chapter_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    output_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("chapter_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    instruction_source: Mapped[str] = mapped_column(String(50), nullable=False, default="critic")
    instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trigger_issue_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    score_before: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_after: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    diff_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class HumanFeedbackEvent(TimestampMixin, Base):
    """Append-only user/editor signal that may feed a later learning cycle."""

    __tablename__ = "human_feedback_events"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_human_feedback_project_idempotency",
        ),
        Index("ix_human_feedback_learning_status", "project_id", "learning_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("work_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    chapter_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("chapter_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    block_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("manuscript_blocks.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("review_queue_items.id", ondelete="SET NULL"),
        nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="user")
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    edited_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tags: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    learning_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", index=True
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PromptVersion(TimestampMixin, Base):
    """Versioned prompt candidate/champion with evaluation provenance."""

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "agent_role",
            "version_no",
            name="uq_prompt_version_scope_role_version",
        ),
        UniqueConstraint(
            "scope_key",
            "idempotency_key",
            name="uq_prompt_version_scope_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    parent_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    learning_cycle_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)

    scope_key: Mapped[str] = mapped_column(String(100), nullable=False, default="global")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_role: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="candidate", index=True)
    evaluation_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class LearningCycle(TimestampMixin, Base):
    """A bounded candidate-generation, evaluation, and promotion cycle."""

    __tablename__ = "learning_cycles"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_learning_cycle_project_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    source_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assessment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_prompt_version_ids: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list
    )
    candidate_memory_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    holdout_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    promotion_decision: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    rollback_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

