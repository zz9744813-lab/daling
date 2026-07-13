"""Long-form drafting must use the real streaming gateway path."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.agents.drafter import Drafter
from app.domain.errors import TruncationError
from app.model_gateway import ModelOutputTruncatedError


class FakeStreamingGateway:
    async def stream_complete(self, request, provider_config=None):
        assert request.stream is True
        assert request.max_tokens == 8192
        assert provider_config == {"model": "test-model"}
        for chunk in ("第一段。", "第二段。", "第三段。"):
            yield chunk


class FakeCappedStreamingGateway:
    async def stream_complete(self, request, provider_config=None):
        assert request.max_tokens == 4096
        yield "受上限约束的正文"


class FakeTruncatedStreamingGateway:
    async def stream_complete(self, request, provider_config=None):
        yield "这段不完整正文绝不能被采用"
        raise ModelOutputTruncatedError("finish_reason=length")


@pytest.mark.asyncio
async def test_long_text_stream_is_joined_and_accounted():
    agent = Drafter(
        db=MagicMock(),
        gateway=FakeStreamingGateway(),
        project_id="00000000-0000-0000-0000-000000000001",
    )
    agent._get_provider_config = AsyncMock(return_value={"model": "test-model"})
    agent._save_agent_run = AsyncMock()

    text = await agent._llm_stream_text(
        system_prompt="system",
        user_prompt="user",
        temperature=0.8,
        max_tokens=8192,
        is_reasoning_model=False,
    )

    assert text == "第一段。第二段。第三段。"
    agent._save_agent_run.assert_awaited_once()
    assert agent._save_agent_run.await_args.kwargs["result"]["streamed"] is True


@pytest.mark.asyncio
async def test_binding_max_output_is_a_hard_streaming_ceiling():
    agent = Drafter(
        db=MagicMock(),
        gateway=FakeCappedStreamingGateway(),
        project_id="00000000-0000-0000-0000-000000000001",
    )
    agent._get_provider_config = AsyncMock(
        return_value={"model": "test-model", "max_output_tokens": 4096}
    )
    agent._save_agent_run = AsyncMock()

    text = await agent._llm_stream_text(
        system_prompt="system",
        user_prompt="user",
        temperature=0.8,
        max_tokens=12_288,
        is_reasoning_model=True,
    )

    assert text == "受上限约束的正文"


@pytest.mark.asyncio
async def test_streamed_length_limit_is_typed_and_partial_text_is_only_a_failed_audit():
    agent = Drafter(
        db=MagicMock(),
        gateway=FakeTruncatedStreamingGateway(),
        project_id="00000000-0000-0000-0000-000000000001",
    )
    agent._get_provider_config = AsyncMock(return_value={"model": "test-model"})
    agent._save_agent_run = AsyncMock()

    with pytest.raises(TruncationError, match="已丢弃不完整结果"):
        await agent._llm_stream_text(
            system_prompt="system",
            user_prompt="user",
            temperature=0.8,
            max_tokens=16_384,
            is_reasoning_model=True,
        )

    agent._save_agent_run.assert_awaited_once()
    audit = agent._save_agent_run.await_args.kwargs
    assert audit["error"] == "finish_reason=length"
    assert audit["result"]["content"] == "这段不完整正文绝不能被采用"
