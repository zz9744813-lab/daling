"""Agent 基类。

所有 Agent 继承 BaseAgent，获得统一的 LLM 调用、JSON 解析与
AgentRun 记录能力。
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.session import AgentRun
from app.db.models.project import ProjectConfig
from app.db.models.provider import LlmProvider, ModelBinding
from app.model_gateway import Gateway, LLMMessage, LLMRequest, LLMResponse

logger = logging.getLogger("app.agents.base")

# ---------------------------------------------------------------------------
# Prompt 约定：要求推理模型先自由思考，再用标记输出 JSON
# ---------------------------------------------------------------------------
JSON_OUTPUT_CONTRACT = """
先自由思考，思考过程不限格式，不要用 JSON。
思考结束后，另起一行，只写这个标记：
===FINAL_JSON===
标记后紧跟且只包含合法 JSON，不要有任何解释、前言、代码块符号。
"""


def strip_json_markdown(text: str) -> str:
    """去除 LLM 返回中可能包裹的 markdown 代码块标记。

    处理 ```` ```json ... ``` ```` 和 ```` ``` ... ``` ```` 两种情况。
    """
    text = text.strip()
    # 匹配 ```json ... ``` 或 ``` ... ```
    pattern = r"^```(?:json)?\s*\n?(.*?)\n?```\s*$"
    match = re.match(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def extract_json_block(text: str) -> Optional[str]:
    """括号配对提取第一个完整 JSON 对象/数组，不依赖正则。

    Args:
        text: 原始文本。

    Returns:
        提取到的 JSON 字符串，找不到返回 None。
    """
    text = strip_json_markdown(text)
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def parse_json_response(text: str) -> dict:
    """解析 LLM 返回的 JSON 文本，处理 markdown 包裹与括号配对。

    Args:
        text: LLM 返回的原始文本。

    Returns:
        解析后的 dict。

    Raises:
        json.JSONDecodeError: JSON 解析失败。
    """
    cleaned = strip_json_markdown(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        block = extract_json_block(cleaned)
        if block is None:
            raise
        return json.loads(block)


def extract_after_marker(text: str, marker: str = "===FINAL_JSON===") -> str:
    """提取标记之后的内容。

    推理模型被要求在思考后用 ``===FINAL_JSON===`` 标记输出 JSON。
    如果找到标记，返回标记后的文本；否则返回原文（兜底）。
    """
    idx = text.find(marker)
    if idx == -1:
        return text
    return text[idx + len(marker):].strip()


class BaseAgent:
    """所有 Agent 的基类。

    子类需覆盖 ``agent_name`` 类属性，并通过 ``_llm_complete`` /
    ``_llm_json`` 方法调用 LLM。

    Attributes:
        agent_name: Agent 名称标识，子类必须覆盖。
    """

    agent_name: str = "base"

    def __init__(
        self,
        gateway: Gateway,
        db: AsyncSession,
        project_id: uuid.UUID,
        session_id: Optional[uuid.UUID] = None,
    ) -> None:
        """初始化 Agent。

        Args:
            gateway: LLM Gateway 实例。
            db: 异步数据库会话。
            project_id: 项目 ID。
            session_id: 关联的 WorkSession ID（可选）。
        """
        self.gateway = gateway
        self.db = db
        self.project_id = project_id
        self.session_id = session_id
        # 项目专属自定义系统提示词（类似 Gemini Gems），由 Orchestrator 注入
        self.custom_system_prompt: str = ""

    # ------------------------------------------------------------------
    # Provider 配置
    # ------------------------------------------------------------------
    async def _get_provider_config(self) -> Optional[dict]:
        """按优先级查找 Provider 配置。

        优先级：
        1. 查 ModelBinding 表：agent_role = self.agent_name 且 project_id 匹配
           （项目级绑定优先于全局绑定）
        2. 查 ModelBinding 表：agent_role = self.agent_name 且 project_id IS NULL
           （全局绑定）
        3. 查 ProjectConfig 表：key='provider'（全局配置，旧方式）
        4. 返回 None（由 Gateway 使用 .env 默认配置）
        """
        # 1 & 2: 查 ModelBinding 表，项目级优先于全局
        try:
            stmt = (
                select(ModelBinding, LlmProvider)
                .outerjoin(LlmProvider, ModelBinding.provider_id == LlmProvider.id)
                .where(
                    ModelBinding.agent_role == self.agent_name,
                    (ModelBinding.project_id == self.project_id)
                    | (ModelBinding.project_id.is_(None)),
                )
                # 项目级（project_id IS NOT NULL）排在前面
                .order_by(ModelBinding.project_id.is_not(None).desc())
            )
            result = await self.db.execute(stmt)
            row = result.first()
            if row is not None:
                binding, provider = row
                if binding is not None and provider is not None:
                    return {
                        "provider_type": provider.provider_type,
                        "base_url": provider.base_url or "",
                        "api_key": provider.api_key_enc or "",
                        "model": binding.model_name,
                        "capabilities": binding.capabilities or {},
                    }
        except Exception:
            logger.exception(
                "Agent %s 查询 ModelBinding 失败，降级到 ProjectConfig",
                self.agent_name,
            )

        # 3: 降级到 ProjectConfig（旧逻辑）
        try:
            stmt = select(ProjectConfig).where(
                ProjectConfig.project_id == self.project_id,
                ProjectConfig.key == "provider",
            )
            result = await self.db.execute(stmt)
            config_row = result.scalar_one_or_none()
            if config_row and config_row.value:
                return config_row.value
        except Exception:
            logger.exception(
                "Agent %s 查询 ProjectConfig 失败", self.agent_name
            )

        # 4: 返回 None，由 Gateway 使用默认配置
        return None

    # ------------------------------------------------------------------
    # LLM 调用
    # ------------------------------------------------------------------
    async def _get_is_reasoning(self) -> bool:
        """从 provider_config 中读取 capabilities.is_reasoning 标记。"""
        provider_config = await self._get_provider_config()
        if provider_config is None:
            return False
        return bool((provider_config.get("capabilities") or {}).get("is_reasoning"))

    async def _llm_complete_raw(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[dict] = None,
        is_reasoning_model: bool = False,
    ) -> LLMResponse:
        """调用 LLM 并记录 AgentRun，返回完整 LLMResponse。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户提示词。
            temperature: 采样温度。
            max_tokens: 最大生成 token 数。
            response_format: 响应格式（如 ``{"type": "json_object"}``）。
            is_reasoning_model: 是否为推理模型，控制 payload 构造方式。

        Returns:
            LLMResponse 响应结果。
        """
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        request = LLMRequest(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            is_reasoning_model=is_reasoning_model,
        )

        provider_config = await self._get_provider_config()
        started_at = datetime.now(timezone.utc)
        start_ts = time.monotonic()

        try:
            response = await self.gateway.complete(request, provider_config)
            duration_ms = int((time.monotonic() - start_ts) * 1000)
            await self._save_agent_run(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                result={
                    "content": (response.content or "")[:2000],
                    "model": response.model,
                },
                error=None,
                started_at=started_at,
                duration_ms=duration_ms,
            )
            return response

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_ts) * 1000)
            logger.exception("Agent %s LLM 调用失败", self.agent_name)
            await self._save_agent_run(
                input_tokens=0,
                output_tokens=0,
                result={},
                error=str(exc),
                started_at=started_at,
                duration_ms=duration_ms,
            )
            raise

    async def _llm_complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[dict] = None,
    ) -> str:
        """调用 LLM 并返回 content 文本（向后兼容）。

        对推理模型自动合并 content + reasoning_content。
        如果设置了 custom_system_prompt，会追加到系统提示词后面。
        """
        # 注入项目专属自定义系统提示词
        full_system = system_prompt
        if self.custom_system_prompt:
            full_system = (
                f"{system_prompt}\n\n"
                f"【项目专属创作指令】\n{self.custom_system_prompt}"
            )

        is_reasoning = await self._get_is_reasoning()
        response = await self._llm_complete_raw(
            system_prompt=full_system,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            is_reasoning_model=is_reasoning,
        )
        content = response.content or ""
        if not content and response.reasoning_content:
            content = response.reasoning_content
        return content

    async def _llm_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        is_reasoning_model: Optional[bool] = None,
    ) -> dict:
        """调用 LLM 并解析 JSON 响应，兼容推理模型和普通模型。

        对推理模型：
        - system_prompt 末尾追加 JSON_OUTPUT_CONTRACT（先思考再输出 JSON）
        - 不传 response_format（部分推理模型不支持会返回空）
        - 同时尝试 content 和 reasoning_content 两个字段

        对普通模型：
        - 传 response_format={"type": "json_object"}
        - 读取 content 字段

        JSON 解析策略（多级降级）：
        1. 先尝试标记后内容 (===FINAL_JSON===)
        2. 再尝试整段文本
        3. 先 json.loads，失败再用括号配对 extract_json_block

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户提示词。
            temperature: 采样温度（默认 0.3）。
            is_reasoning_model: 显式指定是否推理模型，None 则自动读取 capabilities。

        Returns:
            解析后的 JSON dict。

        Raises:
            json.JSONDecodeError: 所有候选字段均无法解析出 JSON。
        """
        if is_reasoning_model is None:
            is_reasoning_model = await self._get_is_reasoning()

        # 注入项目专属自定义系统提示词（类似 Gemini Gems）
        full_system = system_prompt
        if self.custom_system_prompt:
            full_system = (
                f"{system_prompt}\n\n"
                f"【项目专属创作指令】\n{self.custom_system_prompt}"
            )
        if is_reasoning_model:
            full_system = full_system + JSON_OUTPUT_CONTRACT

        response = await self._llm_complete_raw(
            system_prompt=full_system,
            user_prompt=user_prompt,
            temperature=temperature,
            response_format=None if is_reasoning_model else {"type": "json_object"},
            is_reasoning_model=is_reasoning_model,
        )

        # 收集候选文本：content 和 reasoning_content，各试"标记后"和"整段"
        candidates: list[str] = []
        for raw in (response.content, response.reasoning_content):
            if raw and raw.strip():
                candidates.append(extract_after_marker(raw))
                candidates.append(raw)

        last_err: Optional[Exception] = None
        for candidate in candidates:
            try:
                return parse_json_response(candidate)
            except json.JSONDecodeError as exc:
                last_err = exc
                continue

        logger.error(
            "Agent %s 所有候选字段均无法解析出 JSON (candidates=%d, is_reasoning=%s)",
            self.agent_name, len(candidates), is_reasoning_model,
        )
        raise last_err or json.JSONDecodeError("LLM 返回空内容", "", 0)

    # ------------------------------------------------------------------
    # AgentRun 记录
    # ------------------------------------------------------------------
    async def _save_agent_run(
        self,
        input_tokens: int,
        output_tokens: int,
        result: dict[str, Any],
        error: Optional[str] = None,
        started_at: Optional[datetime] = None,
        duration_ms: Optional[int] = None,
    ) -> AgentRun:
        """保存 AgentRun 记录到数据库。

        Args:
            input_tokens: 输入 token 数。
            output_tokens: 输出 token 数。
            result: Agent 执行结果。
            error: 错误信息（如有）。
            started_at: 开始时间。
            duration_ms: 执行耗时（毫秒）。

        Returns:
            创建的 AgentRun 实例。
        """
        now = datetime.now(timezone.utc)
        status = "failed" if error else "success"

        run = AgentRun(
            project_id=self.project_id,
            session_id=self.session_id,
            agent_name=self.agent_name,
            status=status,
            started_at=started_at or now,
            finished_at=now,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=0.0,  # Phase 0 暂不计算成本
            result=result,
            error=error,
        )
        self.db.add(run)
        await self.db.flush()
        return run
