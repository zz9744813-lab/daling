"""Chat-first project creation APIs.

Provider discovery, blueprint normalisation and readiness calculation are kept
independent from HTTP handlers so the critical path is testable without a real
LLM call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Mapping, Optional, Sequence

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import async_session_factory
from app.db.models.provider import LlmProvider, ModelBinding
from app.model_gateway.base import BaseProvider, LLMMessage, LLMRequest, LLMResponse
from app.model_gateway.providers.anthropic import AnthropicProvider
from app.model_gateway.providers.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger("app.api.routes_chat_create")

router = APIRouter(prefix="/api/projects", tags=["chat-create"])

CHAT_AGENT_ROLE = "ProjectDesigner"
MAX_CHAT_MESSAGES = 80
MAX_MESSAGE_CHARS = 12_000
DEFAULT_CHAT_TOKENS = 4096
REASONING_CHAT_TOKENS = 8192


class ChatConfigurationError(RuntimeError):
    """Raised when no usable provider/model configuration exists."""


class BlueprintParseError(ValueError):
    """Raised when the model did not return a valid project blueprint."""


class ChatMessage(BaseModel):
    """One user-visible conversation message."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatCreateRequest(BaseModel):
    """Conversation request for both compatibility and streaming APIs."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_CHAT_MESSAGES)
    extract: bool = False
    # 当前右侧可编辑简报。它只用于帮助模型保留作者已经确认/手改的决定，
    # 不会被当作隐藏指令，也不会直接写入数据库。
    blueprint: Optional[dict[str, Any]] = None


class ChatCreateResponse(BaseModel):
    """Backward-compatible non-streaming response."""

    reply: str
    config: Optional[dict[str, Any]] = None
    readiness: Optional[int] = None
    missing_fields: list[str] = Field(default_factory=list)
    suggested_replies: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ChatCreateStatus(BaseModel):
    """Safe provider readiness information (never credentials)."""

    configured: bool
    model: Optional[str] = None
    source: Optional[str] = None


def _coerce_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value).strip()
    return text or None


def _coerce_string_list(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    raw_items: Sequence[Any]
    if isinstance(value, str):
        raw_items = re.split(r"[\n,，、;；|]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    elif isinstance(value, dict):
        raw_items = list(value.values())
    else:
        raw_items = [value]
    result: list[str] = []
    for item in raw_items:
        text = _coerce_text(item)
        if text and text not in result:
            result.append(text)
    return result or None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().lower()
    if not text or text in {"auto", "automatic", "自动", "未定", "null", "none"}:
        return None
    match = re.search(r"-?\d[\d,，]*", text)
    if not match:
        return None
    return int(match.group(0).replace(",", "").replace("，", ""))


_TEXT_FIELDS = (
    "title",
    "logline",
    "genre",
    "audience",
    "platform",
    "language",
    "premise",
    "protagonist",
    "protagonist_goal",
    "flaw",
    "fear",
    "core_conflict",
    "story_question",
    "ability",
    "ability_cost",
    "antagonist",
    "setting",
    "tone",
    "pacing",
    "pov",
    "tense",
    "ending_preference",
    "custom_prompt",
    "creative_prompt",
)
_LIST_FIELDS = (
    "alternate_titles",
    "subgenres",
    "world_rules",
    "themes",
    "content_boundaries",
    "source_material",
)
_INT_FIELDS = ("target_words", "target_chapters", "words_per_chapter", "volume_count")


class NovelBlueprint(BaseModel):
    """Canonical validated blueprint produced by the conversation."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    title: Optional[str] = Field(default=None, max_length=255)
    alternate_titles: Optional[list[str]] = Field(default=None, max_length=8)
    logline: Optional[str] = Field(default=None, max_length=1000)
    genre: Optional[str] = Field(default=None, max_length=100)
    subgenres: Optional[list[str]] = Field(default=None, max_length=12)
    audience: Optional[str] = Field(default=None, max_length=200)
    platform: Optional[str] = Field(default=None, max_length=100)
    language: Optional[str] = Field(default=None, max_length=50)
    premise: Optional[str] = Field(default=None, max_length=4000)
    protagonist: Optional[str] = Field(default=None, max_length=3000)
    protagonist_goal: Optional[str] = Field(default=None, max_length=2000)
    flaw: Optional[str] = Field(default=None, max_length=1500)
    fear: Optional[str] = Field(default=None, max_length=1500)
    core_conflict: Optional[str] = Field(default=None, max_length=3000)
    story_question: Optional[str] = Field(default=None, max_length=1500)
    ability: Optional[str] = Field(default=None, max_length=2000)
    ability_cost: Optional[str] = Field(default=None, max_length=2000)
    antagonist: Optional[str] = Field(default=None, max_length=3000)
    setting: Optional[str] = Field(default=None, max_length=5000)
    world_rules: Optional[list[str]] = Field(default=None, max_length=30)
    themes: Optional[list[str]] = Field(default=None, max_length=20)
    tone: Optional[str] = Field(default=None, max_length=300)
    pacing: Optional[str] = Field(default=None, max_length=300)
    pov: Optional[str] = Field(default=None, max_length=100)
    tense: Optional[str] = Field(default=None, max_length=100)
    length_type: Optional[Literal["short", "medium", "long", "epic", "mega"]] = None
    target_words: Optional[int] = Field(default=None, ge=1_000, le=100_000_000)
    target_chapters: Optional[int] = Field(default=None, ge=1, le=10_000)
    words_per_chapter: Optional[int] = Field(default=None, ge=500, le=20_000)
    volume_count: Optional[int] = Field(default=None, ge=1, le=100)
    ending_preference: Optional[str] = Field(default=None, max_length=1000)
    content_boundaries: Optional[list[str]] = Field(default=None, max_length=30)
    custom_prompt: Optional[str] = Field(default=None, max_length=20_000)
    source_material: Optional[list[str]] = Field(default=None, max_length=30)
    creative_prompt: Optional[str] = Field(default=None, max_length=12_000)

    @field_validator(*_TEXT_FIELDS, mode="before")
    @classmethod
    def normalise_text(cls, value: Any) -> Optional[str]:
        return _coerce_text(value)

    @field_validator(*_LIST_FIELDS, mode="before")
    @classmethod
    def normalise_list(cls, value: Any) -> Optional[list[str]]:
        return _coerce_string_list(value)

    @field_validator(*_INT_FIELDS, mode="before")
    @classmethod
    def normalise_integer(cls, value: Any) -> Optional[int]:
        return _coerce_int(value)

    @field_validator("length_type", mode="before")
    @classmethod
    def normalise_length_type(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip().lower()
        aliases = {
            "短篇": "short",
            "短篇小说": "short",
            "中篇": "medium",
            "中篇小说": "medium",
            "长篇": "long",
            "长篇小说": "long",
            "大长篇": "epic",
            "史诗": "epic",
            "超长篇": "mega",
        }
        return aliases.get(text, text or None)


_KEY_ALIASES = {
    "name": "title",
    "book_title": "title",
    "novel_title": "title",
    "备选书名": "alternate_titles",
    "类型": "genre",
    "题材": "genre",
    "type": "genre",
    "target_audience": "audience",
    "reader_audience": "audience",
    "目标读者": "audience",
    "main_character": "protagonist",
    "hero": "protagonist",
    "主角": "protagonist",
    "goal": "protagonist_goal",
    "protagonist_desire": "protagonist_goal",
    "主角目标": "protagonist_goal",
    "protagonist_flaw": "flaw",
    "protagonist_fear": "fear",
    "conflict": "core_conflict",
    "核心冲突": "core_conflict",
    "world_setting": "setting",
    "background": "setting",
    "世界观": "setting",
    "style": "tone",
    "文风": "tone",
    "point_of_view": "pov",
    "narrative_perspective": "pov",
    "chapter_words": "words_per_chapter",
    "words_each_chapter": "words_per_chapter",
    "chapters": "target_chapters",
    "total_chapters": "target_chapters",
    "word_count": "target_words",
    "total_words": "target_words",
    "volumes": "volume_count",
    "audience_experience": "audience",
    "system_prompt": "custom_prompt",
    "inspiration": "creative_prompt",
}


def _snake_case_key(key: Any) -> str:
    text = str(key).strip().replace("-", "_").replace(" ", "_")
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", text).lower()
    return _KEY_ALIASES.get(text, _KEY_ALIASES.get(str(key).strip(), text))


def _balanced_json_object(text: str) -> Optional[str]:
    """Find the first balanced JSON object while respecting quoted strings."""
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
    return None


def _json_candidates(text: str) -> list[str]:
    stripped = text.strip().lstrip("\ufeff")
    candidates: list[str] = []
    for fenced in re.findall(r"```(?:json|JSON)?\s*([\s\S]*?)```", stripped):
        if fenced.strip():
            candidates.append(fenced.strip())
    candidates.append(stripped)
    balanced = _balanced_json_object(stripped)
    if balanced:
        candidates.append(balanced)
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _load_json_mapping(text: str) -> dict[str, Any]:
    """Parse fenced JSON or JSON surrounded by natural-language commentary."""
    last_error: Optional[Exception] = None
    for candidate in _json_candidates(text):
        variants = [candidate, re.sub(r",\s*([}\]])", r"\1", candidate)]
        for variant in variants:
            try:
                value = json.loads(variant)
            except (json.JSONDecodeError, TypeError) as exc:
                last_error = exc
                continue
            if isinstance(value, dict):
                return value
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]
    raise BlueprintParseError(f"模型未返回可解析的 JSON 对象: {last_error or 'empty response'}")


