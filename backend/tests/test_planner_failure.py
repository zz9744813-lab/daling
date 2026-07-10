"""测试 ChapterPlanner 失败处理 — 失败不返回空计划。

验证 AGENTS.md 规则第 5/6 条：
- 不允许用默认成功值掩盖异常
- 不允许 LLM 失败后生成占位正文并继续
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.errors import AgentExecutionError, EmptyResultError


@pytest.mark.asyncio
async def test_planner_llm_failure_raises_exception():
    """ChapterPlanner LLM 调用失败时应抛出 AgentExecutionError。"""
    from app.agents.chapter_planner import ChapterPlanner

    planner = ChapterPlanner(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_json 抛出异常
    planner._llm_json = AsyncMock(side_effect=RuntimeError("LLM 连接超时"))

    # mock 辅助方法（避免 AsyncMock 子属性返回 coroutine）
    planner._get_beat_info = AsyncMock(return_value=None)
    planner._get_previous_summaries = AsyncMock(return_value=[])
    planner._get_character_states = AsyncMock(return_value=[])
    planner._get_story_state = AsyncMock(return_value=None)
    planner._get_foreshadows = AsyncMock(return_value=[])

    with pytest.raises(AgentExecutionError) as exc_info:
        await planner.plan_chapter(chapter_no=1)

    assert "ChapterPlanner" in str(exc_info.value)
    assert exc_info.value.cause is not None


@pytest.mark.asyncio
async def test_planner_empty_scene_list_raises_exception():
    """ChapterPlanner 返回空场景列表时应抛出 EmptyResultError。"""
    from app.agents.chapter_planner import ChapterPlanner

    planner = ChapterPlanner(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_json 返回空场景列表
    planner._llm_json = AsyncMock(return_value={
        "chapter_no": 1,
        "chapter_title": "测试章节",
        "scene_list": [],  # 空场景列表
        "overall_goal": "测试目标",
        "ending_hook": "测试钩子",
    })

    # mock 辅助方法
    planner._get_beat_info = AsyncMock(return_value=None)
    planner._get_previous_summaries = AsyncMock(return_value=[])
    planner._get_character_states = AsyncMock(return_value=[])
    planner._get_story_state = AsyncMock(return_value=None)
    planner._get_foreshadows = AsyncMock(return_value=[])

    with pytest.raises(EmptyResultError):
        await planner.plan_chapter(chapter_no=1)
