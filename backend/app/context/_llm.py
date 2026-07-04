"""LLM 调用与文本工具模块。

提供 OpenAI 兼容 API 的异步调用、JSON 响应解析与 token 估算。
当 API key 未配置时，所有需要 LLM 的方法返回 None / 空结果（不报错）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("app.context.llm")


# ---------------------------------------------------------------------------
# LLM 配置检查
# ---------------------------------------------------------------------------

def is_llm_configured() -> bool:
    """检查 LLM 是否已配置（base_url / api_key / model 均非空）。"""
    return bool(
        settings.OPENAI_COMPATIBLE_API_KEY
        and settings.OPENAI_COMPATIBLE_BASE_URL
        and settings.OPENAI_COMPATIBLE_MODEL
    )


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

async def call_llm(
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> Optional[str]:
    """调用 OpenAI 兼容 API，返回助手消息内容。

    - 请求格式：POST ``{base_url}/chat/completions``
    - 未配置或调用失败时返回 None（不抛出异常）。
    """
    if not is_llm_configured():
        logger.debug("LLM 未配置（OPENAI_COMPATIBLE_API_KEY 为空），跳过调用")
        return None

    base_url = settings.OPENAI_COMPATIBLE_BASE_URL.rstrip("/")
    url = f"{base_url}/chat/completions"

    body: dict[str, Any] = {
        "model": settings.OPENAI_COMPATIBLE_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_COMPATIBLE_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM 调用失败: %s", exc)
        return None


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------

def parse_json_response(text: str | None) -> Any:
    """解析 LLM 返回的 JSON，处理 markdown code block 与前后多余文本。

    解析失败时返回 None。
    """
    if text is None:
        return None

    raw = text.strip()

    # 1. 尝试提取 markdown code block（```json ... ``` 或 ``` ... ```）
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if md_match:
        raw = md_match.group(1).strip()

    # 2. 尝试直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 3. 尝试提取最外层 { } 或 [ ]
    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                continue

    logger.warning("无法解析 JSON 响应: %s", text[:200])
    return None


# ---------------------------------------------------------------------------
# Token 估算与截断
# ---------------------------------------------------------------------------

def _is_cjk_char(ch: str) -> bool:
    """判断字符是否为 CJK / 日韩字符。"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF       # CJK 统一表意文字
        or 0x3400 <= code <= 0x4DBF    # CJK 扩展 A
        or 0x3040 <= code <= 0x30FF    # 平假名 + 片假名
        or 0xAC00 <= code <= 0xD7AF    # 韩文音节
        or 0xFF00 <= code <= 0xFFEF    # 全角字符
    )


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数。

    中文约 1.5 字/token，英文约 4 字符/token。
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if _is_cjk_char(ch):
            cjk += 1
        else:
            other += 1
    return int(cjk / 1.5 + other / 4)


def truncate_to_tokens(text: str, token_budget: int) -> str:
    """按 token 预算截断文本。

    根据 CJK / 非 CJK 字符的 token 估算，逐字符累加直到达到预算上限。
    超出部分用省略号 ``…`` 标记。
    """
    if not text or token_budget <= 0:
        return ""

    used = 0.0
    cut = len(text)  # 默认不截断
    for i, ch in enumerate(text):
        if _is_cjk_char(ch):
            used += 1.0 / 1.5
        else:
            used += 1.0 / 4.0
        if used >= token_budget:
            cut = i + 1
            break

    result = text[:cut]
    if cut < len(text):
        result += "\u2026"  # …
    return result