def _normalise_blueprint_payload(payload: Mapping[str, Any]) -> NovelBlueprint:
    raw_config: Any = payload.get("config")
    if not isinstance(raw_config, Mapping):
        raw_config = payload.get("blueprint")
    if not isinstance(raw_config, Mapping):
        raw_config = payload.get("project")
    if not isinstance(raw_config, Mapping):
        raw_config = payload
    canonical: dict[str, Any] = {}
    for raw_key, value in raw_config.items():
        key = _snake_case_key(raw_key)
        if key in NovelBlueprint.model_fields and value not in (None, "", [], {}):
            canonical[key] = value
    try:
        return NovelBlueprint.model_validate(canonical)
    except Exception as exc:
        raise BlueprintParseError(f"蓝图字段校验失败: {exc}") from exc


@dataclass(frozen=True)
class BlueprintAssessment:
    config: dict[str, Any]
    readiness: int
    missing_fields: list[str]
    suggested_replies: list[str]
    assumptions: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "readiness": self.readiness,
            "missing_fields": self.missing_fields,
            "suggested_replies": self.suggested_replies,
            "assumptions": self.assumptions,
        }


def _has_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return value is not None


def _calculate_readiness(blueprint: NovelBlueprint) -> tuple[int, list[str]]:
    """Calculate readiness from information, never from turn count."""
    checks = [
        ("title", 5, _has_text(blueprint.title)),
        (
            "premise",
            20,
            any(
                _has_text(value)
                for value in (blueprint.premise, blueprint.logline, blueprint.creative_prompt)
            ),
        ),
        ("protagonist", 15, _has_text(blueprint.protagonist)),
        ("protagonist_goal", 10, _has_text(blueprint.protagonist_goal)),
        (
            "core_conflict",
            15,
            any(_has_text(value) for value in (blueprint.core_conflict, blueprint.antagonist)),
        ),
        ("genre", 10, _has_text(blueprint.genre)),
        ("setting", 10, _has_text(blueprint.setting)),
        ("tone", 3, _has_text(blueprint.tone)),
        (
            "audience",
            2,
            any(_has_text(value) for value in (blueprint.audience, blueprint.platform)),
        ),
        (
            "length_type",
            5,
            any(
                value is not None
                for value in (
                    blueprint.length_type,
                    blueprint.target_words,
                    blueprint.target_chapters,
                )
            ),
        ),
        ("pov", 5, any(_has_text(value) for value in (blueprint.pov, blueprint.tense))),
    ]
    readiness = sum(weight for _, weight, complete in checks if complete)
    missing = [field for field, _, complete in checks if not complete]
    return readiness, missing


