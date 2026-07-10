"""测试 ContinuityGuard 失败处理 — 失败不默认 passed=True。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.errors import QualityCheckError, EmptyResultError


@pytest.mark.asyncio
async def test_continuity_llm_failure_raises_exception():
    """ContinuityGuard LLM 调用失败时应抛出 QualityCheckError，不返回 passed=True。"""
    from app.agents.continuity_guard import ContinuityGuard

    guard = ContinuityGuard(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_json 抛出异常
    guard._llm_json = AsyncMock(side_effect=RuntimeError("LLM 网络错误"))

    # mock 辅助方法
    guard._get_world_summary = AsyncMock(return_value="世界观")
    guard._get_previous_summaries = AsyncMock(return_value=[])
    guard._get_characters_info = AsyncMock(return_value="角色信息")
    guard._get_foreshadows = AsyncMock(return_value=[])

    block_texts = [{"content": "测试正文", "block_type": "narrative", "block_no": 0}]

    with pytest.raises(QualityCheckError) as exc_info:
        await guard.check_texts(block_texts, chapter_no=1)

    assert "ContinuityGuard" in str(exc_info.value) or "continuity" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_continuity_missing_passed_field_raises_exception():
    """ContinuityGuard 返回缺少 passed 字段时应抛出 EmptyResultError。"""
    from app.agents.continuity_guard import ContinuityGuard

    guard = ContinuityGuard(
        gateway=MagicMock(),
        db=AsyncMock(),
        project_id="test-project",
        session_id=None,
    )

    # mock _llm_json 返回缺少 passed 字段的结果
    guard._llm_json = AsyncMock(return_value={
        "conflicts": [],
        "warnings": [],
        # 没有 passed 字段
    })

    # mock 辅助方法
    guard._get_world_summary = AsyncMock(return_value="世界观")
    guard._get_previous_summaries = AsyncMock(return_value=[])
    guard._get_characters_info = AsyncMock(return_value="角色信息")
    guard._get_foreshadows = AsyncMock(return_value=[])

    block_texts = [{"content": "测试正文", "block_type": "narrative", "block_no": 0}]

    with pytest.raises(EmptyResultError):
        await guard.check_texts(block_texts, chapter_no=1)
