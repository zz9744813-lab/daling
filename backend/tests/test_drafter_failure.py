"""测试 Drafter 失败处理 — 失败不返回占位正文。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.domain.errors import AgentExecutionError, EmptyResultError, TruncationError


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


def _fallback_drafter():
    """Build a Drafter whose context reads cannot touch a database."""
    from app.agents.drafter import Drafter

    drafter = Drafter(
        gateway=MagicMock(),
        db=MagicMock(),
        project_id="test-project",
        session_id=None,
    )
    drafter._get_world_summary = AsyncMock(return_value="世界观")
    drafter._get_characters_info = AsyncMock(return_value="角色信息")
    drafter._get_previous_text = AsyncMock(return_value="前章最后一句")
    return drafter


def _fallback_plan(*, target_words: int = 800):
    return {
        "chapter_no": 2,
        "chapter_title": "断桥之后",
        "overall_goal": "主角找到线索并决定追查",
        "pov": "林舟",
        "ending_hook": "门外响起失踪者的声音",
        "scene_list": [
            {
                "scene_no": 1,
                "summary": "林舟检查断桥下的遗留物",
                "characters": ["林舟"],
                "location": "河滩",
                "mood": "紧张",
                "plot_advancement": "发现带血的钥匙",
                "target_words": target_words,
            },
            {
                "scene_no": 2,
                "summary": "林舟回到住处验证钥匙",
                "characters": ["林舟"],
                "location": "旧宅",
                "mood": "压迫",
                "plot_advancement": "钥匙打开密室",
                "target_words": target_words,
            },
        ],
        "_compiled_context": {
            "system_prompt": "不可改写的 Canon：钥匙为黄铜材质。",
            "context_text": "长期上下文：林舟左手受伤。",
        },
    }


@pytest.mark.asyncio
async def test_whole_chapter_truncation_falls_back_to_atomic_scene_generation():
    """A truncated whole draft is discarded; ordered scenes are joined only at the end."""
    drafter = _fallback_drafter()
    calls = []
    first_scene = "林舟踩进冰冷河水。" + "甲" * 520 + "他握紧了带血的黄铜钥匙。"
    second_scene = "钥匙触到锁孔。" + "乙" * 520 + "门外忽然响起失踪者的声音。"

    async def complete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TruncationError("整章截断", agent_name="Drafter")
        if len(calls) == 2:
            return first_scene
        return second_scene

    drafter._llm_complete = AsyncMock(side_effect=complete)

    blocks = await drafter.draft_chapter(_fallback_plan(), chapter_id="chapter-id")
    visible_text = "\n".join(block.content for block in blocks if block.content)

    assert len(calls) == 3
    assert first_scene in visible_text
    assert second_scene in visible_text
    assert "整章截断" not in visible_text
    assert any(block.block_type == "scene_break" for block in blocks)
    assert "不可改写的 Canon" in calls[1]["system_prompt"]
    assert "长期上下文：林舟左手受伤" in calls[1]["user_prompt"]
    assert "他握紧了带血的黄铜钥匙" in calls[2]["user_prompt"]
    assert "不得重写、复述或推翻" in calls[2]["user_prompt"]
    drafter.db.add.assert_not_called()


@pytest.mark.asyncio
async def test_scene_fallback_failure_never_constructs_or_persists_partial_blocks():
    """If any later scene fails, successful earlier prose never becomes manuscript blocks."""
    drafter = _fallback_drafter()
    drafter._split_into_blocks = MagicMock()
    calls = 0

    async def complete(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TruncationError("整章截断", agent_name="Drafter")
        if calls == 2:
            return "第一场正文" + "甲" * 600
        raise TruncationError("第二场仍截断", agent_name="Drafter")

    drafter._llm_complete = AsyncMock(side_effect=complete)

    with pytest.raises(TruncationError, match="第 2 场仍达到输出上限"):
        await drafter.draft_chapter(_fallback_plan(), chapter_id="chapter-id")

    drafter._split_into_blocks.assert_not_called()
    drafter.db.add.assert_not_called()


@pytest.mark.asyncio
async def test_scene_fallback_rejects_implausibly_short_merged_chapter():
    """Individually non-empty scenes must also satisfy the chapter-level target."""
    drafter = _fallback_drafter()
    drafter._split_into_blocks = MagicMock()
    responses = [
        TruncationError("整章截断", agent_name="Drafter"),
        "甲" * 700,
        "乙" * 700,
    ]
    drafter._llm_complete = AsyncMock(side_effect=responses)

    with pytest.raises(EmptyResultError, match="合并稿总长异常"):
        await drafter.draft_chapter(
            _fallback_plan(target_words=2000),
            chapter_id="chapter-id",
        )

    drafter._split_into_blocks.assert_not_called()
    drafter.db.add.assert_not_called()


@pytest.mark.asyncio
async def test_non_truncation_failure_does_not_trigger_scene_fallback():
    """Network/auth/other failures stay failures instead of multiplying requests."""
    drafter = _fallback_drafter()
    drafter.draft_scene = AsyncMock()
    drafter._llm_complete = AsyncMock(side_effect=RuntimeError("认证失败"))

    with pytest.raises(AgentExecutionError, match="LLM 调用失败"):
        await drafter.draft_chapter(_fallback_plan(), chapter_id="chapter-id")

    drafter._llm_complete.assert_awaited_once()
    drafter.draft_scene.assert_not_awaited()
