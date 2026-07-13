"""Focused tests for chat-first project creation."""

from __future__ import annotations

import app.api.routes_chat_create as chat_routes
import pytest
from app.core.database import Base
from app.db.models.project import Project
from app.db.models.provider import LlmProvider, ModelBinding
from app.model_gateway.base import BaseProvider, LLMRequest, LLMResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class FakeProvider(BaseProvider):
    def __init__(self, responses: list[LLMResponse], deltas: list[str] | None = None):
        self.responses = list(responses)
        self.deltas = deltas or []
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return self.responses.pop(0)

    async def stream_complete(self, request: LLMRequest):
        self.requests.append(request)
        for delta in self.deltas:
            yield delta


def test_blueprint_parser_handles_fences_aliases_and_non_strict_json():
    text = """模型分析如下：
```json
{
  "config": {
    "bookTitle": "余烬天门",
    "type": "末世修仙",
    "main_character": {"name": "林砚", "identity": "废柴学徒"},
    "goal": "阻止灵潮吞没城市",
    "conflict": "传承会逐步抹去他的情感",
    "world_setting": "灵气复苏与科技并存的现代都市",
    "style": "冷峻、热血",
    "chapter_words": "3,200 字",
    "total_chapters": "120章",
    "point_of_view": "第三人称限制",
  },
  "assumptions": ["默认中文创作"],
}
```
以上为当前蓝图。"""
    assessment = chat_routes._parse_blueprint_response(text)

    assert assessment.config["title"] == "余烬天门"
    assert assessment.config["genre"] == "末世修仙"
    assert assessment.config["words_per_chapter"] == 3200
    assert assessment.config["target_chapters"] == 120
    assert "chapter_words" not in assessment.config
    assert assessment.readiness > 50
    assert assessment.assumptions == ["默认中文创作"]


def test_readiness_depends_on_information_not_message_count():
    sparse = chat_routes.NovelBlueprint(creative_prompt="我想写一个末世故事")
    complete = chat_routes.NovelBlueprint(
        title="余烬天门",
        premise="灵气复苏后，一个普通人必须关闭吞噬城市的天门。",
        protagonist="被逐出宗门的林砚",
        protagonist_goal="救回妹妹并关闭天门",
        core_conflict="使用传承会逐步失去人性",
        genre="末世修仙",
        setting="灵气与科技共存的现代都市",
        tone="冷峻热血",
        audience="男频长篇读者",
        target_chapters=120,
        pov="第三人称限制",
    )

    sparse_score, sparse_missing = chat_routes._calculate_readiness(sparse)
    complete_score, complete_missing = chat_routes._calculate_readiness(complete)
    assert sparse_score == 20
    assert "protagonist" in sparse_missing
    assert complete_score == 100
    assert complete_missing == []


def test_blueprint_parser_reuses_equivalent_story_fields():
    assessment = chat_routes._parse_blueprint_response(
        '{"config":{"premise":"普通县令用制度对抗仙门垄断",'
        '"antagonist":"垄断资源与晋升通道的仙门联盟",'
        '"genre":"慢热东方幻想与治理经营"}}'
    )

    assert assessment.config["logline"] == "普通县令用制度对抗仙门垄断"
    assert assessment.config["core_conflict"] == "垄断资源与晋升通道的仙门联盟"
    assert assessment.config["pacing"] == "慢热推进，重视长期积累与阶段性变化"


def test_model_reasoning_detection_matches_observed_gateway_shapes():
    assert chat_routes._model_looks_reasoning("stepfun-ai/step-3.7-flash") is True
    assert chat_routes._model_looks_reasoning("z-ai/glm-5.2") is False


@pytest.mark.asyncio
async def test_compat_chat_and_extract_use_fake_provider(monkeypatch):
    blueprint_json = (
        '前置说明\n```json\n{"config":{"title":"雾城","genre":"悬疑",'
        '"premise":"记者追查一座每天失忆的城市"}}\n```'
    )
    provider = FakeProvider(
        [
            LLMResponse(content="这个设定很抓人。主角最害怕失去什么？", model="fake"),
            LLMResponse(content=blueprint_json, model="fake"),
        ]
    )
    resolved = chat_routes.ResolvedChatLLM(provider, "fake", "test", {})

    async def fake_resolve():
        return resolved

    monkeypatch.setattr(chat_routes, "_resolve_chat_llm", fake_resolve)
    request = chat_routes.ChatCreateRequest(
        messages=[{"role": "user", "content": "我想写一座每天失忆的城市"}]
    )
    reply = await chat_routes.chat_create(request)
    assert reply.reply.endswith("？")

    extracted = await chat_routes.chat_create(
        chat_routes.ChatCreateRequest(messages=request.messages, extract=True)
    )
    assert extracted.config["title"] == "雾城"
    assert extracted.readiness == 35
    assert extracted.missing_fields


