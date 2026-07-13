"""项目模型 - projects / project_configs。"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class Project(TimestampMixin, Base):
    """Project - 一个小说创作项目的顶层实体。"""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    genre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    synopsis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_words: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    # 额外元数据（风格、语言、标签等）
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, title='{self.title}', status='{self.status}')>"


class ProjectConfig(TimestampMixin, Base):
    """ProjectConfig - 项目的键值配置（provider、风格、质量阈值等）。"""

    __tablename__ = "project_configs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<ProjectConfig(project_id={self.project_id}, key='{self.key}')>"
