"""会话与运行模型 - work_sessions / review_queue_items / agent_runs。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class WorkSession(TimestampMixin, Base):
    """WorkSession - 用户感知层主对象，一次创作工作会话。"""

    __tablename__ = "work_sessions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # L0 / L1 / L2 自治等级
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="L2")
    # planning / running / paused / completed / failed
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planning")
    session_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="advance_chapters",
    )

    participants: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    current_artifact_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    current_artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)

    quality_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=85)
    current_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quality_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blocking_issues: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    next_action: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    target_params: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="low")
    progress_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    paused_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mission_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)

    def __repr__(self) -> str:
        return f"<WorkSession(id={self.id}, title='{self.title}', status='{self.status}')>"


class ReviewQueueItem(TimestampMixin, Base):
    """ReviewQueueItem - 人工审批队列条目。"""

    __tablename__ = "review_queue_items"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("work_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # chapter / world_bible / outline / memory_patch ...
    item_type: Mapped[str] = mapped_column(String(40), nullable=False)
    artifact_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="low")
    # pending / approved / revised / rejected / takeover
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    decided_by: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chapter_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<ReviewQueueItem(item_type='{self.item_type}', status='{self.status}')>"


class AgentRun(Base):
    """AgentRun - 一次 Agent 执行的运行记录与用量统计。"""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("work_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # pending / running / success / failed / cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AgentRun(agent='{self.agent_name}', status='{self.status}')>"
