"""Prompt evolution must be evaluated, auditable, and reversibly activated."""

from __future__ import annotations

import json
import re

import pytest
from app.core.database import Base
from app.db.models.project import Project, ProjectConfig
from app.db.models.quality import LearningCycle, PromptVersion
from app.pipeline.llm_client import LLMResponse
from app.services.prompt_evolution import PromptEvolutionService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class PassingHoldoutClient:
    is_configured = True

    def __init__(self) -> None:
        self.generation_calls = 0
        self.judge_calls = 0

    async def chat(self, messages, **kwargs):
        self.generation_calls += 1
        user = messages[-1]["content"]
        marker = re.search(r"【核对：[^】]+】", user)
        return LLMResponse(
            content=f"这是一段遵守全部设定、因果清楚的测试正文。\n{marker.group(0)}",
            model="fixed-test-model",
            input_tokens=10,
            output_tokens=20,
            cost=0.001,
        )

    async def judge(self, prompt, *, system=None):
        self.judge_calls += 1
        return LLMResponse(
            content=json.dumps(
                {
                    "baseline_score": 80,
                    "candidate_score": 86,
                    "baseline_hard_violations": [],
                    "candidate_hard_violations": [],
                    "reasoning": "候选在不改变事实的前提下改善了动机与因果。",
                },
                ensure_ascii=False,
            ),
            model="fixed-test-model",
            input_tokens=12,
            output_tokens=8,
            cost=0.001,
        )


@pytest.mark.asyncio
async def test_fixed_holdout_requires_explicit_run_and_champion_enters_production():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="Prompt 演进测试")
        db.add(project)
        await db.flush()
        db.add(
            ProjectConfig(
                project_id=project.id,
                key="custom_system_prompt",
                value={"text": "保持克制、冷峻的项目文风。"},
            )
        )
        cycle = LearningCycle(
            project_id=project.id,
            idempotency_key="post-chapter:9",
            status="completed",
        )
        db.add(cycle)
        await db.flush()
        candidate = PromptVersion(
            project_id=project.id,
            learning_cycle_id=cycle.id,
            scope_key=f"project:{project.id}",
            idempotency_key="candidate-v1",
            agent_role="Drafter",
            version_no=1,
            content_hash="a" * 64,
            template="每个转折都给出前置动机和可见代价。",
            status="candidate",
            evaluation_metrics={"holdout_status": "pending"},
        )
        db.add(candidate)
        await db.flush()

        client = PassingHoldoutClient()
        service = PromptEvolutionService(db, project.id, llm_client=client)

        before = await service.resolve_production_prompt("Drafter")
        assert "项目文风" in before.text
        assert "前置动机" not in before.text

        metrics = await service.evaluate_holdout(candidate.id)
        assert metrics["holdout_status"] == "passed"
        assert metrics["gate_passed"] is True
        assert metrics["quality_gain"] == 6
        assert metrics["hard_constraint_regression"] is False
        assert candidate.status == "candidate"  # evaluation never auto-promotes
        assert cycle.promotion_decision == "holdout_passed_requires_manual_promotion"
        assert client.generation_calls == 6
        assert client.judge_calls == 3

        # A repeated non-forced request is idempotent and consumes no model calls.
        cached = await service.evaluate_holdout(candidate.id)
        assert cached["evaluation_id"] == metrics["evaluation_id"]
        assert client.generation_calls == 6

        promoted, previous = await service.promote(candidate.id)
        assert previous is None
        assert promoted.status == "champion"
        production = await service.resolve_production_prompt("Drafter")
        assert "项目文风" in production.text
        assert "前置动机" in production.text
        assert production.active_version_id == candidate.id
        assert [source["source"] for source in production.provenance] == [
            "project_config",
            "prompt_version",
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_rollback_restores_exact_previous_qualified_champion():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="回滚链测试")
        db.add(project)
        await db.flush()
        client = PassingHoldoutClient()
        service = PromptEvolutionService(db, project.id, llm_client=client)

        first = PromptVersion(
            project_id=project.id,
            scope_key=f"project:{project.id}",
            idempotency_key="v1",
            agent_role="Drafter",
            version_no=1,
            content_hash="1" * 64,
            template="规则一：动作连续。",
            status="candidate",
            evaluation_metrics={"holdout_status": "pending"},
        )
        db.add(first)
        await db.flush()
        await service.evaluate_holdout(first.id)
        await service.promote(first.id)

        second = PromptVersion(
            project_id=project.id,
            parent_version_id=first.id,
            scope_key=f"project:{project.id}",
            idempotency_key="v2",
            agent_role="Drafter",
            version_no=2,
            content_hash="2" * 64,
            template="规则二：转折有代价。",
            status="candidate",
            evaluation_metrics={"holdout_status": "pending"},
        )
        db.add(second)
        await db.flush()
        await service.evaluate_holdout(second.id)
        promoted, previous = await service.promote(second.id)
        assert promoted.id == second.id
        assert previous and previous.id == first.id
        assert first.status == "retired"

        rolled_back, restored = await service.rollback(second.id, reason="线上质量退化")
        assert rolled_back.status == "rolled_back"
        assert restored and restored.id == first.id
        assert restored.status == "champion"
        production = await service.resolve_production_prompt("Drafter")
        assert production.active_version_id == first.id
        assert "规则一" in production.text
        assert "规则二" not in production.text

    await engine.dispose()


@pytest.mark.asyncio
async def test_unconfigured_holdout_never_creates_a_fake_pass():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class UnconfiguredClient:
        is_configured = False

    async with factory() as db:
        project = Project(title="无模型测试")
        db.add(project)
        await db.flush()
        candidate = PromptVersion(
            project_id=project.id,
            scope_key=f"project:{project.id}",
            idempotency_key="candidate",
            agent_role="Drafter",
            version_no=1,
            content_hash="f" * 64,
            template="候选规则",
            status="candidate",
            evaluation_metrics={"holdout_status": "pending"},
        )
        db.add(candidate)
        await db.flush()
        service = PromptEvolutionService(db, project.id, llm_client=UnconfiguredClient())
        metrics = await service.evaluate_holdout(candidate.id)
        assert metrics["holdout_status"] == "error"
        assert metrics["gate_passed"] is False
        with pytest.raises(PermissionError):
            await service.promote(candidate.id)

    await engine.dispose()
