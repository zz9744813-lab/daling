"""设定事实模型 - canon_facts（v5.0 新增）。

采用 SPO（subject-predicate-object）三元组结构存储可演进的世界设定事实，
支持置信度、可变性、被取代链与标签。
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class CanonFact(TimestampMixin, Base):
    """CanonFact - 一条设定事实（SPO 三元组 + 元数据）。"""

    __tablename__ = "canon_facts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ---- 事实分类 ----
    # setting / character / item / rule / relationship / event / location ...
    fact_type: Mapped[str] = mapped_column(String(40), nullable=False)

    # ---- SPO 三元组 ----
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    predicate: Mapped[str] = mapped_column(String(100), nullable=False)
    object_value: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ---- 可变性与置信度 ----
    # immutable / soft / dynamic
    mutability: Mapped[str] = mapped_column(String(20), nullable=False, default="soft")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # ---- 来源与确认 ----
    source_chapter_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_confirmed_chapter_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ---- 取代链 ----
    # 被新事实取代时，指向新事实 ID；本条状态变为 superseded
    superseded_by_fact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("canon_facts.id", ondelete="SET NULL"), nullable=True
    )

    # ---- 标签 ----
    tags: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    # active / superseded / revoked
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)

    def __repr__(self) -> str:
        return (
            f"<CanonFact(fact_type='{self.fact_type}', "
            f"{self.subject_type}:{self.subject_id} {self.predicate}='{self.object_value[:20]}')>"
        )
