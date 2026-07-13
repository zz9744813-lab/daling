"""Gateway 管理器。

统一管理 LLM Provider 的创建与调用，支持从环境变量配置和数据库 LlmProvider 表加载。

用法::

    gateway = Gateway()
    response = await gateway.complete(request, provider_config)
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, AsyncGenerator, Optional

from app.core.config import settings
from app.model_gateway.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
)
from app.model_gateway.providers.anthropic import AnthropicProvider
from app.model_gateway.providers.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger("app.model_gateway.gateway")


class Gateway:
    """LLM Provider 网关管理器（单例）。

    负责根据 provider_type 创建对应的 Provider 实例，并提供便捷的
    ``complete`` / ``stream_complete`` 方法。
    """

    _instance: Optional["Gateway"] = None

    def __new__(cls) -> "Gateway":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False  # type: ignore[attr-defined]
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:  # type: ignore[attr-defined]
            return
        self._providers: dict[str, BaseProvider] = {}
        self._initialized = True  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Provider 工厂
    # ------------------------------------------------------------------
    def get_provider(
        self,
        provider_type: str,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        **kwargs: Any,
    ) -> BaseProvider:
        """根据类型创建或获取缓存的 Provider 实例。

        Args:
            provider_type: Provider 类型（openai_compatible / anthropic）。
            base_url: API 基础地址。
            api_key: API 密钥。
            model: 默认模型名称。
            **kwargs: 额外参数（timeout、max_retries 等）。

        Returns:
            BaseProvider 实例。

        Raises:
            ValueError: 不支持的 provider_type。
        """
        # 构建缓存键（相同配置复用实例）
        # Credentials are part of provider identity.  Use a one-way fingerprint
        # so rotating a key cannot accidentally reuse an instance holding the
        # old key, while never placing the secret itself in logs/cache keys.
        key_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
        transport_fingerprint = hashlib.sha256(
            json.dumps(kwargs, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:12]
        cache_key = (
            f"{provider_type}:{base_url}:{model}:{key_fingerprint}:"
            f"{transport_fingerprint}"
        )
        if cache_key in self._providers:
            return self._providers[cache_key]

        if provider_type == "openai_compatible":
            provider = OpenAICompatibleProvider(
                base_url=base_url,
                api_key=api_key,
                default_model=model,
                **kwargs,
            )
        elif provider_type == "anthropic":
            provider = AnthropicProvider(
                base_url=base_url,
                api_key=api_key,
                default_model=model,
                **kwargs,
            )
        else:
            raise ValueError(f"不支持的 provider_type: {provider_type}")

        self._providers[cache_key] = provider
        return provider

    def get_provider_from_config(self, provider_config: dict) -> BaseProvider:
        """从配置字典创建 Provider。

        provider_config 格式::

            {
                "provider_type": "openai_compatible",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-xxx",
                "model": "gpt-4o"
            }
        """
        transport_options: dict[str, Any] = {}
        if provider_config.get("timeout") is not None:
            transport_options["timeout"] = float(provider_config["timeout"])
        if provider_config.get("max_retries") is not None:
            transport_options["max_retries"] = int(provider_config["max_retries"])
        return self.get_provider(
            provider_type=provider_config.get("provider_type", settings.DEFAULT_PROVIDER),
            base_url=provider_config.get("base_url", ""),
            api_key=provider_config.get("api_key", ""),
            model=provider_config.get("model", ""),
            **transport_options,
        )

    # ------------------------------------------------------------------
    # 默认配置
    # ------------------------------------------------------------------
    def get_default_config(self) -> dict:
        """从 settings 获取默认 Provider 配置。"""
        provider_type = settings.DEFAULT_PROVIDER or "openai_compatible"
        if provider_type == "anthropic":
            return {
                "provider_type": "anthropic",
                "base_url": settings.ANTHROPIC_BASE_URL or "https://api.anthropic.com",
                "api_key": settings.ANTHROPIC_API_KEY,
                "model": settings.ANTHROPIC_MODEL or settings.DEFAULT_MODEL,
            }
        # 默认 OpenAI 兼容
        return {
            "provider_type": "openai_compatible",
            "base_url": settings.OPENAI_COMPATIBLE_BASE_URL
            or settings.DEFAULT_BASE_URL
            or "https://api.openai.com/v1",
            "api_key": settings.OPENAI_COMPATIBLE_API_KEY or settings.DEFAULT_API_KEY,
            "model": settings.OPENAI_COMPATIBLE_MODEL or settings.DEFAULT_MODEL,
        }

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------
    async def complete(
        self,
        request: LLMRequest,
        provider_config: Optional[dict] = None,
    ) -> LLMResponse:
        """便捷方法：非流式补全。

        Args:
            request: LLM 请求。
            provider_config: Provider 配置字典，为 None 则使用默认配置。
        """
        config = provider_config or self.get_default_config()
        provider = self.get_provider_from_config(config)
        return await provider.complete(request)

    async def stream_complete(
        self,
        request: LLMRequest,
        provider_config: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        """便捷方法：流式补全。

        Args:
            request: LLM 请求。
            provider_config: Provider 配置字典，为 None 则使用默认配置。

        Yields:
            str: 文本增量片段。
        """
        config = provider_config or self.get_default_config()
        provider = self.get_provider_from_config(config)
        async for chunk in provider.stream_complete(request):
            yield chunk


# 全局单例
gateway = Gateway()
