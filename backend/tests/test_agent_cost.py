"""Agent usage must use configured model pricing instead of permanent zeroes."""

from __future__ import annotations

import pytest
from app.agents.base import BaseAgent
from app.core.database import Base
from app.db.models.project import Project
from app.db.models.provider import LlmProvider, ModelBinding
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_agent_run_calculates_binding_cost():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="成本测试")
        provider = LlmProvider(name="真实 API", is_active=True)
        db.add_all([project, provider])
        await db.flush()
        db.add(
            ModelBinding(
                provider_id=provider.id,
                project_id=project.id,
                agent_role="Drafter",
                model_name="priced-model",
                cost_per_1k_input=0.5,
                cost_per_1k_output=1.5,
            )
        )
        await db.flush()

        agent = BaseAgent(object(), db, project.id)
        agent.agent_name = "Drafter"
        run = await agent._save_agent_run(
            input_tokens=2000,
            output_tokens=3000,
            result={"model": "priced-model"},
        )

        assert run.cost == 5.5

    await engine.dispose()
