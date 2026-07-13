"""Autonomous learning must produce evidence-backed, reusable context."""

from __future__ import annotations

import pytest
from app.core.database import Base
from app.db.models.chapter import Chapter
from app.db.models.memory import BookMemory, PlanningReflection
from app.db.models.project import Project
from app.db.models.quality import HumanFeedbackEvent, LearningCycle, PromptVersion
from app.services.autonomous_learning import AutonomousLearningService
from app.services.quality_ledger import QualityLedger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_post_chapter_learning_is_idempotent_and_feeds_book_memory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="北辰纪", status="draft")
        db.add(project)
        await db.flush()
        chapter = Chapter(
            project_id=project.id,
            chapter_no=3,
            title="第三章",
            status="approved",
        )
        db.add(chapter)
        await db.flush()

        ledger = QualityLedger(db, project.id)
        for round_no in range(2):
            await ledger.record_critic_assessment(
                idempotency_key=f"critic-{round_no}",
                result={
                    "scores": {"logic": 72},
                    "overall_score": 72,
                    "verdict": "rewrite",
                    "issues": [
                        {
                            "source": "Critic",
                            "category": "logic",
                            "severity": "high",
                            "description": "关键转折缺少前置动机",
                        }
                    ],
                },
                chapter_id=chapter.id,
                round_no=round_no,
            )
        await ledger.record_feedback(
            idempotency_key="feedback-1",
            action="revise",
            chapter_id=chapter.id,
            instruction="减少空泛抒情，让每段都推动人物选择。",
        )

        service = AutonomousLearningService(db, project.id)
        first = await service.run_post_chapter_cycle(chapter_no=3)
        second = await service.run_post_chapter_cycle(chapter_no=3)

        assert first["status"] == "completed"
        assert first["memory_count"] == 2
        assert second["reused"] is True
        assert second["cycle_id"] == first["cycle_id"]
        assert await db.scalar(select(func.count(LearningCycle.id))) == 1
        assert await db.scalar(select(func.count(PlanningReflection.id))) == 1

        memories = (await db.execute(select(BookMemory))).scalars().all()
        assert {item.memory_type for item in memories} == {"lesson", "preference"}
        lesson = next(item for item in memories if item.memory_type == "lesson")
        assert lesson.value["evidence_count"] == 2
        preference = next(item for item in memories if item.memory_type == "preference")
        assert "推动人物选择" in preference.value["instruction"]

        # Two lessons are useful immediately as memory, but insufficient for a
        # risky prompt mutation; no fake "self-improvement" promotion occurs.
        assert await db.scalar(select(func.count(PromptVersion.id))) == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_reject_and_takeover_feedback_never_become_positive_preferences():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="反馈极性隔离", status="draft")
        db.add(project)
        await db.flush()
        ledger = QualityLedger(db, project.id)
        no_reason_reject = await ledger.record_feedback(
            idempotency_key="reject-without-reason",
            action="reject",
            original_text="这段被用户拒绝的正文绝不能被当作正向偏好。",
        )
        reasoned_reject = await ledger.record_feedback(
            idempotency_key="reject-with-reason",
            action="reject",
            original_text="被拒正文",
            instruction="不要让主角靠巧合脱险",
        )
        no_reason_takeover = await ledger.record_feedback(
            idempotency_key="takeover-without-reason",
            action="takeover",
            original_text="接管前的正文不是偏好样本",
        )
        constrained_takeover = await ledger.record_feedback(
            idempotency_key="takeover-with-constraint",
            action="takeover",
            instruction="本段必须由反派主动结束谈判",
        )

        service = AutonomousLearningService(db, project.id)
        learned = await service._learn_feedback(
            [
                no_reason_reject,
                reasoned_reject,
                no_reason_takeover,
                constrained_takeover,
            ],
            chapter_no=4,
        )

        assert len(learned) == 2
        memories = (await db.execute(select(BookMemory))).scalars().all()
        assert len(memories) == 2
        assert {memory.memory_type for memory in memories} == {"lesson"}
        by_action = {memory.value["action"]: memory for memory in memories}
        assert by_action["reject"].value["polarity"] == "negative"
        assert "避免重复" in by_action["reject"].value["instruction"]
        assert "被拒正文" not in by_action["reject"].value["instruction"]
        assert by_action["takeover"].value["polarity"] == "neutral"
        assert "创作约束" in by_action["takeover"].value["instruction"]
        assert no_reason_reject.learning_status == "skipped"
        assert no_reason_takeover.learning_status == "skipped"
        assert reasoned_reject.learning_status == "processed"
        assert constrained_takeover.learning_status == "processed"
        assert await db.scalar(select(func.count(HumanFeedbackEvent.id))) == 4

    await engine.dispose()
