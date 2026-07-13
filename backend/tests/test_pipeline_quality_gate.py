"""Pipeline safety invariants for unattended multi-chapter production."""

from __future__ import annotations

import asyncio
from types import MethodType, SimpleNamespace

import app.pipeline.orchestrator as orchestrator_module
import pytest
from app.core.database import Base
from app.db.models.chapter import Chapter, ChapterVersion, ManuscriptBlock
from app.db.models.project import Project
from app.db.models.quality import QualityAssessment
from app.db.models.session import ReviewQueueItem, WorkSession
from app.pipeline.orchestrator import PipelineOrchestrator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


async def _project_and_session(db):
    project = Project(title="长夜航线", status="draft")
    db.add(project)
    await db.flush()
    work_session = WorkSession(
        project_id=project.id,
        title="连续创作",
        goal="持续推进",
        mode="L4",
        status="running",
    )
    db.add(work_session)
    await db.flush()
    return project, work_session


@pytest.mark.asyncio
async def test_unattended_bible_generation_consumes_blueprint_conversation_and_outline(
    monkeypatch,
    db_factory,
):
    captured: dict = {}

    class CapturingArchitect:
        def __init__(self, **kwargs):
            self.custom_system_prompt = ""

        async def generate_world_bible(self, hints):
            captured.update(hints)
            return SimpleNamespace(
                id=orchestrator_module.uuid.uuid4(),
                content={"world_name": "雾港"},
                summary="蓝图驱动的世界观",
            )

    async with db_factory() as db:
        project = Project(
            title="雾港余烬",
            genre="工业奇幻",
            synopsis="失忆测绘师追查海雾中的旧城。",
            extra={
                "creation_blueprint": {
                    "protagonist_name": "沈砚",
                    "core_conflict": "记忆税与城市真相",
                    "world_rules": ["每次施术必须遗忘一段记忆"],
                    "tone": "冷峻悬疑",
                },
                "creative_conversation": [
                    {"role": "user", "content": "结尾必须让主角主动放弃最珍贵的记忆。"},
                    {"role": "assistant", "content": "已纳入故事承诺。"},
                ],
                "outline_text": "第一卷：雾税。第一章，港口出现不存在的街道。",
                "outline_filename": "雾港大纲.docx",
            },
        )
        db.add(project)
        await db.flush()
        orchestrator = PipelineOrchestrator(object(), db, project.id)

        async def no_prompt_load(self):
            return None

        orchestrator._load_custom_prompt = MethodType(no_prompt_load, orchestrator)
        monkeypatch.setattr(orchestrator_module, "StoryArchitect", CapturingArchitect)

        result = await orchestrator.generate_bible(hints={})

        assert result["status"] == "completed"
        assert captured["title"] == "雾港余烬"
        assert captured["genre"] == "工业奇幻"
        assert captured["protagonist_name"] == "沈砚"
        assert captured["core_conflict"] == "记忆税与城市真相"
        assert captured["world_rules"] == ["每次施术必须遗忘一段记忆"]
        assert "不存在的街道" in captured["outline_text"]
        assert "主动放弃最珍贵的记忆" in captured["creative_prompt"]


@pytest.mark.asyncio
async def test_review_is_not_success_and_pipeline_never_advances(db_factory):
    async with db_factory() as db:
        project, work_session = await _project_and_session(db)
        orchestrator = PipelineOrchestrator(object(), db, project.id, work_session.id)
        called: list[int] = []

        async def fake_run_chapter(self, chapter_no: int, mode: str = "L2"):
            called.append(chapter_no)
            return {"chapter_no": chapter_no, "status": "review", "score": 76}

        orchestrator.run_chapter = MethodType(fake_run_chapter, orchestrator)
        result = await orchestrator.run_pipeline(3, mode="L4", start_chapter=7)

        assert called == [7]
        assert result["status"] == "waiting_review"
        assert result["success_count"] == 0
        assert result["review_count"] == 1
        assert work_session.status == "waiting_review"


