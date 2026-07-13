"""用量与负载引用模型 - usage_daily_stats / payload_refs。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class UsageDailyStat(TimestampMixin, Base):
    """UsageDailyStat - 按天聚合的 Token / 成本用量统计。"""

    __tablename__ = "usage_daily_stats"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("llm_providers.id", ondelete="SET NULL"), nullable=True
    )
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    total_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:
        return f"<UsageDailyStat(date={self.stat_date}, model='{self.model_name}')>"


class PayloadRef(Base):
    """PayloadRef - 大体积产物（完整 prompt/response）的外部存储引用。"""

    __tablename__ = "payload_refs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(40), nullable=False)
    artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID, nullable=True)
    # prompt / response / embed / cache
    ref_type: Mapped[str] = mapped_column(String(30), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        name="metadata",
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<PayloadRef(ref_type='{self.ref_type}', storage_key='{self.storage_key}')>"
