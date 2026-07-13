"""Evolution API exposes real learning cycles and enforces prompt gates."""

from __future__ import annotations

import pytest
from app.api.routes_evolution import (
    get_evolution,
    promote_prompt_version,
    rollback_prompt_version,
)
from app.core.database import Base
from app.db.models.memory import BookMemory, PlanningReflection
from app.db.models.project import Project
from app.db.models.quality import LearningCycle, PromptVersion
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_evolution_overview_and_prompt_version_gates():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="演化测试")
        db.add(project)
        await db.flush()
        cycle = LearningCycle(
            project_id=project.id,
            idempotency_key="post-chapter:1",
            status="completed",
            assessment_count=3,
            feedback_count=1,
            candidate_memory_ids=["memory-a"],
            promotion_decision="candidate_requires_holdout",
        )
        db.add(cycle)
        await db.flush()
        candidate = PromptVersion(
            project_id=project.id,
            learning_cycle_id=cycle.id,
            scope_key=f"project:{project.id}",
            idempotency_key="candidate-1",
            agent_role="Drafter",
            version_no=1,
            content_hash="a" * 64,
            template="保持角色动机一致",
            status="candidate",
            evaluation_metrics={"holdout_status": "pending"},
        )
        db.add_all(
            [
                candidate,
                BookMemory(
                    project_id=project.id,
                    memory_type="lesson",
                    key="logic",
                    value={"instruction": "补足因果链"},
                ),
                PlanningReflection(
                    project_id=project.id,
                    chapter_no=1,
                    reflection_type="post_chapter",
                    content="质量证据已沉淀",
                ),
            ]
        )
        await db.flush()

        view = await get_evolution(project.id, db)
        assert view.memory_count == 1
        assert view.reflections_count == 1
        assert view.learning_cycles[0]["assessment_count"] == 3
        assert view.prompt_versions[0]["status"] == "candidate"
        assert view.prompt_versions[0]["source"] == {
            "type": "autonomous_learning",
            "learning_cycle_id": str(cycle.id),
            "evidence_count": None,
            "baseline_champion_id": None,
        }
        assert view.memory_entries[0]["project_id"] == str(project.id)
        assert view.memory_status_counts["active"] == 1

        with pytest.raises(HTTPException) as exc_info:
            await promote_prompt_version(project.id, candidate.id, db)
        assert exc_info.value.status_code == 409

        rolled_back = await rollback_prompt_version(project.id, candidate.id, db)
        assert rolled_back["version"]["status"] == "rolled_back"
        assert cycle.promotion_decision == "rolled_back"

    await engine.dispose()
