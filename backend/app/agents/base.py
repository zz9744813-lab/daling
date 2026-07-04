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


def parse_json_response(text: str) -> dict:
    """解析 LLM 返回的 JSON 文本，处理 markdown 包裹。

    Args:
        text: LLM 返回的原始文本。

    Returns:
        解析后的 dict。

    Raises:
        json.JSONDecodeError: JSON 解析失败。
    """
    cleaned = strip_json_markdown(text)
    return json.loads(cleaned)


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
    async def _llm_complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[dict] = None,
    ) -> str:
        """调用 LLM 并记录 AgentRun。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户提示词。
            temperature: 采样温度。
            max_tokens: 最大生成 token 数。
            response_format: 响应格式（如 ``{"type": "json_object"}``）。

        Returns:
            LLM 生成的文本内容。
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
        )

        provider_config = await self._get_provider_config()
        started_at = datetime.now(timezone.utc)
        start_ts = time.monotonic()

        try:
            response: LLMResponse = await self.gateway.complete(request, provider_config)
            duration_ms = int((time.monotonic() - start_ts) * 1000)

            await self._save_agent_run(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                result={"content": response.content[:2000], "model": response.model},
                error=None,
                started_at=started_at,
                duration_ms=duration_ms,
            )
            return response.content

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

    async def _llm_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
    ) -> dict:
        """调用 LLM 并解析 JSON 响应。

        使用较低的 temperature（默认 0.3）以提高 JSON 输出的稳定性。
        自动处理 markdown 代码块包裹。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户提示词。
            temperature: 采样温度（默认 0.3，适合结构化输出）。

        Returns:
            解析后的 JSON dict。

        Raises:
            json.JSONDecodeError: JSON 解析失败。
        """
        content = await self._llm_complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return parse_json_response(content)

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
