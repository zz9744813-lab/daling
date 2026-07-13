"""Structured reasoning agents must request enough output without bypassing bindings."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from app.agents.story_architect import StoryArchitect
from app.model_gateway import LLMResponse


@pytest.mark.asyncio
async def test_json_agent_requests_full_reasoning_budget():
    agent = StoryArchitect(None, None, uuid.uuid4())  # type: ignore[arg-type]
    agent._get_is_reasoning = AsyncMock(return_value=True)
    agent._llm_complete_raw = AsyncMock(
        return_value=LLMResponse(
            content='===FINAL_JSON===\n{"ok": true}',
            model="reasoning-model",
        )
    )

    result = await agent._llm_json(system_prompt="system", user_prompt="user")

    assert result == {"ok": True}
    assert agent._llm_complete_raw.await_args.kwargs["max_tokens"] == 16_384
