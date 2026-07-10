"""测试 Critic 失败处理 — 失败不默认 75 分。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.errors import QualityCheckError, EmptyResultError


@pytest.mark.asyncio
async def test_critic_llm_failure_raises_exception():
    """Critic LLM 调用失败时应抛出 QualityCheckError，不返回默认 75 分。"""
    from app.agents.critic import Critic

    critic = Critic(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_json 抛出异常
    critic._llm_json = AsyncMock(side_effect=RuntimeError("LLM 速率限制"))

    # mock 辅助方法
    critic._get_characters_info = AsyncMock(return_value="角色信息")

    block_texts = [{"content": "测试正文", "block_type": "narrative", "block_no": 0}]
    plan = {"chapter_no": 1, "chapter_title": "测试", "overall_goal": "目标"}

    with pytest.raises(QualityCheckError) as exc_info:
        await critic.review_texts(block_texts, plan)

    assert "Critic" in str(exc_info.value)


@pytest.mark.asyncio
async def test_critic_no_scores_raises_exception():
    """Critic 返回空评分时应抛出 EmptyResultError，不填充默认 75 分。"""
    from app.agents.critic import Critic

    critic = Critic(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_json 返回空评分
    critic._llm_json = AsyncMock(return_value={
        "scores": {},
        "overall_score": None,
        "issues": [],
    })

    # mock 辅助方法
    critic._get_characters_info = AsyncMock(return_value="角色信息")

    block_texts = [{"content": "测试正文", "block_type": "narrative", "block_no": 0}]
    plan = {"chapter_no": 1, "chapter_title": "测试", "overall_goal": "目标"}

    with pytest.raises(EmptyResultError):
        await critic.review_texts(block_texts, plan)
