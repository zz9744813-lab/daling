"""世界设定模型 - world_bibles。"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class WorldBible(TimestampMixin, Base):
    """WorldBible - 世界观圣经，包含设定、规则、势力、地理等结构化内容。"""

    __tablename__ = "world_bibles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 结构化世界设定内容
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # 纯文本摘要，便于检索
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    created_by_agent: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    def __repr__(self) -> str:
        return f"<WorldBible(project_id={self.project_id}, version={self.version}, status='{self.status}')>"
