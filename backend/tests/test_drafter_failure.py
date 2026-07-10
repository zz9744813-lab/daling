"""测试 Drafter 失败处理 — 失败不返回占位正文。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.errors import AgentExecutionError, EmptyResultError


@pytest.mark.asyncio
async def test_drafter_llm_failure_raises_exception():
    """Drafter LLM 调用失败时应抛出 AgentExecutionError，不返回占位文本。"""
    from app.agents.drafter import Drafter

    drafter = Drafter(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_complete 抛出异常
    drafter._llm_complete = AsyncMock(side_effect=RuntimeError("LLM 认证失败"))

    # mock 辅助方法
    drafter._get_world_summary = AsyncMock(return_value="世界观")
    drafter._get_characters_info = AsyncMock(return_value="角色信息")
    drafter._get_previous_text = AsyncMock(return_value="前文")

    plan = {"chapter_no": 1, "chapter_title": "测试", "scene_list": [], "overall_goal": "目标"}

    with pytest.raises(AgentExecutionError) as exc_info:
        await drafter.draft_chapter(plan, chapter_id="test-chapter-id")

    assert "Drafter" in str(exc_info.value)


@pytest.mark.asyncio
async def test_drafter_empty_response_raises_exception():
    """Drafter LLM 返回空正文时应抛出 EmptyResultError。"""
    from app.agents.drafter import Drafter

    drafter = Drafter(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_complete 返回空字符串
    drafter._llm_complete = AsyncMock(return_value="")

    # mock 辅助方法
    drafter._get_world_summary = AsyncMock(return_value="世界观")
    drafter._get_characters_info = AsyncMock(return_value="角色信息")
    drafter._get_previous_text = AsyncMock(return_value="前文")

    plan = {"chapter_no": 1, "chapter_title": "测试", "scene_list": [], "overall_goal": "目标"}

    with pytest.raises(EmptyResultError):
        await drafter.draft_chapter(plan, chapter_id="test-chapter-id")


@pytest.mark.asyncio
async def test_drafter_short_response_raises_exception():
    """Drafter LLM 返回过短正文时应抛出 EmptyResultError。"""
    from app.agents.drafter import Drafter

    drafter = Drafter(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_complete 返回过短文本
    drafter._llm_complete = AsyncMock(return_value="太短了")

    # mock 辅助方法
    drafter._get_world_summary = AsyncMock(return_value="世界观")
    drafter._get_characters_info = AsyncMock(return_value="角色信息")
    drafter._get_previous_text = AsyncMock(return_value="前文")

    plan = {"chapter_no": 1, "chapter_title": "测试", "scene_list": [], "overall_goal": "目标"}

    with pytest.raises(EmptyResultError):
        await drafter.draft_chapter(plan, chapter_id="test-chapter-id")
