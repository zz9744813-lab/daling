"""测试 Rewriter 失败处理 — 空结果不删除旧版本。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.errors import AgentExecutionError, EmptyResultError


@pytest.mark.asyncio
async def test_rewriter_llm_failure_raises_exception():
    """Rewriter LLM 调用失败时应抛出 AgentExecutionError，不返回空列表。"""
    from app.agents.rewriter import Rewriter

    rewriter = Rewriter(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_complete 抛出异常
    rewriter._llm_complete = AsyncMock(side_effect=RuntimeError("LLM 超时"))

    # mock 辅助方法
    rewriter._get_characters_info = AsyncMock(return_value="角色信息")

    block_texts = [{"content": "原文", "block_type": "narrative", "block_no": 0}]
    issues = [{"severity": "high", "description": "测试问题"}]
    plan = {"chapter_no": 1, "chapter_title": "测试", "overall_goal": "目标"}

    with pytest.raises(AgentExecutionError) as exc_info:
        await rewriter.rewrite_texts(block_texts, issues, plan, chapter_id="test-chapter-id")

    assert "Rewriter" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rewriter_empty_response_raises_exception():
    """Rewriter LLM 返回空正文时应抛出 EmptyResultError，不返回空列表。"""
    from app.agents.rewriter import Rewriter

    rewriter = Rewriter(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_complete 返回空字符串
    rewriter._llm_complete = AsyncMock(return_value="")

    # mock 辅助方法
    rewriter._get_characters_info = AsyncMock(return_value="角色信息")

    block_texts = [{"content": "原文", "block_type": "narrative", "block_no": 0}]
    issues = [{"severity": "high", "description": "测试问题"}]
    plan = {"chapter_no": 1, "chapter_title": "测试", "overall_goal": "目标"}

    with pytest.raises(EmptyResultError):
        await rewriter.rewrite_texts(block_texts, issues, plan, chapter_id="test-chapter-id")
