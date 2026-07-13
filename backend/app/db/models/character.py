"""角色模型 - characters / relationships。"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class Character(TimestampMixin, Base):
    """Character - 角色档案。"""

    __tablename__ = "characters"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # protagonist / antagonist / supporting / minor
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="supporting")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 性格、外貌、背景等结构化属性
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    first_appearance_chapter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<Character(name='{self.name}', role='{self.role}')>"


class Relationship(TimestampMixin, Base):
    """Relationship - 角色之间的关系。"""

    __tablename__ = "relationships"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_character_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_character_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    strength: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")

    def __repr__(self) -> str:
        return (
            f"<Relationship(type='{self.relationship_type}', "
            f"from={self.from_character_id}, to={self.to_character_id})>"
        )
