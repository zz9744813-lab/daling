"""Learned memories remain reviewable and reversible in production context."""

from __future__ import annotations

import pytest
from app.api.routes_book_memory import _to_out
from app.api.routes_evolution import get_evolution
from app.context.book_memory_manager import BookMemoryManager
from app.context.compiler import ContextCompiler
from app.core.database import Base
from app.db.models.memory import BookMemory
from app.db.models.project import Project
from app.services.autonomous_learning import AutonomousLearningService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_memory_reject_approve_and_rollback_control_compiled_context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="记忆治理测试")
        db.add(project)
        await db.flush()
        manager = BookMemoryManager(db, project.id)
        memory = await manager.add_memory(
            memory_type="lesson",
            key="causality",
            value={"instruction": "关键转折必须有前置动机"},
            source="quality-ledger",
        )
        compiler = ContextCompiler(db, project.id)

        assert "前置动机" in await compiler._get_style_memory()
        rejected = await manager.review_memory(
            memory.id,
            action="reject",
            actor="editor",
            reason="证据不足",
        )
        assert _to_out(rejected).status == "rejected"
        assert "前置动机" not in await compiler._get_style_memory()

        approved = await manager.review_memory(
            memory.id,
            action="approve",
            actor="editor",
            reason="人工复核通过",
        )
        assert _to_out(approved).status == "active"
        assert "前置动机" in await compiler._get_style_memory()

        rolled_back = await manager.review_memory(
            memory.id,
            action="rollback",
            actor="editor",
            reason="线上表现退化",
        )
        output = _to_out(rolled_back)
        assert output.status == "rolled_back"
        assert len(output.governance["history"]) == 3
        assert "前置动机" not in await compiler._get_style_memory()

        overview = await get_evolution(project.id, db)
        assert overview.memory_entries[0]["status"] == "rolled_back"
        assert overview.memory_entries[0]["project_id"] == str(project.id)
        assert overview.memory_entries[0]["value"] == {
            "instruction": "关键转折必须有前置动机"
        }
        assert overview.memory_status_counts == {
            "active": 0,
            "rejected": 0,
            "rolled_back": 1,
        }
        assert overview.quality_report["lessons_learned"] == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_autonomous_refresh_does_not_reactivate_rejected_memory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="拒绝保持测试")
        db.add(project)
        await db.flush()
        memory = BookMemory(
            project_id=project.id,
            memory_type="lesson",
            key="avoid-Critic-logic",
            value={
                "instruction": "旧规则",
                "_governance": {
                    "status": "rejected",
                    "origin": "autonomous_learning",
                    "history": [],
                },
            },
            source="quality-ledger:Critic:logic",
        )
        db.add(memory)
        await db.flush()

        service = AutonomousLearningService(db, project.id)
        refreshed = await service._upsert_memory(
            memory_type="lesson",
            key="avoid-Critic-logic",
            value={"instruction": "有更多证据的新版规则", "evidence_count": 7},
            source="quality-ledger:Critic:logic",
            confidence=0.9,
        )
        assert refreshed.value["instruction"] == "有更多证据的新版规则"
        assert refreshed.value["_governance"]["status"] == "rejected"
        assert "新版规则" not in await ContextCompiler(db, project.id)._get_style_memory()

    await engine.dispose()