_SUGGESTION_BY_FIELD = {
    "title": "先使用一个可随时修改的暂定书名",
    "premise": "用一句话说清故事最独特的设定和将要发生的事",
    "protagonist": "补充主角的身份、性格，以及他与普通人的关键差异",
    "protagonist_goal": "说明主角最想得到什么，以及失败会失去什么",
    "core_conflict": "描述阻止主角的核心力量或不可回避的矛盾",
    "genre": "确定主类型，并补充一两个混合题材",
    "setting": "说明故事发生的时代、地点和最重要的世界规则",
    "tone": "选择希望读者感受到的整体气质和文风",
    "audience": "说明目标读者或计划发布的平台",
    "length_type": "选择短篇、中篇或长篇，并给出大致字数/章数",
    "pov": "选择第一/第三人称，以及过去时或现在时",
}


def _assessment_from_payload(payload: Mapping[str, Any]) -> BlueprintAssessment:
    blueprint = _normalise_blueprint_payload(payload)
    data = blueprint.model_dump(exclude_none=True)

    # Models occasionally express the same fact under a neighbouring field and
    # omit the canonical editor field.  Reuse only information already present;
    # never invent story facts merely to increase the readiness score.
    if not _has_text(data.get("logline")):
        candidate = data.get("premise") or data.get("creative_prompt")
        if _has_text(candidate):
            data["logline"] = str(candidate)[:1000]
    if not _has_text(data.get("premise")) and _has_text(data.get("logline")):
        data["premise"] = str(data["logline"])[:4000]
    if not _has_text(data.get("core_conflict")):
        candidate = data.get("story_question") or data.get("antagonist") or data.get("ability_cost")
        if _has_text(candidate):
            data["core_conflict"] = str(candidate)[:3000]
    if not _has_text(data.get("pacing")):
        pacing_source = " ".join(
            str(value)
            for value in (
                data.get("genre"),
                data.get("subgenres"),
                data.get("tone"),
                data.get("creative_prompt"),
            )
            if _has_text(value)
        )
        pacing_phrases = (
            ("慢热", "慢热推进，重视长期积累与阶段性变化"),
            ("快节奏", "快节奏推进，保持高频冲突与回报"),
            ("紧凑", "节奏紧凑，减少无效停顿"),
            ("舒缓", "舒缓推进，给人物关系与日常细节留出空间"),
            ("张弛", "张弛有度，在高压事件之间安排必要缓冲"),
        )
        for marker, description in pacing_phrases:
            if marker in pacing_source:
                data["pacing"] = description
                break

    blueprint = NovelBlueprint.model_validate(data)
    config = blueprint.model_dump(exclude_none=True)
    readiness, missing = _calculate_readiness(blueprint)
    suggested = _coerce_string_list(payload.get("suggested_replies")) or []
    assumptions = _coerce_string_list(payload.get("assumptions")) or []
    if not suggested:
        suggested = [_SUGGESTION_BY_FIELD[field] for field in missing[:3]]
    return BlueprintAssessment(
        config=config,
        readiness=readiness,
        missing_fields=missing,
        suggested_replies=suggested[:4],
        assumptions=assumptions[:20],
    )


