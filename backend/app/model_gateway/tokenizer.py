"""简单的 token 估算器（不依赖 tiktoken）。

基于中英文混合文本的经验估算：
- 中文字符：约 1.5 字 / token
- 英文/ASCII 字符：约 4 字符 / token

仅为粗略估算，用于成本预估与上下文长度控制，非精确值。
"""

from __future__ import annotations

from app.model_gateway.base import LLMMessage


def _count_chars(text: str) -> tuple[int, int]:
    """统计文本中的中文字符数与非中文字符数。

    Args:
        text: 输入文本。

    Returns:
        (中文字符数, 非中文字符数)
    """
    cjk_count = 0
    other_count = 0
    for ch in text:
        # CJK 统一表意文字范围 + 常用中文标点扩展范围
        code = ord(ch)
        if (
            0x4E00 <= code <= 0x9FFF  # CJK 统一表意文字
            or 0x3400 <= code <= 0x4DBF  # CJK 扩展 A
            or 0x20000 <= code <= 0x2A6DF  # CJK 扩展 B
            or 0x3000 <= code <= 0x303F  # CJK 标点符号
            or 0xFF00 <= code <= 0xFFEF  # 全角字符
        ):
            cjk_count += 1
        else:
            other_count += 1
    return cjk_count, other_count


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数。

    中文约 1.5 字/token，英文约 4 字符/token。

    Args:
        text: 输入文本。

    Returns:
        估算的 token 数（向上取整，最小为 0）。
    """
    if not text:
        return 0
    cjk_count, other_count = _count_chars(text)
    # 中文：1.5 字/token → token = cjk / 1.5
    # 英文：4 字符/token → token = other / 4
    tokens = cjk_count / 1.5 + other_count / 4.0
    return max(0, int(tokens) + (1 if tokens % 1 > 0 else 0))


def estimate_messages_tokens(messages: list[LLMMessage]) -> int:
    """估算消息列表的总 token 数（含每条消息的额外开销）。

    每条消息约额外 4 token 的结构开销（role 标记等），
    整个对话额外 3 token 的模板开销。

    Args:
        messages: LLM 消息列表。

    Returns:
        估算的总 token 数。
    """
    if not messages:
        return 0
    total = 3  # 对话模板开销
    for msg in messages:
        total += estimate_tokens(msg.content)
        total += 4  # 每条消息的结构开销
    return total
