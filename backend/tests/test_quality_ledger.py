"""Structured quality ledger and deterministic Critic policy tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.agents.critic import Critic
from app.core.database import Base
from app.db.models.chapter import Chapter, ChapterVersion
from app.db.models.project import Project
from app.db.models.quality import (
    HumanFeedbackEvent,
    QualityAssessment,
    QualityIssue,
    RevisionAttempt,
)
from app.services.quality_ledger import QualityLedger, fingerprint_issue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def ledger_context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        project = Project(title="质量台账测试")
        db.add(project)
        await db.flush()
        yield db, project.id
    await engine.dispose()


def _critic_with_result(result: dict) -> Critic:
    critic = Critic(
        gateway=MagicMock(),
        db=MagicMock(),
        project_id=MagicMock(),
    )
    critic._llm_json = AsyncMock(return_value=result)
    critic._get_characters_info = AsyncMock(return_value="角色信息")
    return critic


@pytest.mark.asyncio
@pytest.mark.parametrize("severity", ["high", "critical"])
async def test_critic_high_or_critical_issue_forces_rewrite_and_clamps_scores(severity):
    critic = _critic_with_result(
        {
            "scores": {"prose_quality": 130, "pacing": -5, "plot_coherence": "bad"},
            "issues": [
                {
                    "severity": severity,
                    "category": "canon",
                    "description": "违反不可变设定",
                }
            ],
            "overall_score": 120,
            "verdict": "pass",
        }
    )

    result = await critic.review_texts([{"content": "正文", "block_no": 1}])

    assert result["scores"] == {
        "prose_quality": 100.0,
        "pacing": 0.0,
        "plot_coherence": 0.0,
    }
    assert result["overall_score"] == 100.0
    assert result["issues"][0]["severity"] == severity
    assert result["verdict"] == "rewrite"


@pytest.mark.asyncio
async def test_critic_medium_issue_forces_revise_but_clean_high_score_passes():
    medium_critic = _critic_with_result(
        {
            "scores": {"prose_quality": 96},
            "issues": [{"severity": "warning", "description": "节奏略快"}],
            "overall_score": 96,
            "verdict": "pass",
        }
    )
    clean_critic = _critic_with_result(
        {
            "scores": {"prose_quality": 90},
            "issues": [],
            "overall_score": 90,
            "verdict": "rewrite",
        }
    )

    medium_result = await medium_critic.review_texts([{"content": "正文"}])
    clean_result = await clean_critic.review_texts([{"content": "正文"}])

    assert medium_result["issues"][0]["severity"] == "medium"
    assert medium_result["verdict"] == "revise"
    assert clean_result["verdict"] == "pass"


@pytest.mark.asyncio
async def test_assessment_and_issues_are_idempotent(ledger_context):
    db, project_id = ledger_context
    ledger = QualityLedger(db, project_id)
    result = {
        "scores": {"prose_quality": 72, "pacing": 80},
        "overall_score": 76,
        "verdict": "revise",
        "issues": [
            {
                "severity": "high",
                "category": "plot_coherence",
                "description": "转折缺少铺垫",
                "location": "第三段",
                "suggestion": "增加前置线索",
                "block_no": 3,
            },
            {
                "severity": "high",
                "category": "plot_coherence",
                "description": "转折缺少铺垫",
                "location": "第三段",
                "suggestion": "增加前置线索",
                "block_no": 3,
            },
        ],
    }

    first = await ledger.record_critic_assessment(
        idempotency_key="chapter-1-round-0-critic",
        result=result,
        round_no=0,
    )
    second = await ledger.record_critic_assessment(
        idempotency_key="chapter-1-round-0-critic",
        result={"overall_score": 1, "issues": []},
        round_no=0,
    )

    assessment_count = await db.scalar(select(func.count(QualityAssessment.id)))
    issues = list((await db.scalars(select(QualityIssue))).all())
    assert first.id == second.id
    assert assessment_count == 1
    assert len(issues) == 1
    assert issues[0].severity == "high"
    assert issues[0].block_no == 3
    assert len(issues[0].issue_fingerprint) == 64


@pytest.mark.asyncio
async def test_continuity_conflicts_and_warnings_are_structured(ledger_context):
    db, project_id = ledger_context
    ledger = QualityLedger(db, project_id)

    assessment = await ledger.record_continuity_assessment(
        idempotency_key="chapter-2-round-1-continuity",
        result={
            "passed": False,
            "conflicts": [
                {
                    "type": "timeline",
                    "description": "角色死亡后再次行动",
                    "expected": "角色已死亡",
                    "actual": "角色开口说话",
                }
            ],
            "warnings": [
                {
                    "type": "foreshadow",
                    "description": "伏笔推进不足",
                    "suggestion": "补充一个呼应细节",
                }
            ],
        },
        round_no=1,
    )

    issues = list(
        (
            await db.scalars(
                select(QualityIssue).where(QualityIssue.assessment_id == assessment.id)
            )
        ).all()
    )
    assert assessment.passed is False
    assert assessment.verdict == "rewrite"
    assert {issue.severity for issue in issues} == {"high", "medium"}
    assert {issue.extra["kind"] for issue in issues} == {"conflict", "warning"}
    status_by_kind = {issue.extra["kind"]: issue.status for issue in issues}
    assert status_by_kind == {"conflict": "open", "warning": "advisory"}


@pytest.mark.asyncio
async def test_issue_lifecycle_tracks_current_version_and_reselected_versions(ledger_context):
    db, project_id = ledger_context
    chapter = Chapter(
        project_id=project_id,
        chapter_no=4,
        title="版本问题生命周期",
        status="review",
    )
    db.add(chapter)
    await db.flush()
    first_version = ChapterVersion(
        chapter_id=chapter.id,
        version_no=1,
        content="第一版",
        word_count=3,
        status="revision",
    )
    second_version = ChapterVersion(
        chapter_id=chapter.id,
        version_no=2,
        content="第二版",
        word_count=3,
        status="revision",
    )
    db.add_all([first_version, second_version])
    await db.flush()

    ledger = QualityLedger(db, project_id)
    await ledger.record_critic_assessment(
        idempotency_key="chapter-4-v1-critic",
        result={
            "overall_score": 70,
            "verdict": "revise",
            "issues": [{"category": "logic", "description": "第一版问题"}],
        },
        chapter_id=chapter.id,
        version_id=first_version.id,
    )
    await ledger.record_critic_assessment(
        idempotency_key="chapter-4-v2-critic",
        result={
            "overall_score": 80,
            "verdict": "revise",
            "issues": [{"category": "pacing", "description": "第二版问题"}],
        },
        chapter_id=chapter.id,
        version_id=second_version.id,
    )
    await ledger.sync_chapter_issue_statuses(
        chapter_id=chapter.id,
        current_version_id=second_version.id,
        approved=False,
    )

    issues = list((await db.scalars(select(QualityIssue).order_by(QualityIssue.created_at))).all())
    assert {issue.version_id: issue.status for issue in issues} == {
        first_version.id: "superseded",
        second_version.id: "open",
    }

    # The orchestrator may deliberately select an earlier, higher-scoring version.
    # Its concrete blockers must become active again while the abandoned version
    # becomes historical.
    changed = await ledger.sync_chapter_issue_statuses(
        chapter_id=chapter.id,
        current_version_id=first_version.id,
        approved=False,
    )
    assert changed == {"superseded": 1, "reactivated": 1, "resolved": 0}
    assert {issue.version_id: issue.status for issue in issues} == {
        first_version.id: "open",
        second_version.id: "superseded",
    }

    changed = await ledger.sync_chapter_issue_statuses(
        chapter_id=chapter.id,
        current_version_id=first_version.id,
        approved=True,
    )
    assert changed == {"superseded": 0, "reactivated": 0, "resolved": 1}
    assert {issue.version_id: issue.status for issue in issues} == {
        first_version.id: "resolved",
        second_version.id: "superseded",
    }


@pytest.mark.asyncio
async def test_revision_and_feedback_are_idempotent(ledger_context):
    db, project_id = ledger_context
    ledger = QualityLedger(db, project_id)

    revision_one = await ledger.record_revision_attempt(
        idempotency_key="chapter-3-revision-1",
        status="completed",
        round_no=1,
        trigger_issue_ids=["issue-a", "issue-b"],
        instruction="修正时间线",
        score_before=68,
        score_after=86,
    )
    revision_two = await ledger.record_revision_attempt(
        idempotency_key="chapter-3-revision-1",
        status="failed",
    )
    feedback_one = await ledger.record_feedback(
        idempotency_key="feedback-edit-1",
        action="edit",
        original_text="旧文本",
        edited_text="新文本",
        instruction="以后减少重复解释",
        tags=["style", "concise"],
    )
    feedback_two = await ledger.record_feedback(
        idempotency_key="feedback-edit-1",
        action="reject",
    )

    revision_count = await db.scalar(select(func.count(RevisionAttempt.id)))
    feedback_count = await db.scalar(select(func.count(HumanFeedbackEvent.id)))
    assert revision_one.id == revision_two.id
    assert revision_one.status == "completed"
    assert feedback_one.id == feedback_two.id
    assert feedback_one.action == "edit"
    assert revision_count == 1
    assert feedback_count == 1


def test_issue_fingerprint_is_stable_across_whitespace_and_key_order():
    first = {
        "category": "plot",
        "description": "转折  缺少\n铺垫",
        "location": "第三段",
        "severity": "HIGH",
    }
    second = {
        "severity": "high",
        "location": "第三段",
        "description": "转折 缺少 铺垫",
        "category": "plot",
    }
    assert fingerprint_issue(first, "critic") == fingerprint_issue(second, "critic")