def _parse_blueprint_response(text: str) -> BlueprintAssessment:
    return _assessment_from_payload(_load_json_mapping(text))


def _merge_with_current_blueprint(
    current_blueprint: Mapping[str, Any],
    assessment: BlueprintAssessment,
) -> BlueprintAssessment:
    """Return a complete snapshot while preserving prior/manual editor values."""
    current = _normalise_blueprint_payload({"config": current_blueprint})
    merged = current.model_dump(exclude_none=True)
    merged.update(assessment.config)
    return _assessment_from_payload(
        {
            "config": merged,
            "suggested_replies": assessment.suggested_replies,
            "assumptions": assessment.assumptions,
        }
    )


def _model_looks_reasoning(
    model: str,
    capabilities: Optional[Mapping[str, Any]] = None,
) -> bool:
    if capabilities and capabilities.get("is_reasoning"):
        return True
    lowered = model.lower()
    # 当前网关上的 GLM 5.2 会直接返回 content。错误标记为推理模型会
    # 禁用 JSON mode、放大 token 预算并显著拖慢交互；Step 3.7 则会使用推理区。
    markers = ("step-3.7", "reasoner", "reasoning", "thinking", "deepseek-r1")
    return any(marker in lowered for marker in markers)


def _normalise_provider_type(provider_type: str) -> str:
    lowered = (provider_type or "openai_compatible").strip().lower()
    if lowered == "anthropic":
        return "anthropic"
    if lowered in {"openai", "openai_compatible", "custom", "ollama", "azure"}:
        return "openai_compatible"
    raise ChatConfigurationError(f"不支持的 Provider 类型: {provider_type}")


