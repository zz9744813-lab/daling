"""Provider Gateway 核心接口定义。

定义 LLM 调用的统一数据结构与抽象基类，所有 Provider 实现均需继承 BaseProvider。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, Optional


@dataclass
class LLMMessage:
    """单条对话消息。

    Attributes:
        role: 消息角色，取值 system / user / assistant。
        content: 消息文本内容。
    """

    role: str  # system / user / assistant
    content: str


@dataclass
class LLMRequest:
    """LLM 请求参数。

    Attributes:
        messages: 对话消息列表。
        model: 模型名称，留空则使用 Provider 默认模型。
        temperature: 采样温度，控制随机性。
        max_tokens: 最大生成 token 数。
        stream: 是否流式返回。
        response_format: 响应格式，如 ``{"type": "json_object"}``。
        stop: 停止序列列表。
        is_reasoning_model: 是否为推理模型。推理模型会自动提升 max_tokens、
            跳过 response_format、并通过 reasoning_content 字段返回思考过程。
    """

    messages: list[LLMMessage]
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    stream: bool = False
    response_format: Optional[dict] = None  # {"type": "json_object"}
    stop: Optional[list[str]] = None
    is_reasoning_model: bool = False


@dataclass
class LLMResponse:
    """LLM 响应结果。

    Attributes:
        content: 生成的文本内容（普通模型的主要输出）。
        reasoning_content: 推理模型的思考过程（推理模型可能 content 为空，
            真正的结构化内容在 reasoning_content 中）。
        input_tokens: 输入 token 数。
        output_tokens: 输出 token 数。
        model: 实际使用的模型名称。
        finish_reason: 结束原因（stop / length / content_filter 等）。
    """

    content: str
    reasoning_content: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    finish_reason: str = "stop"


class BaseProvider(ABC):
    """LLM Provider 抽象基类。

    所有具体 Provider（OpenAI 兼容、Anthropic 等）均需继承此类并实现
    ``complete`` 与 ``stream_complete`` 方法。
    """

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """同步（非流式）补全。

        Args:
            request: LLM 请求参数。

        Returns:
            LLMResponse 响应结果。
        """
        ...

    @abstractmethod
    async def stream_complete(self, request: LLMRequest) -> AsyncGenerator[str, None]:
        """流式补全，逐块 yield 文本增量。

        Args:
            request: LLM 请求参数（stream 字段会被忽略，始终按流式处理）。

        Yields:
            str: 文本增量片段。
        """
        ...
        yield ""  # pragma: no cover  # 仅用于类型标注，子类会覆盖
