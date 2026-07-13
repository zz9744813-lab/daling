"""情节线模型 - plot_threads / current_story_states。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class PlotThread(TimestampMixin, Base):
    """PlotThread - 一条情节线（主线 / 支线 / 伏笔）。"""

    __tablename__ = "plot_threads"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # main / sub / foreshadow
    type: Mapped[str] = mapped_column(String(30), nullable=False, default="sub")
    # planned / active / resolved / abandoned
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned")
    introduced_chapter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolved_chapter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    importance: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        name="metadata",
        nullable=False,
        default=dict,
    )

    def __repr__(self) -> str:
        return f"<PlotThread(name='{self.name}', type='{self.type}', status='{self.status}')>"


class CurrentStoryState(Base):
    """CurrentStoryState - 某一章结束时的故事世界快照（角色状态、情节进度等）。"""

    __tablename__ = "current_story_states"
    __table_args__ = (
        UniqueConstraint("project_id", "chapter_no", name="uq_story_state_project_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # 结构化世界 / 角色状态
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # 各 Agent 的局部状态
    agent_states: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<CurrentStoryState(project_id={self.project_id}, chapter_no={self.chapter_no})>"
