"""故事线模型 - storyline_volumes / storyline_beats。"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class StorylineVolume(TimestampMixin, Base):
    """StorylineVolume - 故事线卷宗，将全书划分为若干卷。"""

    __tablename__ = "storyline_volumes"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    volume_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned")

    def __repr__(self) -> str:
        return f"<StorylineVolume(volume_no={self.volume_no}, title='{self.title}')>"


class StorylineBeat(TimestampMixin, Base):
    """StorylineBeat - 故事节拍，对应单章或一段情节的叙事节点。"""

    __tablename__ = "storyline_beats"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    volume_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("storyline_volumes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    beat_no: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 关联的情节线 ID 列表
    plot_threads: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    importance: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned")

    def __repr__(self) -> str:
        return f"<StorylineBeat(beat_no={self.beat_no}, title='{self.title}')>"
