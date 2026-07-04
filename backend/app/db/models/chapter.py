"""章节模型 - chapters / chapter_versions / manuscript_blocks。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class Chapter(TimestampMixin, Base):
    """Chapter - 一个章节的元信息与状态。"""

    __tablename__ = "chapters"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # draft / generating / review / approved / published
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_words: Mapped[int] = mapped_column(Integer, nullable=False, default=3000)
    current_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)

    def __repr__(self) -> str:
        return f"<Chapter(chapter_no={self.chapter_no}, title='{self.title}', status='{self.status}')>"


class ChapterVersion(Base):
    """ChapterVersion - 章节的某一版正文快照。"""

    __tablename__ = "chapter_versions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    chapter_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    created_by_agent: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ChapterVersion(chapter_id={self.chapter_id}, version_no={self.version_no})>"


class ManuscriptBlock(Base):
    """ManuscriptBlock - 正文的分块存储，支持细粒度编辑与版本对比。"""

    __tablename__ = "manuscript_blocks"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    chapter_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("chapter_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    block_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # paragraph / dialogue / scene_break / note
    block_type: Mapped[str] = mapped_column(String(30), nullable=False, default="paragraph")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ManuscriptBlock(chapter_id={self.chapter_id}, block_no={self.block_no})>"
