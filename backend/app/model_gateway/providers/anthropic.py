"""Anthropic Provider（Messages API）。

使用 Anthropic Messages API 进行 LLM 调用。

特性：
- system 消息单独传，messages 只含 user/assistant
- 非流式与流式补全
- 流式解析 content_block_delta 事件
- 错误处理：超时、429 速率限制、5xx 重试（最多 3 次，指数退避）
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

import httpx

from app.model_gateway.base import BaseProvider, LLMRequest, LLMResponse
from app.model_gateway.tokenizer import estimate_messages_tokens, estimate_tokens

logger = logging.getLogger("app.model_gateway.anthropic")

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API Provider。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_model: str = "",
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        """初始化 Provider。

        Args:
            base_url: API 基础地址（如 ``https://api.anthropic.com``）。
            api_key: API 密钥（x-api-key）。
            default_model: 默认模型名称（如 ``claude-3-5-sonnet-20241022``）。
            timeout: 请求超时时间（秒）。
            max_retries: 最大重试次数（针对 429 / 5xx）。
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _build_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    def _split_system_messages(
        self, request: LLMRequest
    ) -> tuple[str, list[dict]]:
        """将 system 消息单独提取，剩余消息只含 user/assistant。

        Anthropic API 要求 system 作为顶层字段传入，messages 只含
        user/assistant 角色。

        Returns:
            (system_text, messages_list)
        """
        system_parts: list[str] = []
        messages: list[dict] = []
        for msg in request.messages:
            if msg.role == "system":
                system_parts.append(msg.content)
            else:
                messages.append({"role": msg.role, "content": msg.content})
        system_text = "\n\n".join(system_parts)
        return system_text, messages

    def _build_payload(self, request: LLMRequest, stream: bool = False) -> dict:
        """构造请求体。"""
        model = request.model or self.default_model
        system_text, messages = self._split_system_messages(request)
        payload: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }
        if system_text:
            payload["system"] = system_text
        if request.stop:
            payload["stop_sequences"] = request.stop
        return payload

    def _url(self) -> str:
        return f"{self.base_url}/v1/messages"

    async def _request_with_retry(
        self, client: httpx.AsyncClient, payload: dict
    ) -> httpx.Response:
        """带重试逻辑的 HTTP POST 请求。

        对 429 和 5xx 错误进行指数退避重试。
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await client.post(
                    self._url(), json=payload, headers=self._build_headers()
                )
                # 429 速率限制 → 重试
                if resp.status_code == 429:
                    wait = 2 ** (attempt - 1)
                    logger.warning(
                        "收到 429 速率限制，%ds 后重试（第 %d/%d 次）",
                        wait, attempt, self.max_retries,
                    )
                    last_exc = httpx.HTTPStatusError(
                        "Rate limited (429)", request=resp.request, response=resp
                    )
                    if attempt < self.max_retries:
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                # 5xx 服务端错误 → 重试
                if resp.status_code >= 500:
                    wait = 2 ** (attempt - 1)
                    logger.warning(
                        "收到 %d 服务端错误，%ds 后重试（第 %d/%d 次）",
                        resp.status_code, wait, attempt, self.max_retries,
                    )
                    last_exc = httpx.HTTPStatusError(
                        f"Server error ({resp.status_code})",
                        request=resp.request, response=resp,
                    )
                    if attempt < self.max_retries:
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                # 其它非 2xx
                if resp.status_code >= 400:
                    resp.raise_for_status()
                return resp
            except httpx.TimeoutException as exc:
                last_exc = exc
                wait = 2 ** (attempt - 1)
                logger.warning(
                    "请求超时，%ds 后重试（第 %d/%d 次）: %s",
                    wait, attempt, self.max_retries, exc,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(wait)
                    continue
                raise
        # 所有重试均失败
        if last_exc:
            raise last_exc
        raise RuntimeError("请求失败且无异常信息")

    @staticmethod
    def _extract_content(data: dict) -> str:
        """从 Anthropic 响应中提取文本内容。

        Anthropic 的 content 是一个列表，每项有 type 字段，
        text 类型的 block 包含实际文本。
        """
        content_blocks = data.get("content", [])
        parts: list[str] = []
        for block in content_blocks:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)

    # ------------------------------------------------------------------
    # BaseProvider 实现
    # ------------------------------------------------------------------
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """非流式补全。"""
        payload = self._build_payload(request, stream=False)
        model = request.model or self.default_model

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await self._request_with_retry(client, payload)
            data = resp.json()

        content = self._extract_content(data)
        stop_reason = data.get("stop_reason", "stop")

        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens") or estimate_messages_tokens(request.messages)
        output_tokens = usage.get("output_tokens") or estimate_tokens(content)
        used_model = data.get("model", model)

        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=used_model,
            finish_reason=stop_reason or "stop",
        )

    async def stream_complete(self, request: LLMRequest) -> AsyncGenerator[str, None]:
        """流式补全，解析 content_block_delta 事件。"""
        payload = self._build_payload(request, stream=True)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await self._request_with_retry(client, payload)
            async for line in resp.aiter_lines():
                if not line:
                    continue
                # SSE 格式：event: xxx\ndata: {...}
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("跳过无法解析的 SSE 行: %s", data_str)
                        continue
                    event_type = chunk.get("type", "")
                    # content_block_delta 事件携带增量文本
                    if event_type == "content_block_delta":
                        delta = chunk.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield text
                    # message_stop 事件表示流结束
                    elif event_type == "message_stop":
                        break