@dataclass(frozen=True)
class ResolvedChatLLM:
    provider: BaseProvider
    model: str
    source: str
    capabilities: dict[str, Any]

    @property
    def is_reasoning(self) -> bool:
        return _model_looks_reasoning(self.model, self.capabilities)


def _create_provider(
    provider_type: str,
    base_url: str,
    api_key: str,
    model: str,
) -> BaseProvider:
    normalised = _normalise_provider_type(provider_type)
    if normalised == "anthropic":
        return AnthropicProvider(
            base_url=base_url or "https://api.anthropic.com",
            api_key=api_key,
            default_model=model,
        )
    return OpenAICompatibleProvider(
        base_url=base_url or "https://api.openai.com/v1",
        api_key=api_key,
        default_model=model,
    )


def _binding_priority(binding: ModelBinding) -> tuple[int, int, int, int]:
    """Default first; prefer GLM for interactive chat when no default exists."""
    model = (binding.model_name or "").lower()
    created_at = getattr(binding, "created_at", None)
    created_rank = int(created_at.timestamp()) if created_at is not None else 0
    return (
        int(bool(binding.is_default)),
        int("glm-5.2" in model),
        int(binding.agent_role == CHAT_AGENT_ROLE),
        created_rank,
    )


def _provider_priority(provider: LlmProvider) -> tuple[int, int, int, int]:
    config = provider.config if isinstance(provider.config, dict) else {}
    model = (provider.default_model or "").lower()
    created_at = getattr(provider, "created_at", None)
    created_rank = int(created_at.timestamp()) if created_at is not None else 0
    return (
        int(bool(config.get("is_default"))),
        int("glm-5.2" in model),
        int(bool(provider.default_model)),
        created_rank,
    )


def _resolved_from_database_row(
    provider_row: LlmProvider,
    model: str,
    source: str,
    capabilities: Optional[Mapping[str, Any]] = None,
) -> Optional[ResolvedChatLLM]:
    base_url = (provider_row.base_url or "").strip()
    api_key = (provider_row.api_key_enc or "").strip()
    model = (model or "").strip()
    if not api_key or not model:
        return None
    try:
        provider = _create_provider(provider_row.provider_type, base_url, api_key, model)
    except ChatConfigurationError:
        logger.warning("跳过不支持的聊天 Provider: %s", provider_row.provider_type)
        return None
    return ResolvedChatLLM(
        provider=provider,
        model=model,
        source=source,
        capabilities=dict(capabilities or {}),
    )


