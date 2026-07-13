"""Long uploaded outlines must influence both architecture prompts end to end."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from app.agents.story_architect import StoryArchitect, _compact_outline


class PromptCaptured(RuntimeError):
    pass


def _long_outline() -> str:
    return "".join(
        [
            "HEAD_SENTINEL：主角名为沈砚，世界规则是记忆不可复生。\n",
            "开篇铺垫。" * 5_000,
            "\n第十八卷 中部转折\nMIDDLE_SENTINEL：盟友在此卷揭示双重身份。\n",
            "中段发展。" * 5_000,
            "\n终章 天亮之前\nTAIL_SENTINEL：结局必须由沈砚主动销毁永生装置，且不可复活。\n",
        ]
    )


def test_outline_compaction_is_bounded_deterministic_and_preserves_whole_book():
    source = _long_outline()

    first = _compact_outline(source, 30_000)
    second = _compact_outline(source, 30_000)

    assert first == second
    assert len(first) <= 30_000
    assert "HEAD_SENTINEL" in first
    assert "MIDDLE_SENTINEL" in first
    assert "TAIL_SENTINEL" in first
    assert first.endswith("且不可复活。\n")


@pytest.mark.asyncio
async def test_bible_and_structure_prompts_both_receive_tail_constraints():
    source = _long_outline()
    prompts: list[str] = []
    architect = StoryArchitect(None, None, uuid.uuid4())  # type: ignore[arg-type]

    async def capture(**kwargs):
        prompts.append(kwargs["user_prompt"])
        raise PromptCaptured

    architect._llm_json = capture  # type: ignore[method-assign]

    with pytest.raises(PromptCaptured):
        await architect.generate_world_bible({"outline_text": source, "title": "全篇约束测试"})

    bible = SimpleNamespace(summary="测试世界观", content={})
    with pytest.raises(PromptCaptured):
        await architect.generate_outline(bible, hints={"outline_text": source})

    assert len(prompts) == 2
    for prompt in prompts:
        assert "HEAD_SENTINEL" in prompt
        assert "MIDDLE_SENTINEL" in prompt
        assert "TAIL_SENTINEL" in prompt
        assert "大纲全篇压缩视图" in prompt
