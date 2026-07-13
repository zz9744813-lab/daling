"""Provider and model-binding mutation contracts must be safe and complete."""

from __future__ import annotations

import app.db.models  # noqa: F401
import pytest
import pytest_asyncio
from app.api.routes_provider import (
    ModelBindingCreate,
    ModelBindingUpdate,
    ProviderUpdate,
    create_model_binding,
    delete_model_binding,
    delete_provider,
    list_model_bindings,
    update_model_binding,
    update_provider,
)
from app.api.routes_provider import (
    TestRequest as ProviderTestRequest,
)
from app.api.routes_provider import (
    test_provider as run_provider_test,
)
from app.core.database import Base
from app.db.models.project import Project
from app.db.models.provider import LlmProvider, ModelBinding
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db_factory(tmp_path):
    database = tmp_path / "provider-contract.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _provider(db, name: str, *, default_model: str = "writer") -> LlmProvider:
    provider = LlmProvider(
        name=name,
        provider_type="openai_compatible",
        base_url="https://old.invalid/v1",
        api_key_enc="saved-secret",
        default_model=default_model,
        config={
            "models": [default_model],
            "health": {
                "ok": True,
                "checked_at": "2026-07-12T00:00:00+00:00",
                "latency_ms": 10,
                "model": default_model,
                "error": None,
            },
        },
    )
    db.add(provider)
    await db.flush()
    return provider


