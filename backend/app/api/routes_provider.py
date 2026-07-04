"""Provider 路由 - LLM 提供方与模型绑定管理。"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
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


class ProviderOut(BaseModel):
    """对齐前端 Provider 类型。"""

    id: str
    name: str
    type: Optional[str] = None  # = provider_type
    provider_type: Optional[str] = None  # 兼容旧字段
    base_url: Optional[str] = None
    status: Optional[str] = None  # 'active' | 'inactive' | 'error'
    is_active: Optional[bool] = None  # 兼容旧字段
    default_model: Optional[str] = None
    models: list[str] = Field(default_factory=list)
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}


class ModelBindingCreate(BaseModel):
    provider_id: str
    model_name: Optional[str] = None  # 前端发 "model"，映射到 model_name
    model: Optional[str] = None  # 兼容前端发的 "model" 字段
    agent_role: Optional[str] = None  # 新增：StoryArchitect/Drafter/Critic 等
    project_id: Optional[str] = None  # 新增：null=全局绑定，非null=项目级绑定
    display_name: Optional[str] = None
    context_window: int = 8192
    max_output_tokens: int = 4096
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    is_default: bool = False
    capabilities: dict[str, Any] = Field(default_factory=dict)


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

    model_config = {"from_attributes": True}


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


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------
def _provider_status(is_active: bool) -> str:
    """将 is_active 映射为前端 Provider.status。"""
    return "active" if is_active else "inactive"


def serialize_provider(p: LlmProvider) -> ProviderOut:
    """将 LlmProvider ORM 对象序列化为前端 Provider 结构。"""
    models: list[str] = []
    config = p.config or {}
    if isinstance(config, dict):
        cfg_models = config.get("models")
        if isinstance(cfg_models, list):
            models = [str(m) for m in cfg_models]
    return ProviderOut(
        id=str(p.id),
        name=p.name,
        type=p.provider_type,
        provider_type=p.provider_type,
        base_url=p.base_url,
        status=_provider_status(p.is_active),
        is_active=p.is_active,
        default_model=p.default_model,
        models=models,
        created_at=p.created_at.isoformat() if p.created_at else None,
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
    """创建 LLM Provider（api_key 明文存储，Phase 0 不加密）。"""
    # type 与 provider_type 同义，优先用 provider_type
    provider_type = payload.provider_type or payload.type or "openai_compatible"
    # models 存入 config
    config = dict(payload.config)
    if payload.models:
        config["models"] = payload.models

    provider = LlmProvider(
        name=payload.name,
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


@router.post("/providers/test")
async def test_provider(payload: TestRequest, db: AsyncSession = Depends(get_db)):
    """真实调用 Provider 验证连通性。

    支持两种入参：
    - ``{ provider_type, base_url, api_key, model }`` 直接测试（前端 ProviderTestParams）
    - ``{ provider_id }`` 从数据库加载配置
    """
    provider_config: dict[str, Any] = {}

    if payload.provider_id:
        # 从数据库加载 Provider 配置
        provider = await db.get(LlmProvider, uuid.UUID(payload.provider_id))
        if not provider:
            raise HTTPException(status_code=404, detail="Provider 不存在")
        provider_config = {
            "provider_type": provider.provider_type,
            "base_url": provider.base_url or "",
            "api_key": provider.api_key_enc or "",
            "model": payload.model or provider.default_model or "",
        }
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

    try:
        response = await gateway.complete(request, provider_config)
        return {
            "ok": True,
            "message": "Provider 连通性测试通过",
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "response_preview": response.content[:200],
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Provider 测试失败: {exc}",
            "error": str(exc),
        }


# ---------------- Model Bindings ----------------

def _serialize_model_binding(b: ModelBinding, provider_name: Optional[str] = None) -> ModelBindingOut:
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
        stmt = stmt.where(ModelBinding.provider_id == uuid.UUID(provider_id))
    if agent_role:
        stmt = stmt.where(ModelBinding.agent_role == agent_role)
    if project_id:
        # 返回该项目的绑定 + 全局绑定（project_id IS NULL）
        pid = uuid.UUID(project_id)
        stmt = stmt.where(
            (ModelBinding.project_id == pid) | (ModelBinding.project_id.is_(None))
        )
    result = await db.execute(stmt)
    rows = result.all()
    return [
        _serialize_model_binding(b, provider_name=pname) for b, pname in rows
    ]


@router.post("/model-bindings", response_model=ModelBindingOut, status_code=201)
async def create_model_binding(
    payload: ModelBindingCreate, db: AsyncSession = Depends(get_db)
):
    """创建模型绑定。

    兼容前端发送 ``model`` 字段（映射到 ``model_name``）。
    保存 ``agent_role`` 与 ``project_id``（null=全局绑定）。
    """
    # 验证 Provider 存在
    provider = await db.get(LlmProvider, uuid.UUID(payload.provider_id))
    if not provider:
        raise HTTPException(status_code=404, detail="Provider 不存在")

    # 兼容前端发送的 "model" 字段，优先使用 model_name，否则使用 model
    model_name = payload.model_name or payload.model
    if not model_name:
        raise HTTPException(status_code=422, detail="model_name 或 model 必填")

    # 如果设为默认，取消其它默认绑定
    if payload.is_default:
        stmt = select(ModelBinding).where(
            ModelBinding.provider_id == uuid.UUID(payload.provider_id),
            ModelBinding.is_default == True,  # noqa: E712
        )
        result = await db.execute(stmt)
        for existing in result.scalars().all():
            existing.is_default = False

    # 解析 project_id（前端传字符串，转为 UUID 或 None）
    project_id_value: Optional[uuid.UUID] = None
    if payload.project_id:
        try:
            project_id_value = uuid.UUID(payload.project_id)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=422, detail="project_id 不是有效的 UUID")

    binding = ModelBinding(
        provider_id=uuid.UUID(payload.provider_id),
        model_name=model_name,
        display_name=payload.display_name,
        context_window=payload.context_window,
        max_output_tokens=payload.max_output_tokens,
        cost_per_1k_input=payload.cost_per_1k_input,
        cost_per_1k_output=payload.cost_per_1k_output,
        is_default=payload.is_default,
        capabilities=payload.capabilities,
        agent_role=payload.agent_role,
        project_id=project_id_value,
    )
    db.add(binding)
    await db.flush()
    await db.refresh(binding)
    return _serialize_model_binding(binding, provider_name=provider.name)