@pytest.mark.asyncio
async def test_next_chapter_resumes_earliest_unapproved_chapter(db_factory):
    async with db_factory() as db:
        project, work_session = await _project_and_session(db)
        db.add_all(
            [
                Chapter(project_id=project.id, chapter_no=1, title="一", status="approved"),
                Chapter(
                    project_id=project.id,
                    chapter_no=2,
                    title="二",
                    status="review",
                    word_count=3200,
                ),
                Chapter(project_id=project.id, chapter_no=3, title="三", status="draft"),
            ]
        )
        await db.flush()
        orchestrator = PipelineOrchestrator(object(), db, project.id, work_session.id)

        assert await orchestrator._get_next_chapter_no() == 2


@pytest.mark.asyncio
async def test_review_queue_item_is_idempotent(db_factory):
    async with db_factory() as db:
        project, work_session = await _project_and_session(db)
        chapter = Chapter(project_id=project.id, chapter_no=4, title="第四章", status="review")
        db.add(chapter)
        await db.flush()
        orchestrator = PipelineOrchestrator(object(), db, project.id, work_session.id)
        kwargs = {
            "chapter": chapter,
            "score": 73,
            "quality_threshold": 85,
            "critic_result": {"issues": [{"severity": "high"}]},
            "continuity_result": {"conflicts": [{"description": "时间线冲突"}]},
            "notes": "需要重写",
        }

        first = await orchestrator._ensure_review_item(**kwargs)
        second = await orchestrator._ensure_review_item(**kwargs)
        count = await db.scalar(
            select(func.count(ReviewQueueItem.id)).where(ReviewQueueItem.project_id == project.id)
        )

        assert first.id == second.id
        assert count == 1
        assert first.risk_level == "high"


@pytest.mark.asyncio
async def test_persisted_best_candidate_survives_later_quality_retry_regression(db_factory):
    async with db_factory() as db:
        project, work_session = await _project_and_session(db)
        chapter = Chapter(
            project_id=project.id,
            chapter_no=3,
            title="第三章",
            status="review",
        )
        db.add(chapter)
        await db.flush()
        strong = ChapterVersion(
            chapter_id=chapter.id,
            version_no=1,
            content="较强候选正文。\n\n时间线与人物位置均保持一致。",
            word_count=22,
            status="revision",
            created_by_agent="Rewriter:round-4",
        )
        regressed = ChapterVersion(
            chapter_id=chapter.id,
            version_no=2,
            content="后一次重试正文。\n\n它引入了新的连续性冲突。",
            word_count=21,
            status="draft",
            created_by_agent="Drafter",
        )
        db.add_all([strong, regressed])
        await db.flush()

        def assessment(
            version: ChapterVersion,
            kind: str,
            *,
            score: float,
            verdict: str,
            passed: bool,
            raw: dict,
        ) -> QualityAssessment:
            return QualityAssessment(
                project_id=project.id,
                session_id=work_session.id,
                chapter_id=chapter.id,
                version_id=version.id,
                idempotency_key=f"{version.id}:{kind}",
                assessor="Critic" if kind == "critic" else "ContinuityGuard",
                assessment_type=kind,
                round_no=4,
                rubric_version="test",
                dimension_scores={kind: score},
                overall_score=score,
                verdict=verdict,
                passed=passed,
                raw_result=raw,
            )

        db.add_all(
            [
                assessment(
                    strong,
                    "critic",
                    score=85.4,
                    verdict="rewrite",
                    passed=False,
                    raw={"overall_score": 85.4, "verdict": "rewrite", "issues": []},
                ),
                assessment(
                    strong,
                    "continuity",
                    score=100,
                    verdict="pass",
                    passed=True,
                    raw={"passed": True, "conflicts": [], "warnings": []},
                ),
                assessment(
                    regressed,
                    "critic",
                    score=84,
                    verdict="rewrite",
                    passed=False,
                    raw={"overall_score": 84, "verdict": "rewrite", "issues": []},
                ),
                assessment(
                    regressed,
                    "continuity",
                    score=0,
                    verdict="rewrite",
                    passed=False,
                    raw={
                        "passed": False,
                        "conflicts": [{"severity": "high", "description": "时间线冲突"}],
                        "warnings": [],
                    },
                ),
            ]
        )
        await db.flush()
        orchestrator = PipelineOrchestrator(
            object(),
            db,
            project.id,
            work_session.id,
        )

        selected = await orchestrator._best_persisted_candidate(chapter.id, 85)

        assert selected is not None
        assert selected["id"] == strong.id
        assert selected["version_no"] == 1
        assert selected["continuity_result"]["passed"] is True


