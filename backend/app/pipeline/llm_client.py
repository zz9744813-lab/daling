"""OpenAI 兼容 LLM 调用客户端。

使用 httpx 直接调用 ``{base_url}/chat/completions``，
从 ``app.core.config.settings`` 读取配置。

如果 API key 为空，所有方法返回合理默认值（不发起网络请求），
使整个系统在无 LLM 环境下也能运行（返回占位内容）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("app.pipeline.llm_client")


@dataclass
class LLMResponse:
    """LLM 调用的统一返回结构。"""

    content: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    error: str = ""


class LLMClient:
    """OpenAI 兼容 API 的轻量异步客户端。

    设计要点：
    - 单例模式（通过 ``get_llm_client()`` 获取）
    - 自动从 settings 读取 base_url / api_key / model
    - api_key 为空时返回占位响应（``ok=False``），不抛异常
    - 支持 system / user / assistant 多轮消息
    - 记录 token 用量与延迟
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout

        # 如果未显式传入，从 settings 推断
        if not self.base_url:
            self.base_url = (
                settings.OPENAI_COMPATIBLE_BASE_URL or settings.DEFAULT_BASE_URL
            ).rstrip("/")
        if not self.api_key:
            self.api_key = settings.OPENAI_COMPATIBLE_API_KEY or settings.DEFAULT_API_KEY
        if not self.model:
            self.model = settings.OPENAI_COMPATIBLE_MODEL or settings.DEFAULT_MODEL

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        """LLM 是否已配置（base_url + api_key + model 均非空）。"""
        return bool(self.base_url and self.api_key and self.model)

    # ------------------------------------------------------------------
    # 核心调用
    # ------------------------------------------------------------------
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stop: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 ``POST {base_url}/chat/completions``。

        如果 LLM 未配置，返回占位响应。
        """
        if not self.is_configured:
            logger.warning("LLM 未配置（base_url/api_key/model 为空），返回占位响应")
            return LLMResponse(
                content="",
                model=self.model or "unconfigured",
                ok=False,
                error="LLM 未配置",
            )

        used_model = model or self.model
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": used_model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stop is not None:
            payload["stop"] = stop

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            latency = int((time.monotonic() - t0) * 1000)
            logger.error("LLM 调用 HTTP 错误: %s (status=%s)", exc, exc.response.status_code)
            return LLMResponse(
                model=used_model,
                latency_ms=latency,
                ok=False,
                error=f"HTTP {exc.response.status_code}: {exc.response.text[:500]}",
            )
        except Exception as exc:  # noqa: BLE001
            latency = int((time.monotonic() - t0) * 1000)
            logger.error("LLM 调用异常: %s", exc)
            return LLMResponse(
                model=used_model,
                latency_ms=latency,
                ok=False,
                error=str(exc),
            )

        latency = int((time.monotonic() - t0) * 1000)

        # 解析标准 OpenAI 响应结构
        choices = data.get("choices", [])
        content = ""
        if choices:
            msg = choices[0].get("message", {})
            content = msg.get("content", "")

        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cost = self._estimate_cost(used_model, input_tokens, output_tokens)

        return LLMResponse(
            content=content,
            model=used_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            latency_ms=latency,
            raw=data,
            ok=True,
        )

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------
    async def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """便捷的单轮文本补全。

        Args:
            prompt: 用户提示词
            system: 可选的 system 消息
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages, temperature=temperature, max_tokens=max_tokens)

    async def judge(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
    ) -> LLMResponse:
        """评判模式调用（temperature=0，确保确定性）。"""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages, temperature=0.0)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """粗略估算成本（美元）。"""
        # 常见模型的每百万 token 价格（输入, 输出），按需扩展
        pricing = {
            "gpt-4o": (2.5, 10.0),
            "gpt-4o-mini": (0.15, 0.6),
            "gpt-4-turbo": (10.0, 30.0),
            "gpt-3.5-turbo": (0.5, 1.5),
            "deepseek-chat": (0.14, 0.28),
            "deepseek-reasoner": (0.55, 2.19),
        }
        in_price, out_price = pricing.get(model, (1.0, 3.0))
        return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_llm_client_instance: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局 LLMClient 单例。"""
    global _llm_client_instance
    if _llm_client_instance is None:
        _llm_client_instance = LLMClient()
    return _llm_client_instance


def reset_llm_client() -> None:
    """重置单例（测试用）。"""
    global _llm_client_instance
    _llm_client_instance = None
