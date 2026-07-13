"""记忆与规划反思模型 - book_memory / planning_reflections（v5.0 新增）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class BookMemory(TimestampMixin, Base):
    """BookMemory - 作品级长期记忆条目（跨章节的累积认知）。"""

    __tablename__ = "book_memory"
    __table_args__ = (
        UniqueConstraint("project_id", "memory_type", "key", name="uq_book_memory_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # style / tone / convention / preference / lesson ...
    memory_type: Mapped[str] = mapped_column(String(40), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    def __repr__(self) -> str:
        return f"<BookMemory(type='{self.memory_type}', key='{self.key}')>"


class PlanningReflection(Base):
    """PlanningReflection - 规划阶段的反思记录，用于持续改进 Agent 决策。"""

    __tablename__ = "planning_reflections"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("work_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chapter_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # pre_chapter / post_chapter / session_end / volume_end
    reflection_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decisions: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    lessons_learned: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<PlanningReflection(type='{self.reflection_type}', chapter_no={self.chapter_no})>"