@pytest.mark.asyncio
async def test_stream_emits_delta_blueprint_and_done(monkeypatch):
    provider = FakeProvider(
        [
            LLMResponse(
                content='{"config":{"premise":"危城求生","protagonist":"林砚"}}',
                model="fake",
            )
        ],
        deltas=["先确定核心代价。", "主角每次使用力量会失去什么？"],
    )
    resolved = chat_routes.ResolvedChatLLM(provider, "fake", "test", {})

    async def fake_resolve():
        return resolved

    monkeypatch.setattr(chat_routes, "_resolve_chat_llm", fake_resolve)
    request = chat_routes.ChatCreateRequest(
        messages=[{"role": "user", "content": "我想写末世异能"}]
    )
    events = [event async for event in chat_routes._chat_event_stream(request)]
    assert events[0].startswith("event: delta")
    assert any(event.startswith("event: blueprint") for event in events)
    assert events[-1] == 'event: done\ndata: {"ok": true}\n\n'


@pytest.mark.asyncio
async def test_extraction_preserves_current_blueprint_when_model_omits_fields():
    provider = FakeProvider([LLMResponse(content='{"config":{"tone":"冷静克制"}}', model="fake")])
    resolved = chat_routes.ResolvedChatLLM(provider, "fake", "test", {})
    current = {
        "title": "凡土长明",
        "logline": "无灵根县令以制度改变边地命运",
        "protagonist_desire": "让普通人拥有长期生存能力",
        "core_conflict": "仙门垄断资源与晋升通道",
        "world_setting": "仙凡秩序森严的边远县域",
    }

    assessment = await chat_routes._extract_blueprint(
        resolved,
        [chat_routes.ChatMessage(role="user", content="文风要冷静克制")],
        current,
    )

    assert assessment.config["title"] == "凡土长明"
    assert assessment.config["logline"] == "无灵根县令以制度改变边地命运"
    assert assessment.config["protagonist_goal"] == "让普通人拥有长期生存能力"
    assert assessment.config["core_conflict"] == "仙门垄断资源与晋升通道"
    assert assessment.config["setting"] == "仙凡秩序森严的边远县域"
    assert assessment.config["tone"] == "冷静克制"


@pytest.mark.asyncio
async def test_provider_selection_ignores_project_binding_and_inactive_provider(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        project = Project(title="other-project")
        active = LlmProvider(
            name="active",
            provider_type="openai_compatible",
            base_url="https://example.test/v1",
            api_key_enc="active-key",
            is_active=True,
            default_model=None,
        )
        inactive = LlmProvider(
            name="inactive",
            provider_type="openai_compatible",
            base_url="https://example.test/v1",
            api_key_enc="inactive-key",
            is_active=False,
            default_model="inactive-model",
        )
        session.add_all([project, active, inactive])
        await session.flush()
        session.add_all(
            [
                ModelBinding(
                    provider_id=active.id,
                    project_id=project.id,
                    model_name="project-only-model",
                    is_default=True,
                ),
                ModelBinding(
                    provider_id=active.id,
                    project_id=None,
                    model_name="z-ai/glm-5.2",
                    is_default=False,
                ),
                ModelBinding(
                    provider_id=inactive.id,
                    project_id=None,
                    model_name="inactive-model",
                    is_default=True,
                ),
            ]
        )
        await session.commit()

    monkeypatch.setattr(chat_routes, "async_session_factory", factory)
    resolved = await chat_routes._resolve_from_database()
    assert resolved is not None
    assert resolved.model == "z-ai/glm-5.2"
    assert resolved.source == "database_binding"
    await engine.dispose()


def test_reasoning_without_confirmed_final_is_not_returned():
    with pytest.raises(RuntimeError, match="推理过程"):
        chat_routes._visible_response(
            LLMResponse(
                content="",
                reasoning_content="仍在分析故事结构",
                finish_reason="length",
                model="stepfun-ai/step-3.7-flash",
            )
        )