@pytest.mark.asyncio
async def test_staged_continuous_pipeline_does_not_lock_heartbeat_during_long_llm(
    monkeypatch,
    tmp_path,
):
    database = tmp_path / "staged-heartbeat.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database}",
        connect_args={"timeout": 0.2},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(Base.metadata.create_all)

    planner_started = asyncio.Event()
    allow_planner = asyncio.Event()

    class BlockingPlanner:
        def __init__(self, **kwargs):
            self.custom_system_prompt = ""
            self.prompt_provenance = {}

        async def plan_chapter(self, chapter_no):
            planner_started.set()
            await allow_planner.wait()
            return {
                "chapter_no": chapter_no,
                "chapter_title": "不持锁的长调用",
                "overall_goal": "验证租约可续期",
                "scene_list": [],
            }

    class FakeCompiler:
        def __init__(self, *args, **kwargs):
            pass

        async def compile(self, *args, **kwargs):
            return SimpleNamespace(
                system_prompt="",
                context_text="",
                total_tokens=0,
                budget_breakdown={},
                provenance={},
                prompt_provenance={},
            )

    class FakeDrafter:
        def __init__(self, **kwargs):
            self.custom_system_prompt = ""
            self.prompt_provenance = {}

        async def draft_chapter(self, plan, chapter_id=None):
            return [
                ManuscriptBlock(
                    chapter_id=chapter_id,
                    block_no=1,
                    block_type="paragraph",
                    content="完整正文" * 700,
                )
            ]

    class FakeCritic:
        def __init__(self, **kwargs):
            self.custom_system_prompt = ""
            self.prompt_provenance = {}

        async def review_texts(self, block_texts, chapter_plan=None):
            return {
                "scores": {"quality": 92},
                "issues": [],
                "overall_score": 92,
                "verdict": "pass",
            }

    class FakeGuard:
        def __init__(self, **kwargs):
            self.custom_system_prompt = ""
            self.prompt_provenance = {}

        async def check_texts(self, block_texts, chapter_no):
            return {"passed": True, "conflicts": [], "warnings": []}

    class FakeMemoryKeeper:
        def __init__(self, **kwargs):
            self.custom_system_prompt = ""
            self.prompt_provenance = {}

        async def prepare_state_update(self, chapter_no, block_texts):
            return {
                "chapter_no": chapter_no,
                "manuscript_text": "完整正文" * 700,
                "word_count": 2800,
                "summary_result": {},
            }

        async def apply_prepared_state(self, chapter_id, prepared):
            return {"chapter_no": prepared["chapter_no"]}

    async def no_prompt_load(self):
        return None

    async def learning_done(self, **kwargs):
        return {"status": "completed"}

    monkeypatch.setattr(orchestrator_module, "ChapterPlanner", BlockingPlanner)
    monkeypatch.setattr(orchestrator_module, "ContextCompiler", FakeCompiler)
    monkeypatch.setattr(orchestrator_module, "Drafter", FakeDrafter)
    monkeypatch.setattr(orchestrator_module, "Critic", FakeCritic)
    monkeypatch.setattr(orchestrator_module, "ContinuityGuard", FakeGuard)
    monkeypatch.setattr(orchestrator_module, "MemoryKeeper", FakeMemoryKeeper)
    monkeypatch.setattr(
        orchestrator_module.AutonomousLearningService,
        "run_post_chapter_cycle",
        learning_done,
    )

    try:
        async with factory() as setup_db:
            project, work_session = await _project_and_session(setup_db)
            await setup_db.commit()
            project_id = project.id
            session_id = work_session.id

        async with factory() as orchestration_db:
            orchestrator = PipelineOrchestrator(
                object(),
                orchestration_db,
                project_id,
                session_id,
                quality_threshold=85,
                max_rewrite_rounds=0,
                agent_run_db_factory=factory,
            )
            orchestrator._load_custom_prompt = MethodType(no_prompt_load, orchestrator)
            chapter_task = asyncio.create_task(orchestrator.run_chapter(1, mode="L3"))
            await asyncio.wait_for(planner_started.wait(), timeout=2)

            # This commit stands in for the independent fenced heartbeat.  It
            # must succeed while the model call is still deliberately blocked.
            async with factory() as heartbeat_db:
                heartbeat_session = await heartbeat_db.get(WorkSession, session_id)
                assert heartbeat_session is not None
                heartbeat_session.progress_percent = 17.0
                await asyncio.wait_for(heartbeat_db.commit(), timeout=1)

            allow_planner.set()
            result = await asyncio.wait_for(chapter_task, timeout=5)
            await orchestration_db.commit()
            assert result["status"] == "approved"
            assert result["score"] == 92
    finally:
        allow_planner.set()
        await engine.dispose()