async def _resolve_from_database() -> Optional[ResolvedChatLLM]:
    """Resolve only global bindings; project-scoped bindings are never borrowed."""
    async with async_session_factory() as db:
        binding_stmt = (
            select(ModelBinding, LlmProvider)
            .join(LlmProvider, ModelBinding.provider_id == LlmProvider.id)
            .where(
                ModelBinding.project_id.is_(None),
                (ModelBinding.agent_role.is_(None) | (ModelBinding.agent_role == CHAT_AGENT_ROLE)),
                LlmProvider.is_active.is_(True),
            )
        )
        binding_rows = list((await db.execute(binding_stmt)).all())
        binding_rows.sort(key=lambda row: _binding_priority(row[0]), reverse=True)
        for binding, provider_row in binding_rows:
            resolved = _resolved_from_database_row(
                provider_row,
                binding.model_name,
                source="database_binding",
                capabilities=binding.capabilities,
            )
            if resolved:
                return resolved

        provider_stmt = select(LlmProvider).where(LlmProvider.is_active.is_(True))
        providers = list((await db.execute(provider_stmt)).scalars().all())
        providers.sort(key=_provider_priority, reverse=True)
        for provider_row in providers:
            config = provider_row.config if isinstance(provider_row.config, dict) else {}
            resolved = _resolved_from_database_row(
                provider_row,
                provider_row.default_model or "",
                source="database_provider",
                capabilities=config.get("capabilities"),
            )
            if resolved:
                return resolved
    return None


def _resolve_from_environment() -> Optional[ResolvedChatLLM]:
    provider_type = settings.DEFAULT_PROVIDER or "openai_compatible"
    normalised = _normalise_provider_type(provider_type)
    if normalised == "anthropic":
        base_url = settings.ANTHROPIC_BASE_URL or settings.DEFAULT_BASE_URL
        api_key = settings.ANTHROPIC_API_KEY or settings.DEFAULT_API_KEY
        model = settings.ANTHROPIC_MODEL or settings.DEFAULT_MODEL
    else:
        base_url = settings.OPENAI_COMPATIBLE_BASE_URL or settings.DEFAULT_BASE_URL
        api_key = settings.OPENAI_COMPATIBLE_API_KEY or settings.DEFAULT_API_KEY
        model = settings.OPENAI_COMPATIBLE_MODEL or settings.DEFAULT_MODEL
    if not api_key.strip() or not model.strip():
        return None
    return ResolvedChatLLM(
        provider=_create_provider(provider_type, base_url, api_key, model),
        model=model,
        source="environment",
        capabilities={},
    )


async def _resolve_chat_llm() -> ResolvedChatLLM:
    try:
        database_config = await _resolve_from_database()
    except SQLAlchemyError as exc:
        logger.warning("读取聊天 Provider 配置失败，尝试环境变量: %s", exc)
        database_config = None
    if database_config:
        return database_config
    environment_config = _resolve_from_environment()
    if environment_config:
        return environment_config
    raise ChatConfigurationError(
        "未配置可用的全局 LLM：请启用 Provider，并设置默认模型/全局绑定，"
        "或配置 OPENAI_COMPATIBLE_* / ANTHROPIC_* 环境变量"
    )


SYSTEM_PROMPT = """你是资深小说项目设计师，正在与作者共同把一个模糊灵感发展成可执行的小说蓝图。

工作方式：
- 先简短复述并推进用户刚提供的想法，再只问一个当前信息价值最高的问题。
- 每轮只能问一个问题，不要列出问卷，不要一次索取多个字段。
- 优先发现故事的独特卖点、主角欲望与代价、核心冲突、世界规则和目标读者。
- 已明确的信息不要重复追问；允许作者保留未知项，并清楚标注合理假设。
- 回复要自然、具体、简洁，不输出 JSON，不声称已创建项目。
- 使用纯文本，不使用 Markdown 标记；整次回复控制在 220 个汉字以内。
- 当信息已经充分时，给出一句凝练总结，并只问一个可选的最终校准问题。"""