@pytest.mark.asyncio
async def test_provider_patch_returns_complete_safe_fields_and_conflict_is_side_effect_free(
    db_factory,
):
    async with db_factory() as db:
        provider = await _provider(db, "Primary")
        other = await _provider(db, "Already Used")
        await db.commit()

        updated = await update_provider(
            provider.id,
            ProviderUpdate(
                base_url="https://new.invalid/v1",
                api_key="",
                default_model="writer-v2",
                models=["writer-v2", "critic-v2"],
            ),
            db,
        )
        assert updated.id == str(provider.id)
        assert updated.base_url == "https://new.invalid/v1"
        assert updated.default_model == "writer-v2"
        assert updated.models == ["writer-v2", "critic-v2"]
        assert updated.status == "untested"
        assert updated.has_saved_api_key is False
        assert updated.created_at is not None
        assert updated.updated_at is not None
        assert "api_key" not in updated.model_dump()

        with pytest.raises(HTTPException) as exc_info:
            await update_provider(
                provider.id,
                ProviderUpdate(name=" already used "),
                db,
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "provider_name_conflict"
        persisted = await db.get(LlmProvider, provider.id)
        assert persisted is not None
        assert persisted.name == "Primary"
        assert other.name == "Already Used"


@pytest.mark.asyncio
async def test_model_binding_uuid_validation_is_consistently_422(db_factory):
    async with db_factory() as db:
        provider = await _provider(db, "UUID Provider")
        project = Project(title="UUID Project")
        db.add(project)
        await db.flush()
        binding = ModelBinding(
            provider_id=provider.id,
            project_id=project.id,
            model_name="writer",
            agent_role="Drafter",
        )
        db.add(binding)
        await db.commit()

        invalid_calls = [
            lambda: create_model_binding(
                ModelBindingCreate(provider_id="not-a-uuid", model="writer"), db
            ),
            lambda: create_model_binding(
                ModelBindingCreate(
                    provider_id=str(provider.id),
                    project_id="not-a-uuid",
                    model="writer",
                ),
                db,
            ),
            lambda: update_model_binding(
                binding.id, ModelBindingUpdate(provider_id="not-a-uuid"), db
            ),
            lambda: update_model_binding(
                binding.id, ModelBindingUpdate(project_id="not-a-uuid"), db
            ),
            lambda: list_model_bindings(provider_id="not-a-uuid", db=db),
            lambda: list_model_bindings(project_id="not-a-uuid", db=db),
            lambda: run_provider_test(ProviderTestRequest(provider_id="not-a-uuid"), db),
        ]
        for make_call in invalid_calls:
            with pytest.raises(HTTPException) as exc_info:
                await make_call()
            assert exc_info.value.status_code == 422
            assert exc_info.value.detail["code"] == "invalid_uuid"


@pytest.mark.asyncio
async def test_binding_scope_conflicts_return_409_and_patch_response_is_complete(db_factory):
    async with db_factory() as db:
        first_provider = await _provider(db, "First")
        second_provider = await _provider(db, "Second")
        project = Project(title="Scoped project")
        db.add(project)
        await db.flush()
        drafter = ModelBinding(
            provider_id=first_provider.id,
            model_name="draft-v1",
            agent_role="Drafter",
            project_id=project.id,
        )
        critic = ModelBinding(
            provider_id=first_provider.id,
            model_name="critic-v1",
            agent_role="Critic",
            project_id=project.id,
        )
        db.add_all([drafter, critic])
        await db.commit()

        with pytest.raises(HTTPException) as create_conflict:
            await create_model_binding(
                ModelBindingCreate(
                    provider_id=str(second_provider.id),
                    project_id=str(project.id),
                    agent_role="Drafter",
                    model="another-drafter",
                ),
                db,
            )
        assert create_conflict.value.status_code == 409
        assert create_conflict.value.detail["code"] == "model_binding_conflict"

        with pytest.raises(HTTPException) as patch_conflict:
            await update_model_binding(
                critic.id,
                ModelBindingUpdate(agent_role="Drafter"),
                db,
            )
        assert patch_conflict.value.status_code == 409
        unchanged = await db.get(ModelBinding, critic.id)
        assert unchanged is not None
        assert unchanged.agent_role == "Critic"

        response = await update_model_binding(
            critic.id,
            ModelBindingUpdate(
                provider_id=str(second_provider.id),
                model="critic-v2",
                display_name="Quality critic",
                context_window=128_000,
                max_output_tokens=16_000,
                cost_per_1k_input=0.12,
                cost_per_1k_output=0.34,
                capabilities={"json": True, "vision": False},
                is_default=True,
            ),
            db,
        )
        assert response.provider_id == str(second_provider.id)
        assert response.provider_name == "Second"
        assert response.model_name == "critic-v2"
        assert response.model == "critic-v2"
        assert response.display_name == "Quality critic"
        assert response.context_window == 128_000
        assert response.max_output_tokens == 16_000
        assert response.cost_per_1k_input == 0.12
        assert response.cost_per_1k_output == 0.34
        assert response.capabilities == {"json": True, "vision": False}
        assert response.is_default is True
        assert response.created_at is not None
        assert response.updated_at is not None


@pytest.mark.asyncio
async def test_provider_delete_conflict_then_force_cascades_and_binding_delete_is_descriptive(
    db_factory,
):
    async with db_factory() as db:
        provider = await _provider(db, "Cascade Provider")
        project = Project(title="Cascade project")
        db.add(project)
        await db.flush()
        first = ModelBinding(
            provider_id=provider.id,
            project_id=project.id,
            model_name="writer",
            agent_role="Drafter",
            is_default=True,
        )
        second = ModelBinding(
            provider_id=provider.id,
            project_id=project.id,
            model_name="critic",
            agent_role="Critic",
        )
        db.add_all([first, second])
        await db.commit()

        with pytest.raises(HTTPException) as conflict:
            await delete_provider(provider.id, force=False, db=db)
        assert conflict.value.status_code == 409
        assert await db.scalar(select(func.count(LlmProvider.id))) == 1
        assert await db.scalar(select(func.count(ModelBinding.id))) == 2

        binding_response = await delete_model_binding(first.id, db)
        assert binding_response.ok is True
        assert binding_response.provider_id == str(provider.id)
        assert binding_response.model_name == "writer"
        assert binding_response.agent_role == "Drafter"
        assert binding_response.project_id == str(project.id)
        assert binding_response.was_default is True

        deleted = await delete_provider(provider.id, force=True, db=db)
        assert deleted.ok is True
        assert deleted.provider_id == str(provider.id)
        assert deleted.name == "Cascade Provider"
        assert deleted.force is True
        assert deleted.deleted_bindings == 1
        assert await db.scalar(select(func.count(LlmProvider.id))) == 0
        assert await db.scalar(select(func.count(ModelBinding.id))) == 0
        assert await db.get(Project, project.id) is not None
