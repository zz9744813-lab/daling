"""OpenAI 兼容 Provider。

支持所有兼容 OpenAI Chat Completions API 的服务端点（如 OpenAI、DeepSeek、
Moonshot、本地 vLLM/Ollama 等），使用 httpx 进行异步 HTTP 调用。

特性：
- 非流式与流式补全
- JSON mode（response_format）
- 错误处理：超时、429 速率限制、5xx 重试（最多 3 次，指数退避）
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncGenerator, Optional

import httpx

from app.model_gateway.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ModelOutputTruncatedError,
)
from app.model_gateway.tokenizer import estimate_messages_tokens, estimate_tokens

logger = logging.getLogger("app.model_gateway.openai_compatible")


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI 兼容 Chat Completions Provider。"""

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
            base_url: API 基础地址（如 ``https://api.openai.com/v1``）。
            api_key: API 密钥。
            default_model: 默认模型名称。
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
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, request: LLMRequest, stream: bool = False) -> dict:
        """构造请求体。"""
        model = request.model or self.default_model
        # The role binding is the operator-controlled hard ceiling.  BaseAgent
        # already chooses an adequate request size; the provider must not silently
        # raise it again for reasoning models and defeat that ceiling.
        max_tokens = request.max_tokens
        payload: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        # 推理模型不传 response_format（部分推理模型不支持会导致返回空）
        if request.response_format and not request.is_reasoning_model:
            payload["response_format"] = request.response_format
        if request.stop:
            payload["stop"] = request.stop
        return payload

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    async def _request_with_retry(self, client: httpx.AsyncClient, payload: dict) -> httpx.Response:
        """带重试逻辑的 HTTP POST 请求。

        对 429 和 5xx 错误进行指数退避重试。
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await client.post(self._url(), json=payload)
                # 429 速率限制 → 重试
                if resp.status_code == 429:
                    wait = 2 ** (attempt - 1)
                    logger.warning(
                        "收到 429 速率限制，%ds 后重试（第 %d/%d 次）",
                        wait,
                        attempt,
                        self.max_retries,
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
                        resp.status_code,
                        wait,
                        attempt,
                        self.max_retries,
                    )
                    last_exc = httpx.HTTPStatusError(
                        f"Server error ({resp.status_code})",
                        request=resp.request,
                        response=resp,
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
                    wait,
                    attempt,
                    self.max_retries,
                    exc,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(wait)
                    continue
                raise
        # 所有重试均失败
        if last_exc:
            raise last_exc
        raise RuntimeError("请求失败且无异常信息")

    # ------------------------------------------------------------------
    # BaseProvider 实现
    # ------------------------------------------------------------------
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """非流式补全。"""
        payload = self._build_payload(request, stream=False)
        model = request.model or self.default_model

        async with httpx.AsyncClient(timeout=self.timeout, headers=self._build_headers()) as client:
            resp = await self._request_with_retry(client, payload)
            data = resp.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        reasoning_content = message.get("reasoning_content") or message.get("reasoning") or ""
        finish_reason = choice.get("finish_reason", "stop")

        if not content and not reasoning_content:
            raise RuntimeError(
                f"模型返回空响应 (model={data.get('model', model)}, finish={finish_reason})"
            )
        if not content and reasoning_content and finish_reason == "length":
            raise RuntimeError(
                "模型在推理阶段耗尽输出长度，未生成可展示的最终回答；"
                "请提高 max_tokens 或改用更适合交互的模型"
            )

        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens") or estimate_messages_tokens(request.messages)
        output_tokens = usage.get("completion_tokens") or estimate_tokens(
            content or reasoning_content
        )
        used_model = data.get("model", model)

        if request.is_reasoning_model:
            logger.info(
                "推理模型响应: content=%d 字符, reasoning=%d 字符, model=%s, finish=%s",
                len(content),
                len(reasoning_content),
                used_model,
                finish_reason,
            )
        elif not content and reasoning_content:
            # 兼容：普通调用但 content 为空、reasoning 有内容
            logger.info(
                "content 为空，从 reasoning 提取 (%d 字符), model=%s, finish=%s",
                len(reasoning_content),
                used_model,
                finish_reason,
            )
        else:
            logger.info(
                "API content 正常 (%d 字符), model=%s, finish=%s",
                len(content),
                used_model,
                finish_reason,
            )

        return LLMResponse(
            content=content,
            reasoning_content=reasoning_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=used_model,
            finish_reason=finish_reason,
        )

    async def stream_complete(self, request: LLMRequest) -> AsyncGenerator[str, None]:
        """True streaming completion with retry before the first visible chunk.

        ``AsyncClient.post`` buffers the body before returning, so it cannot be
        used here.  Once a visible chunk has been emitted we intentionally do
        not retry a broken stream, because replaying it would duplicate text in
        the client.
        """
        payload = self._build_payload(request, stream=True)
        emitted_visible = False
        reasoning_parts: list[str] = []
        finish_reason: Optional[str] = None
        last_error: Optional[Exception] = None

        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers=self._build_headers(),
        ) as client:
            for attempt in range(1, self.max_retries + 1):
                if not emitted_visible:
                    reasoning_parts.clear()
                    finish_reason = None
                try:
                    async with client.stream("POST", self._url(), json=payload) as resp:
                        if resp.status_code == 429 or resp.status_code >= 500:
                            await resp.aread()
                            error = httpx.HTTPStatusError(
                                f"Streaming HTTP {resp.status_code}",
                                request=resp.request,
                                response=resp,
                            )
                            last_error = error
                            if attempt < self.max_retries:
                                retry_after = resp.headers.get("Retry-After", "")
                                try:
                                    wait = min(max(float(retry_after), 0.0), 60.0)
                                except ValueError:
                                    wait = float(2 ** (attempt - 1))
                                logger.warning(
                                    "流请求收到 %d，%.1fs 后重试（第 %d/%d 次）",
                                    resp.status_code,
                                    wait,
                                    attempt,
                                    self.max_retries,
                                )
                                await asyncio.sleep(wait)
                                continue
                            raise error

                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                logger.debug("跳过无法解析的 SSE 行: %s", data_str)
                                continue
                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            choice = choices[0]
                            if choice.get("finish_reason"):
                                finish_reason = choice["finish_reason"]
                            delta = choice.get("delta") or {}
                            reasoning_delta = (
                                delta.get("reasoning_content") or delta.get("reasoning") or ""
                            )
                            if reasoning_delta:
                                reasoning_parts.append(str(reasoning_delta))
                            delta_content = delta.get("content")
                            if isinstance(delta_content, str) and delta_content:
                                emitted_visible = True
                                yield delta_content

                    if str(finish_reason).lower() in {
                        "length",
                        "max_tokens",
                        "max_output_tokens",
                    }:
                        raise ModelOutputTruncatedError(
                            "模型流式输出达到长度上限，正文可能不完整；已拒绝该结果"
                        )
                    if emitted_visible:
                        return

                    reasoning = "".join(reasoning_parts).strip()
                    if reasoning and finish_reason != "length":
                        marker = re.search(
                            r"(?:===FINAL_ANSWER===|最终答案|最终回答|final answer)"
                            r"\s*[:：]?\s*([\s\S]+)$",
                            reasoning,
                            flags=re.IGNORECASE,
                        )
                        if marker and marker.group(1).strip():
                            emitted_visible = True
                            yield marker.group(1).strip()
                            return
                    if reasoning:
                        raise RuntimeError("模型流只返回推理过程，未生成可展示的最终回答")
                    raise RuntimeError("模型流结束但没有返回任何内容")

                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = exc
                    if emitted_visible or attempt >= self.max_retries:
                        raise
                    wait = float(2 ** (attempt - 1))
                    logger.warning(
                        "流请求中断，%.1fs 后重试（第 %d/%d 次）: %s",
                        wait,
                        attempt,
                        self.max_retries,
                        exc,
                    )
                    await asyncio.sleep(wait)

        if last_error:
            raise last_error
        raise RuntimeError("流请求失败且无异常信息")
