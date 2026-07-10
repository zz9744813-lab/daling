"""测试 MemoryKeeper 失败处理 — 不使用空数据更新状态。"""
import pytest
from unittest.mock import AsyncMock, MagicMock
import uuid

from app.domain.errors import AgentExecutionError, EmptyResultError


@pytest.mark.asyncio
async def test_memory_keeper_llm_failure_raises_exception():
    """MemoryKeeper LLM 调用失败时应抛出 AgentExecutionError，不使用空数据。"""
    from app.agents.memory_keeper import MemoryKeeper

    keeper = MemoryKeeper(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_json 抛出异常
    keeper._llm_json = AsyncMock(side_effect=RuntimeError("LLM 服务不可用"))

    # mock 辅助方法
    keeper._get_characters_info = AsyncMock(return_value="角色信息")
    keeper._get_known_facts = AsyncMock(return_value=[])

    # mock chapter
    mock_chapter = MagicMock()
    mock_chapter.id = uuid.uuid4()
    mock_chapter.chapter_no = 1
    keeper.db.get = AsyncMock(return_value=mock_chapter)

    blocks = [{"content": "测试正文内容", "block_type": "narrative", "block_no": 0}]

    with pytest.raises(AgentExecutionError):
        await keeper.update_state(mock_chapter.id, blocks)


@pytest.mark.asyncio
async def test_memory_keeper_empty_summary_raises_exception():
    """MemoryKeeper 返回空摘要时应抛出 EmptyResultError，不写入空数据。"""
    from app.agents.memory_keeper import MemoryKeeper

    keeper = MemoryKeeper(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_json 返回空摘要
    keeper._llm_json = AsyncMock(return_value={
        "summary": "",
        "entities_involved": [],
        "facts_asserted": [],
    })

    # mock 辅助方法
    keeper._get_characters_info = AsyncMock(return_value="角色信息")
    keeper._get_known_facts = AsyncMock(return_value=[])

    # mock chapter
    mock_chapter = MagicMock()
    mock_chapter.id = uuid.uuid4()
    mock_chapter.chapter_no = 1
    keeper.db.get = AsyncMock(return_value=mock_chapter)

    blocks = [{"content": "测试正文内容", "block_type": "narrative", "block_no": 0}]

    with pytest.raises(EmptyResultError):
        await keeper.update_state(mock_chapter.id, blocks)
