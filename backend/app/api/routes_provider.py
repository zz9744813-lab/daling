"""Provider 路由 - LLM 提供方与模型绑定管理。"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.models.project import Project
from app.db.models.provider import LlmProvider, ModelBinding
from app.model_gateway import LLMMessage, LLMRequest, gateway

router = APIRouter(prefix="/api", tags=["provider"])


class ProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    provider_type: str = "openai_compatible"
    type: Optional[str] = None  # 前端 Provider.type（与 provider_type 同义）
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    models: list[str] = Field(default_factory=list)
    default_model: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)


class ProviderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    provider_type: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    default_model: Optional[str] = None
    models: Optional[list[str]] = None
    is_active: Optional[bool] = None


class ProviderOut(BaseModel):
    """对齐前端 Provider 类型。"""

    id: str
    name: str
    type: Optional[str] = None  # = provider_type
    provider_type: Optional[str] = None  # 兼容旧字段
    base_url: Optional[str] = None
    status: Optional[str] = None  # active / untested / inactive / error
    is_active: Optional[bool] = None  # 兼容旧字段
    default_model: Optional[str] = None
    models: list[str] = Field(default_factory=list)
    last_health_check_at: Optional[str] = None
    latency_ms: Optional[int] = None
    tested_model: Optional[str] = None
    last_error: Optional[str] = None
    has_saved_api_key: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = {"from_attributes": True}


class ModelBindingCreate(BaseModel):
    provider_id: str
    model_name: Optional[str] = None  # 前端发 "model"，映射到 model_name
    model: Optional[str] = None  # 兼容前端发的 "model" 字段
    agent_role: Optional[str] = None  # 新增：StoryArchitect/Drafter/Critic 等
    project_id: Optional[str] = None  # 新增：null=全局绑定，非null=项目级绑定
    display_name: Optional[str] = None
    context_window: int = Field(default=8192, ge=1024)
    max_output_tokens: int = Field(default=4096, ge=256)
    cost_per_1k_input: float = Field(default=0.0, ge=0)
    cost_per_1k_output: float = Field(default=0.0, ge=0)
    is_default: bool = False
    capabilities: dict[str, Any] = Field(default_factory=dict)

    model_config = {"protected_namespaces": ()}


class ModelBindingUpdate(BaseModel):
    provider_id: Optional[str] = None
    model_name: Optional[str] = None
    model: Optional[str] = None
    agent_role: Optional[str] = None
    project_id: Optional[str] = None
    display_name: Optional[str] = None
    context_window: Optional[int] = Field(default=None, ge=1024)
    max_output_tokens: Optional[int] = Field(default=None, ge=256)
    cost_per_1k_input: Optional[float] = Field(default=None, ge=0)
    cost_per_1k_output: Optional[float] = Field(default=None, ge=0)
    is_default: Optional[bool] = None
    capabilities: Optional[dict[str, Any]] = None

    model_config = {"protected_namespaces": ()}


class ModelBindingOut(BaseModel):
    id: str
    provider_id: str
    model_name: str
    model: Optional[str] = None  # = model_name，前端用
    agent_role: Optional[str] = None  # 新增
    project_id: Optional[str] = None  # 新增
    provider_name: Optional[str] = None  # 关联查询填充
    display_name: Optional[str] = None
    is_default: bool = False
    context_window: int = 8192
    max_output_tokens: int = 4096
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    capabilities: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class ProviderDeleteOut(BaseModel):
    ok: bool
    provider_id: str
    name: str
    force: bool
    deleted_bindings: int


class ModelBindingDeleteOut(BaseModel):
    ok: bool
    binding_id: str
    provider_id: str
    model_name: str
    agent_role: Optional[str] = None
    project_id: Optional[str] = None
    was_default: bool = False

    model_config = {"protected_namespaces": ()}


class TestRequest(BaseModel):
    """Provider 连通性测试请求体。

    支持两种模式：
    1. 直接传 ``{ provider_type, base_url, api_key, model }``（前端 ProviderTestParams）
    2. 传 ``provider_id`` 从数据库加载配置（兼容旧调用）
    """

    provider_type: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    provider_id: Optional[str] = None
    prompt: str = "ping"


def _parse_uuid(value: Any, field_name: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_uuid",
                "field": field_name,
                "message": f"{field_name} must be a valid UUID",
            },
        ) from exc


async def _ensure_provider_name_available(
    db: AsyncSession,
    name: str,
    *,
    exclude_id: Optional[uuid.UUID] = None,
) -> None:
    stmt = select(LlmProvider.id).where(func.lower(LlmProvider.name) == name.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(LlmProvider.id != exclude_id)
    conflict_id = await db.scalar(stmt.limit(1))
    if conflict_id is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "provider_name_conflict",
                "provider_id": str(conflict_id),
                "message": "A provider with this name already exists",
            },
        )


async def _resolve_project_id(
    db: AsyncSession,
    value: Optional[str],
) -> Optional[uuid.UUID]:
    if not value:
        return None
    project_id = _parse_uuid(value, "project_id")
    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_id


async def _ensure_binding_scope_available(
    db: AsyncSession,
    *,
    provider_id: uuid.UUID,
    model_name: str,
    agent_role: Optional[str],
    project_id: Optional[uuid.UUID],
    exclude_id: Optional[uuid.UUID] = None,
) -> None:
    scope_condition = (
        ModelBinding.project_id.is_(None)
        if project_id is None
        else ModelBinding.project_id == project_id
    )
    stmt = select(ModelBinding.id).where(scope_condition)
    if agent_role:
        # Exactly one effective binding may own a role in a given scope.
        stmt = stmt.where(ModelBinding.agent_role == agent_role)
    else:
        stmt = stmt.where(
            ModelBinding.agent_role.is_(None),
            ModelBinding.provider_id == provider_id,
            ModelBinding.model_name == model_name,
        )
    if exclude_id is not None:
        stmt = stmt.where(ModelBinding.id != exclude_id)
    conflict_id = await db.scalar(stmt.limit(1))
    if conflict_id is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "model_binding_conflict",
                "binding_id": str(conflict_id),
                "agent_role": agent_role,
                "project_id": str(project_id) if project_id else None,
                "message": "This model-binding scope is already configured",
            },
        )


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------
def _provider_health(provider: LlmProvider) -> dict[str, Any]:
    config = provider.config if isinstance(provider.config, dict) else {}
    health = config.get("health")
    return health if isinstance(health, dict) else {}


def _provider_status(provider: LlmProvider) -> str:
    """配置存在不等于在线；只有最近一次真实调用成功才标记 active。"""
    if not provider.is_active:
        return "inactive"
    health = _provider_health(provider)
    if not health.get("checked_at"):
        return "untested"
    return "active" if health.get("ok") is True else "error"


def _resolved_saved_provider_config(
    provider: LlmProvider,
    *,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve a saved provider without duplicating an env-managed secret.

    A database provider may intentionally omit its key when its type and URL
    match the process default. In that exact case the read-only ``.env`` key is
    reused. A provider pointing at another URL never inherits that credential.
    """
    default = gateway.get_default_config()
    provider_type = provider.provider_type or "openai_compatible"
    base_url = (provider.base_url or default.get("base_url") or "").rstrip("/")
    default_url = str(default.get("base_url") or "").rstrip("/")
    api_key = provider.api_key_enc or ""
    if (
        not api_key
        and provider_type == default.get("provider_type")
        and base_url
        and base_url == default_url
    ):
        api_key = str(default.get("api_key") or "")
    return {
        "provider_type": provider_type,
        "base_url": base_url,
        "api_key": api_key,
        "model": model or provider.default_model or str(default.get("model") or ""),
    }