@pytest.mark.asyncio
async def test_approved_newer_version_archives_stale_pending_quality_gate(db_factory):
    async with db_factory() as db:
        project, work_session = await _project_and_session(db)
        chapter = Chapter(
            project_id=project.id,
            chapter_no=5,
            title="approved after retry",
            status="approved",
        )
        db.add(chapter)
        await db.flush()
        pending = ReviewQueueItem(
            project_id=project.id,
            session_id=work_session.id,
            item_type="quality_gate",
            artifact_type="chapter",
            artifact_id=chapter.id,
            title="older version failed",
            status="pending",
            risk_level="high",
            chapter_no=chapter.chapter_no,
        )
        db.add(pending)
        await db.flush()
        orchestrator = PipelineOrchestrator(object(), db, project.id, work_session.id)

        resolved = await orchestrator._resolve_pending_review_items(chapter)

        assert resolved == 1
        assert pending.status == "approved"
        assert pending.decided_by == "system"
        assert pending.decided_at is not None
        assert "newer version" in (pending.decision_notes or "")


@pytest.mark.asyncio
async def test_agent_failure_rolls_back_partial_chapter_work(monkeypatch, db_factory):
    async with db_factory() as db:
        project, work_session = await _project_and_session(db)
        chapter = Chapter(project_id=project.id, chapter_no=1, title="第一章", status="review")
        db.add(chapter)
        await db.flush()
        previous = ChapterVersion(
            chapter_id=chapter.id,
            version_no=1,
            content="上一版完整正文",
            word_count=8,
            status="draft",
        )
        block = ManuscriptBlock(
            chapter_id=chapter.id,
            block_no=1,
            content="上一版完整正文",
        )
        db.add_all([previous, block])
        await db.flush()

        async def fail_plan(self, chapter_no: int):
            raise RuntimeError("planner unavailable")

        monkeypatch.setattr(orchestrator_module.ChapterPlanner, "plan_chapter", fail_plan)
        orchestrator = PipelineOrchestrator(object(), db, project.id, work_session.id)
        result = await orchestrator.run_chapter(1, mode="L4")

        assert result["status"] == "failed"
        assert chapter.status == "failed"
        blocks = (
            (
                await db.execute(
                    select(ManuscriptBlock).where(ManuscriptBlock.chapter_id == chapter.id)
                )
            )
            .scalars()
            .all()
        )
        versions = (
            (
                await db.execute(
                    select(ChapterVersion).where(ChapterVersion.chapter_id == chapter.id)
                )
            )
            .scalars()
            .all()
        )
        assert [item.content for item in blocks] == ["上一版完整正文"]
        assert [item.content for item in versions] == ["上一版完整正文"]