EXTRACT_PROMPT = """你是小说项目蓝图编辑器。
根据完整对话提取当前已知信息，返回一个 JSON 对象，不要输出解释。

顶层格式必须是：
{
  "config": { ... },
  "suggested_replies": ["最多4条，帮助作者回答下一关键缺口"],
  "assumptions": ["只列出对话未明说但你确实采用的假设"]
}

config 只允许这些字段：
title, alternate_titles, logline, genre, subgenres, audience, platform, language,
premise, protagonist, protagonist_goal, flaw, fear, core_conflict, story_question,
ability, ability_cost, antagonist, setting, world_rules, themes, tone, pacing, pov,
tense, length_type, target_words, target_chapters, words_per_chapter, volume_count,
ending_preference, content_boundaries, custom_prompt, source_material, creative_prompt。

规则：
- 不知道就省略字段，绝不能用空洞套话伪造完整度。
- 如果作者没有明确书名但故事概念已经足够，请给出一个可编辑的暂定 title，
  并在 assumptions 中明确标注“书名为 AI 暂定”。
- length_type 只能是 short/medium/long/epic/mega。
- 数字字段必须是整数。
- custom_prompt 是可直接注入写作 Agent 的完整项目专属创作指令。
- creative_prompt 保留作者最初灵感及不可丢失的约束。
- 对话中的最新明确决定覆盖较早决定。
- 必须输出合法 JSON；可以省略尚未确定的字段。"""


def _request_messages(messages: Sequence[ChatMessage]) -> list[LLMMessage]:
    result = [LLMMessage(role="system", content=SYSTEM_PROMPT)]
    result.extend(LLMMessage(role=message.role, content=message.content) for message in messages)
    return result


def _conversation_text(messages: Sequence[ChatMessage]) -> str:
    labels = {"user": "作者", "assistant": "顾问"}
    return "\n".join(f"{labels[message.role]}：{message.content}" for message in messages)


