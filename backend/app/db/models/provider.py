"""Provider 模型 - llm_providers / model_bindings。"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base
from app.db.models._base import TimestampMixin


class LlmProvider(TimestampMixin, Base):
    """LlmProvider - 一个 LLM 服务提供方（OpenAI 兼容 / Anthropic 等）。"""

    __tablename__ = "llm_providers"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # openai_compatible / anthropic / custom
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, default="openai_compatible")
    base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # 存储加密后的 API Key
    api_key_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_model: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<LlmProvider(name='{self.name}', type='{self.provider_type}')>"


class ModelBinding(TimestampMixin, Base):
    """ModelBinding - 将具体模型绑定到 Provider，并记录能力/成本信息。"""

    __tablename__ = "model_bindings"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("llm_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    context_window: Mapped[int] = mapped_column(Integer, nullable=False, default=8192)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    cost_per_1k_input: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_per_1k_output: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # {"vision": true, "function_calling": true, ...}
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Agent 角色绑定（StoryArchitect/Drafter/Critic 等），null 表示不绑定特定角色
    agent_role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    # 项目级绑定：null=全局绑定，非null=项目级绑定
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )

    def __repr__(self) -> str:
        return f"<ModelBinding(model='{self.model_name}', provider_id={self.provider_id})>"
