"""摘要模型 - chapter_summaries / narrative_summaries。

chapter_summaries 含 v5.0 扩展字段：
entities_involved / facts_asserted / facts_referenced。
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class ChapterSummary(TimestampMixin, Base):
    """ChapterSummary - 章节摘要 + v5.0 实体/事实追踪字段。"""

    __tablename__ = "chapter_summaries"
    __table_args__ = (
        UniqueConstraint("project_id", "chapter_no", name="uq_chapter_summary_project_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ---- v5.0 扩展字段 ----
    # 本章涉及的角色 / 实体 ID 列表
    entities_involved: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    # 本章新确立的设定事实 ID 列表
    facts_asserted: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    # 本章引用的既有事实 ID 列表
    facts_referenced: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    def __repr__(self) -> str:
        return f"<ChapterSummary(chapter_no={self.chapter_no})>"


class NarrativeSummary(TimestampMixin, Base):
    """NarrativeSummary - 跨章节 / 卷 / 全书的叙事摘要。"""

    __tablename__ = "narrative_summaries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # chapter_range / volume / book
    scope: Mapped[str] = mapped_column(String(30), nullable=False, default="chapter_range")
    scope_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    scope_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:
        return f"<NarrativeSummary(scope='{self.scope}', {self.scope_start}-{self.scope_end})>"
