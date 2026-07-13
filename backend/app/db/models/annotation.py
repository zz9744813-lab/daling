"""批注模型 - annotations。"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class Annotation(TimestampMixin, Base):
    """Annotation - 对章节正文的批注 / 评审意见。"""

    __tablename__ = "annotations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chapter_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    block_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # agent name 或 "user"
    author: Mapped[str] = mapped_column(String(50), nullable=False)
    # style / plot / canon / quality / note
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return (
            f"<Annotation(author='{self.author}', type='{self.type}', severity='{self.severity}')>"
        )