def serialize_provider(p: LlmProvider) -> ProviderOut:
    """将 LlmProvider ORM 对象序列化为前端 Provider 结构。"""
    models: list[str] = []
    config = p.config or {}
    if isinstance(config, dict):
        cfg_models = config.get("models")
        if isinstance(cfg_models, list):
            models = [str(m) for m in cfg_models]
    health = _provider_health(p)
    return ProviderOut(
        id=str(p.id),
        name=p.name,
        type=p.provider_type,
        provider_type=p.provider_type,
        base_url=p.base_url,
        status=_provider_status(p),
        is_active=p.is_active,
        default_model=p.default_model,
        models=models,
        last_health_check_at=health.get("checked_at"),
        latency_ms=health.get("latency_ms"),
        tested_model=health.get("model"),
        last_error=health.get("error"),
        has_saved_api_key=bool(p.api_key_enc),
        created_at=p.created_at.isoformat() if p.created_at else None,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    )


# ---------------- Providers ----------------


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(db: AsyncSession = Depends(get_db)):
    """查询所有 LLM Provider。"""
    stmt = select(LlmProvider).order_by(LlmProvider.created_at.desc())
    result = await db.execute(stmt)
    providers = result.scalars().all()
    return [serialize_provider(p) for p in providers]


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(payload: ProviderCreate, db: AsyncSession = Depends(get_db)):
    await _ensure_provider_name_available(db, payload.name)
    """创建 LLM Provider（api_key 明文存储，Phase 0 不加密）。"""
    # type 与 provider_type 同义，优先用 provider_type
    provider_type = payload.provider_type or payload.type or "openai_compatible"
    # models 存入 config
    config = dict(payload.config)
    if payload.models:
        config["models"] = payload.models

    provider = LlmProvider(
        name=payload.name.strip(),
        provider_type=provider_type,
        base_url=payload.base_url,
        api_key_enc=payload.api_key,  # Phase 0 明文存储
        is_active=True,
        default_model=payload.default_model or payload.model,
        config=config,
    )
    db.add(provider)
    await db.flush()
    await db.refresh(provider)
    return serialize_provider(provider)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: uuid.UUID,
    payload: ProviderUpdate,
    db: AsyncSession = Depends(get_db),
):
    provider = await db.get(LlmProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    if payload.name is not None:
        await _ensure_provider_name_available(db, payload.name, exclude_id=provider.id)
    configuration_changed = any(
        value is not None
        for value in (
            payload.provider_type,
            payload.base_url,
            payload.api_key,
            payload.default_model,
            payload.models,
        )
    )
    for field_name in ("name", "provider_type", "base_url", "default_model", "is_active"):
        value = getattr(payload, field_name)
        if value is not None:
            if field_name == "name":
                value = value.strip()
            setattr(provider, field_name, value)
    if payload.api_key is not None:
        provider.api_key_enc = payload.api_key or None
    if payload.models is not None:
        provider.config = {**(provider.config or {}), "models": payload.models}
    if configuration_changed:
        config = dict(provider.config or {})
        config.pop("health", None)
        provider.config = config
    await db.flush()
    await db.refresh(provider)
    return serialize_provider(provider)


@router.delete("/providers/{provider_id}", response_model=ProviderDeleteOut)
async def delete_provider(
    provider_id: uuid.UUID,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
):
    provider = await db.get(LlmProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    bindings = list(
        (
            await db.scalars(
                select(ModelBinding).where(ModelBinding.provider_id == provider_id)
            )
        ).all()
    )
    if bindings and not force:
        raise HTTPException(
            status_code=409,
            detail=f"Provider 仍被 {len(bindings)} 个模型绑定使用；确认后使用 force=true",
        )
    # Keep cascade semantics deterministic even when a deployment has not
    # enabled database-side foreign-key cascades correctly.
    for binding in bindings:
        await db.delete(binding)
    if bindings:
        await db.flush()
    provider_name = provider.name
    await db.delete(provider)
    await db.flush()
    return ProviderDeleteOut(
        ok=True,
        provider_id=str(provider_id),
        name=provider_name,
        force=force,
        deleted_bindings=len(bindings),
    )


@router.post("/providers/test")
async def test_provider(payload: TestRequest, db: AsyncSession = Depends(get_db)):
    """真实调用 Provider 验证连通性。

    支持两种入参：
    - ``{ provider_type, base_url, api_key, model }`` 直接测试（前端 ProviderTestParams）
    - ``{ provider_id }`` 从数据库加载配置
    """
    provider_config: dict[str, Any] = {}
    saved_provider: Optional[LlmProvider] = None

    if payload.provider_id:
        # 从数据库加载 Provider 配置
        saved_provider = await db.get(
            LlmProvider,
            _parse_uuid(payload.provider_id, "provider_id"),
        )
        if not saved_provider:
            raise HTTPException(status_code=404, detail="Provider 不存在")
        provider_config = _resolved_saved_provider_config(saved_provider, model=payload.model)
    elif payload.provider_type:
        # 前端 ProviderTestParams 模式
        provider_config = {
            "provider_type": payload.provider_type,
            "base_url": payload.base_url or "",
            "api_key": payload.api_key or "",
            "model": payload.model or "",
        }
    else:
        # 使用默认配置
        provider_config = gateway.get_default_config()
        if payload.model:
            provider_config["model"] = payload.model

    # 构造测试请求
    request = LLMRequest(
        messages=[LLMMessage(role="user", content=payload.prompt)],
        model=provider_config.get("model", ""),
        temperature=0.0,
        max_tokens=100,
    )

    checked_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    try:
        response = await gateway.complete(request, provider_config)
        latency_ms = int((time.monotonic() - started) * 1000)
        if saved_provider is not None:
            saved_provider.config = {
                **(saved_provider.config or {}),
                "health": {
                    "ok": True,
                    "checked_at": checked_at,
                    "latency_ms": latency_ms,
                    "model": response.model or provider_config.get("model", ""),
                    "error": None,
                },
            }
            await db.commit()
        return {
            "ok": True,
            "message": "Provider 连通性测试通过",
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "latency_ms": latency_ms,
            "last_health_check_at": checked_at,
            "response_preview": response.content[:200],
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        if saved_provider is not None:
            saved_provider.config = {
                **(saved_provider.config or {}),
                "health": {
                    "ok": False,
                    "checked_at": checked_at,
                    "latency_ms": latency_ms,
                    "model": provider_config.get("model", ""),
                    "error": str(exc)[:1000],
                },
            }
            await db.commit()
        return {
            "ok": False,
            "message": f"Provider 测试失败: {exc}",
            "error": str(exc),
            "latency_ms": latency_ms,
            "last_health_check_at": checked_at,
        }


# ---------------- Model Bindings ----------------


def _serialize_model_binding(
    b: ModelBinding,
    provider_name: Optional[str] = None,
) -> ModelBindingOut:
    """将 ModelBinding ORM 对象序列化为前端结构。

    Args:
        b: ModelBinding 实例。
        provider_name: 关联的 LlmProvider 名称（需通过关联查询填充）。
    """
    return ModelBindingOut(
        id=str(b.id),
        provider_id=str(b.provider_id),
        model_name=b.model_name,
        model=b.model_name,  # 前端用 model 字段
        agent_role=b.agent_role,
        project_id=str(b.project_id) if b.project_id else None,
        provider_name=provider_name,
        display_name=b.display_name,
        is_default=b.is_default,
        context_window=b.context_window,
        max_output_tokens=b.max_output_tokens,
        cost_per_1k_input=b.cost_per_1k_input,
        cost_per_1k_output=b.cost_per_1k_output,
        capabilities=dict(b.capabilities or {}),
        created_at=b.created_at.isoformat() if b.created_at else None,
        updated_at=b.updated_at.isoformat() if b.updated_at else None,
    )


@router.get("/model-bindings", response_model=list[ModelBindingOut])
async def list_model_bindings(
    provider_id: Optional[str] = None,
    project_id: Optional[str] = None,
    agent_role: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """查询模型绑定列表。

    Args:
        provider_id: 可选，按 Provider 过滤。
        project_id: 可选，按项目过滤。传入后会返回该项目的绑定 + 全局绑定
            （project_id IS NULL）。
        agent_role: 可选，按 Agent 角色过滤。
    """
    # 关联查询 LlmProvider 以填充 provider_name
    stmt = (
        select(ModelBinding, LlmProvider.name)
        .outerjoin(LlmProvider, ModelBinding.provider_id == LlmProvider.id)
        .order_by(ModelBinding.created_at.desc())
    )
    if provider_id:
        stmt = stmt.where(
            ModelBinding.provider_id == _parse_uuid(provider_id, "provider_id")
        )
    if agent_role:
        stmt = stmt.where(ModelBinding.agent_role == agent_role)
    if project_id:
        # 返回该项目的绑定 + 全局绑定（project_id IS NULL）
        pid = _parse_uuid(project_id, "project_id")
        stmt = stmt.where((ModelBinding.project_id == pid) | (ModelBinding.project_id.is_(None)))
    result = await db.execute(stmt)
    rows = result.all()
    return [_serialize_model_binding(b, provider_name=pname) for b, pname in rows]


@router.post("/model-bindings", response_model=ModelBindingOut, status_code=201)
async def create_model_binding(payload: ModelBindingCreate, db: AsyncSession = Depends(get_db)):
    """创建模型绑定。

    兼容前端发送 ``model`` 字段（映射到 ``model_name``）。
    保存 ``agent_role`` 与 ``project_id``（null=全局绑定）。
    """
    # 验证 Provider 存在
    provider_id = _parse_uuid(payload.provider_id, "provider_id")
    provider = await db.get(LlmProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider 不存在")

    # 兼容前端发送的 "model" 字段，优先使用 model_name，否则使用 model
    model_name = payload.model_name or payload.model
    if not model_name or not model_name.strip():
        raise HTTPException(status_code=422, detail="model_name 或 model 必填")
    model_name = model_name.strip()

    # 如果设为默认，取消其它默认绑定
    # 解析 project_id（前端传字符串，转为 UUID 或 None）
    project_id_value = await _resolve_project_id(db, payload.project_id)
    agent_role = payload.agent_role.strip() if payload.agent_role else None
    await _ensure_binding_scope_available(
        db,
        provider_id=provider_id,
        model_name=model_name,
        agent_role=agent_role,
        project_id=project_id_value,
    )
    if payload.is_default:
        existing_defaults = list(
            (
                await db.scalars(
                    select(ModelBinding).where(
                        ModelBinding.provider_id == provider_id,
                        ModelBinding.is_default.is_(True),
                    )
                )
            ).all()
        )
        for existing in existing_defaults:
            existing.is_default = False

    binding = ModelBinding(
        provider_id=provider_id,
        model_name=model_name,
        display_name=payload.display_name,
        context_window=payload.context_window,
        max_output_tokens=payload.max_output_tokens,
        cost_per_1k_input=payload.cost_per_1k_input,
        cost_per_1k_output=payload.cost_per_1k_output,
        is_default=payload.is_default,
        capabilities=payload.capabilities,
        agent_role=agent_role,
        project_id=project_id_value,
    )
    db.add(binding)
    await db.flush()
    await db.refresh(binding)
    return _serialize_model_binding(binding, provider_name=provider.name)


@router.patch("/model-bindings/{binding_id}", response_model=ModelBindingOut)
async def update_model_binding(
    binding_id: uuid.UUID,
    payload: ModelBindingUpdate,
    db: AsyncSession = Depends(get_db),
):
    binding = await db.get(ModelBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="模型绑定不存在")
    provider_id = binding.provider_id
    if payload.provider_id is not None:
        provider_id = _parse_uuid(payload.provider_id, "provider_id")
    provider = await db.get(LlmProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider 不存在")

    model_update = (
        payload.model_name if payload.model_name is not None else payload.model
    )
    model_name = binding.model_name
    if model_update is not None:
        model_name = model_update.strip()
        if not model_name:
            raise HTTPException(status_code=422, detail="model_name must not be empty")
    agent_role = binding.agent_role
    if payload.agent_role is not None:
        agent_role = payload.agent_role.strip() or None
    project_id = binding.project_id
    if payload.project_id is not None:
        project_id = await _resolve_project_id(db, payload.project_id)

    await _ensure_binding_scope_available(
        db,
        provider_id=provider_id,
        model_name=model_name,
        agent_role=agent_role,
        project_id=project_id,
        exclude_id=binding.id,
    )

    binding.provider_id = provider_id
    binding.model_name = model_name
    binding.agent_role = agent_role
    binding.project_id = project_id
    for field_name in (
        "display_name",
        "context_window",
        "max_output_tokens",
        "cost_per_1k_input",
        "cost_per_1k_output",
        "capabilities",
    ):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(binding, field_name, value)
    desired_default = payload.is_default if payload.is_default is not None else binding.is_default
    if desired_default:
        existing = list(
            (
                await db.scalars(
                    select(ModelBinding).where(
                        ModelBinding.provider_id == provider_id,
                        ModelBinding.is_default.is_(True),
                        ModelBinding.id != binding.id,
                    )
                )
            ).all()
        )
        for item in existing:
            item.is_default = False
    binding.is_default = desired_default
    await db.flush()
    await db.refresh(binding)
    return _serialize_model_binding(binding, provider_name=provider.name)


@router.delete("/model-bindings/{binding_id}", response_model=ModelBindingDeleteOut)
async def delete_model_binding(
    binding_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    binding = await db.get(ModelBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="模型绑定不存在")
    response = ModelBindingDeleteOut(
        ok=True,
        binding_id=str(binding.id),
        provider_id=str(binding.provider_id),
        model_name=binding.model_name,
        agent_role=binding.agent_role,
        project_id=str(binding.project_id) if binding.project_id else None,
        was_default=binding.is_default,
    )
    await db.delete(binding)
    await db.flush()
    return response
