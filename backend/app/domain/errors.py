"""Novel Agent OS 领域异常体系。

所有 Agent 在 LLM 调用失败或结果校验失败时必须抛出这些异常，
不允许返回默认值伪装成功。

规则（AGENTS.md 第 5/6/14/15 条）：
- 不允许用默认成功值掩盖异常
- 不允许 LLM 失败后生成占位正文并继续
- 失败时修复根因，不允许静默 catch Exception
- 不允许使用 broad except 后继续成功流程
"""

from __future__ import annotations


class AgentExecutionError(Exception):
    """Agent 执行失败的基类。

    当 LLM 调用失败、JSON 解析失败、或结果校验失败时抛出。
    Orchestrator 捕获后应将章节标记为 failed，不应继续流水线。
    """

    def __init__(
        self,
        message: str,
        *,
        agent_name: str = "",
        project_id: str = "",
        chapter_no: int | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.agent_name = agent_name
        self.project_id = project_id
        self.chapter_no = chapter_no
        self.cause = cause

    def __str__(self) -> str:
        parts = [self.message]
        if self.agent_name:
            parts.append(f"agent={self.agent_name}")
        if self.project_id:
            parts.append(f"project={self.project_id}")
        if self.chapter_no is not None:
            parts.append(f"chapter={self.chapter_no}")
        if self.cause:
            parts.append(f"cause={type(self.cause).__name__}: {self.cause}")
        return " | ".join(parts)


class EmptyResultError(AgentExecutionError):
    """Agent 返回了空结果。

    当 LLM 返回空字符串、空 JSON、空场景列表等无效内容时抛出。
    不允许用默认值填充后继续执行。
    """


class QualityCheckError(AgentExecutionError):
    """质量检查 Agent（Critic / ContinuityGuard）执行失败。

    当 Critic 或 ContinuityGuard 的 LLM 调用失败时抛出。
    不允许默认给 75 分或 passed=True。
    """


class LLMCallError(AgentExecutionError):
    """LLM 调用本身失败（网络错误、认证错误、速率限制等）。

    这是底层错误，Agent 应将其包装为此异常后抛出，
    而非 catch 后返回默认值。
    """


class TruncationError(AgentExecutionError):
    """LLM 响应被截断（finish_reason=length）。

    当检测到响应因 max_tokens 限制被截断时抛出。
    截断的正文不应被当作完整内容处理。
    """