def _extract_marked_final(text: str) -> Optional[str]:
    patterns = (
        r"===FINAL_ANSWER===\s*([\s\S]+)$",
        r"(?:final answer|最终回答|最终答案)\s*[:：]\s*([\s\S]+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return None


def _visible_response(response: LLMResponse) -> str:
    """Return a confirmed visible answer, never raw hidden reasoning."""
    if response.content and response.content.strip():
        return response.content.strip()
    reasoning = (response.reasoning_content or "").strip()
    if reasoning and response.finish_reason != "length":
        marked = _extract_marked_final(reasoning)
        if marked:
            return marked
    if reasoning:
        suffix = "（输出长度耗尽）" if response.finish_reason == "length" else ""
        raise RuntimeError(f"模型只返回了推理过程，未生成可展示的最终回答{suffix}")
    raise RuntimeError("模型返回空内容，未生成可展示的最终回答")


async def _extract_blueprint(
    resolved: ResolvedChatLLM,
    messages: Sequence[ChatMessage],
    current_blueprint: Optional[Mapping[str, Any]] = None,
) -> BlueprintAssessment:
    current_context = ""
    if current_blueprint:
        current_context = (
            "\n\n作者当前在右侧简报中保留的版本（手动编辑优先，除非后续对话明确修改）：\n"
            + json.dumps(dict(current_blueprint), ensure_ascii=False)
        )
    request = LLMRequest(
        model=resolved.model,
        messages=[
            LLMMessage(role="system", content=EXTRACT_PROMPT),
            LLMMessage(
                role="user",
                content=(
                    f"完整创作对话：\n{_conversation_text(messages)}"
                    f"{current_context}\n\n请输出当前项目蓝图。"
                ),
            ),
        ],
        temperature=0.2,
        max_tokens=REASONING_CHAT_TOKENS if resolved.is_reasoning else DEFAULT_CHAT_TOKENS,
        response_format=None if resolved.is_reasoning else {"type": "json_object"},
        is_reasoning_model=resolved.is_reasoning,
    )
    response = await resolved.provider.complete(request)
    candidates = [response.content]
    if response.finish_reason != "length":
        candidates.append(response.reasoning_content)
    errors: list[str] = []
    for candidate in candidates:
        if not candidate or not candidate.strip():
            continue
        try:
            assessment = _parse_blueprint_response(candidate)
            if current_blueprint:
                assessment = _merge_with_current_blueprint(
                    current_blueprint,
                    assessment,
                )
            return assessment
        except BlueprintParseError as exc:
            errors.append(str(exc))
    if response.reasoning_content and response.finish_reason == "length":
        raise BlueprintParseError("蓝图生成在推理阶段耗尽输出长度，未得到完整 JSON")
    raise BlueprintParseError("; ".join(errors) or "模型未返回蓝图 JSON")


def _sse(event: str, payload: Mapping[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/chat-create/status", response_model=ChatCreateStatus)
async def chat_create_status() -> ChatCreateStatus:
    try:
        resolved = await _resolve_chat_llm()
    except ChatConfigurationError:
        return ChatCreateStatus(configured=False)
    return ChatCreateStatus(configured=True, model=resolved.model, source=resolved.source)


@router.post("/chat-create", response_model=ChatCreateResponse)
async def chat_create(request: ChatCreateRequest) -> ChatCreateResponse:
    """Backward-compatible non-streaming chat/extraction endpoint."""
    try:
        resolved = await _resolve_chat_llm()
    except ChatConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        if request.extract:
            assessment = await _extract_blueprint(
                resolved,
                request.messages,
                request.blueprint,
            )
            return ChatCreateResponse(
                reply="配置已生成",
                config=assessment.config,
                readiness=assessment.readiness,
                missing_fields=assessment.missing_fields,
                suggested_replies=assessment.suggested_replies,
                assumptions=assessment.assumptions,
            )
        llm_request = LLMRequest(
            model=resolved.model,
            messages=_request_messages(request.messages),
            temperature=0.7,
            max_tokens=REASONING_CHAT_TOKENS if resolved.is_reasoning else DEFAULT_CHAT_TOKENS,
            is_reasoning_model=resolved.is_reasoning,
        )
        response = await resolved.provider.complete(llm_request)
        return ChatCreateResponse(reply=_visible_response(response))
    except (BlueprintParseError, RuntimeError) as exc:
        logger.warning("对话式创建失败: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _chat_event_stream(request: ChatCreateRequest) -> AsyncIterator[str]:
    try:
        resolved = await _resolve_chat_llm()
        llm_request = LLMRequest(
            model=resolved.model,
            messages=_request_messages(request.messages),
            temperature=0.7,
            max_tokens=REASONING_CHAT_TOKENS if resolved.is_reasoning else DEFAULT_CHAT_TOKENS,
            stream=True,
            is_reasoning_model=resolved.is_reasoning,
        )
        reply_parts: list[str] = []
        async for delta in resolved.provider.stream_complete(llm_request):
            if not delta:
                continue
            reply_parts.append(delta)
            yield _sse("delta", {"delta": delta})
        reply = "".join(reply_parts).strip()
        if not reply:
            raise RuntimeError("模型流结束但没有生成可展示内容")
        completed_messages = [
            *request.messages,
            ChatMessage(role="assistant", content=reply),
        ]
        assessment = await _extract_blueprint(
            resolved,
            completed_messages,
            request.blueprint,
        )
        yield _sse("blueprint", assessment.as_dict())
        yield _sse("done", {"ok": True})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("对话式创建流失败")
        yield _sse(
            "error",
            {"message": str(exc), "error": str(exc), "code": "chat_stream_failed"},
        )
        yield _sse("done", {"ok": False})


@router.post("/chat-create/stream")
async def chat_create_stream(request: ChatCreateRequest) -> StreamingResponse:
    return StreamingResponse(
        _chat_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
