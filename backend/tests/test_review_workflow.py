"""Human review actions must mutate manuscript state and learning evidence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import app.api.routes_cockpit as cockpit_routes
import app.api.routes_review_queue as review_routes
import pytest
from app.core.database import Base
from app.db.models.chapter import Chapter, ChapterVersion, ManuscriptBlock
from app.db.models.project import Project
from app.db.models.quality import (
    HumanFeedbackEvent,
    PromptVersion,
    QualityAssessment,
    QualityIssue,
)
from app.db.models.session import ReviewQueueItem, WorkSession
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_review_revision_can_select_stronger_historical_base_version():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="历史最佳版本修订")
        db.add(project)
        await db.flush()
        chapter = Chapter(
            project_id=project.id,
            chapter_no=3,
            title="第三章",
            status="review",
        )
        db.add(chapter)
        await db.flush()
        stronger = ChapterVersion(
            chapter_id=chapter.id,
            version_no=1,
            content="此前通过连续性校验的较强正文。" * 20,
            word_count=280,
            status="revision",
        )
        regressed = ChapterVersion(
            chapter_id=chapter.id,
            version_no=2,
            content="后续重试发生质量倒退的当前正文。" * 20,
            word_count=320,
            status="draft",
        )
        db.add_all([stronger, regressed])
        await db.flush()
        chapter.current_version_id = regressed.id
        item = ReviewQueueItem(
            project_id=project.id,
            item_type="quality_gate",
            artifact_type="chapter",
            artifact_id=chapter.id,
            title="待精修",
            status="pending",
            chapter_no=3,
        )
        db.add(item)
        await db.flush()

        loaded_chapter, loaded_version, content = await review_routes._load_chapter_for_item(
            db,
            item,
            base_version_id=stronger.id,
        )

        assert loaded_chapter.id == chapter.id
        assert loaded_version is not None
        assert loaded_version.id == stronger.id
        assert content == stronger.content
        with pytest.raises(HTTPException) as exc:
            await review_routes._load_chapter_for_item(
                db,
                item,
                base_version_id=uuid.uuid4(),
            )
        assert exc.value.status_code == 409

    await engine.dispose()


@pytest.mark.asyncio
async def test_user_revision_creates_versions_rechecks_and_records_feedback(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="审阅闭环")
        db.add(project)
        await db.flush()
        session = WorkSession(
            project_id=project.id,
            title="自动写作",
            goal="完成章节",
            status="paused",
            quality_threshold=85,
        )
        chapter = Chapter(
            project_id=project.id,
            chapter_no=1,
            title="第一章",
            status="review",
            word_count=120,
        )
        db.add_all([session, chapter])
        await db.flush()
        original = ChapterVersion(
            chapter_id=chapter.id,
            version_no=1,
            content="旧正文" * 60,
            word_count=180,
            status="draft",
        )
        db.add(original)
        await db.flush()
        chapter.current_version_id = original.id
        db.add(
            ManuscriptBlock(
                chapter_id=chapter.id,
                version_id=original.id,
                block_no=1,
                content=original.content,
            )
        )
        item = ReviewQueueItem(
            project_id=project.id,
            session_id=session.id,
            item_type="quality_gate",
            artifact_type="chapter",
            artifact_id=chapter.id,
            title="待修改",
            status="pending",
            chapter_no=1,
        )
        db.add(item)
        await db.flush()

        async def critic_pass(self, block_texts, chapter_plan=None):
            return {
                "scores": {"logic": 92},
                "issues": [
                    {
                        "severity": "low",
                        "category": "style",
                        "description": "可选的措辞微调",
                    }
                ],
                "overall_score": 92,
                "verdict": "pass",
            }

        async def continuity_pass(self, block_texts, chapter_no):
            return {"passed": True, "conflicts": [], "warnings": []}

        async def memory_done(self, chapter_id, blocks):
            return {"chapter_no": 1}

        async def learning_done(self, **kwargs):
            return {"status": "completed"}

        monkeypatch.setattr(review_routes.Critic, "review_texts", critic_pass)
        monkeypatch.setattr(review_routes.ContinuityGuard, "check_texts", continuity_pass)
        monkeypatch.setattr(review_routes.MemoryKeeper, "update_state", memory_done)
        monkeypatch.setattr(
            review_routes.AutonomousLearningService,
            "run_post_chapter_cycle",
            learning_done,
        )

        revised = ("新正文明确补足人物动机与因果链。" * 20) + "\n\n" + ("结尾留下新的悬念。" * 20)
        payload = review_routes.DecisionRequest(
            revised_content=revised,
            decision_notes="采用人工精修稿",
        )
        changed_chapter, final_version, approved = await review_routes._revise_and_recheck(
            db,
            item,
            payload,
        )

        assert approved is True
        assert changed_chapter.status == "approved"
        assert final_version.status == "approved"
        assert final_version.content == revised
        assert (
            await db.scalar(
                select(func.count(ChapterVersion.id)).where(ChapterVersion.chapter_id == chapter.id)
            )
            == 2
        )
        assert await db.scalar(select(func.count(QualityAssessment.id))) == 3
        chief_gate = await db.scalar(
            select(QualityAssessment).where(QualityAssessment.assessor == "ChiefEditor")
        )
        assert chief_gate is not None
        assert chief_gate.passed is True
        assert chief_gate.version_id == final_version.id
        issue = (await db.execute(select(QualityIssue))).scalar_one()
        assert issue.version_id == final_version.id
        assert issue.status == "resolved"
        feedback = (await db.execute(select(HumanFeedbackEvent))).scalar_one()
        assert feedback.action == "revise"
        assert feedback.original_text == original.content
        assert feedback.edited_text == revised

    await engine.dispose()


@pytest.mark.asyncio
async def test_manual_recheck_uses_each_role_champion_and_records_provenance(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="人工复检 Prompt 治理")
        db.add(project)
        await db.flush()
        roles = ["Rewriter", "Critic", "ContinuityGuard", "ChiefEditor", "MemoryKeeper"]
        champions: dict[str, PromptVersion] = {}
        for role in roles:
            champion = PromptVersion(
                project_id=project.id,
                scope_key=f"project:{project.id}",
                idempotency_key=f"manual-review-{role}",
                agent_role=role,
                version_no=1,
                content_hash=(role.lower() + "0" * 64)[:64],
                template=f"{role}_CHAMPION_RULE",
                status="champion",
                activated_at=datetime.now(timezone.utc),
            )
            champions[role] = champion
            db.add(champion)

        chapter = Chapter(
            project_id=project.id,
            chapter_no=1,
            title="第一章",
            status="review",
            word_count=200,
        )
        db.add(chapter)
        await db.flush()
        version = ChapterVersion(
            chapter_id=chapter.id,
            version_no=1,
            content="原始正文需要按人工意见修订。" * 30,
            word_count=300,
            status="draft",
        )
        db.add(version)
        await db.flush()
        chapter.current_version_id = version.id
        db.add(
            ManuscriptBlock(
                chapter_id=chapter.id,
                version_id=version.id,
                block_no=1,
                content=version.content,
            )
        )
        item = ReviewQueueItem(
            project_id=project.id,
            item_type="quality_gate",
            artifact_type="chapter",
            artifact_id=chapter.id,
            title="角色 Prompt 复检",
            status="pending",
            chapter_no=1,
        )
        db.add(item)
        await db.flush()

        async def rewrite_with_champion(self, old_blocks, issues, chapter_id=None):
            assert "Rewriter_CHAMPION_RULE" in self.custom_system_prompt
            assert self.prompt_provenance["active_version_id"] == str(champions["Rewriter"].id)
            return self._split_into_blocks("按冠军提示词完成的修订正文。" * 40, chapter_id)

        async def critic_with_champion(self, block_texts, chapter_plan=None):
            assert "Critic_CHAMPION_RULE" in self.custom_system_prompt
            assert self.prompt_provenance["active_version_id"] == str(champions["Critic"].id)
            return {"scores": {"logic": 92}, "issues": [], "overall_score": 92, "verdict": "pass"}

        async def guard_with_champion(self, block_texts, chapter_no):
            assert "ContinuityGuard_CHAMPION_RULE" in self.custom_system_prompt
            assert self.prompt_provenance["active_version_id"] == str(
                champions["ContinuityGuard"].id
            )
            return {"passed": True, "conflicts": [], "warnings": [], "overall_score": 100}

        original_finalize = review_routes.ChiefEditor.finalize

        async def editor_with_champion(self, *args, **kwargs):
            assert "ChiefEditor_CHAMPION_RULE" in self.custom_system_prompt
            assert self.prompt_provenance["active_version_id"] == str(
                champions["ChiefEditor"].id
            )
            return await original_finalize(self, *args, **kwargs)

        async def keeper_with_champion(self, chapter_id, blocks):
            assert "MemoryKeeper_CHAMPION_RULE" in self.custom_system_prompt
            assert self.prompt_provenance["active_version_id"] == str(
                champions["MemoryKeeper"].id
            )
            return {"chapter_no": 1}

        async def learning_done(self, **kwargs):
            return {"status": "completed"}

        monkeypatch.setattr(review_routes.Rewriter, "rewrite_texts", rewrite_with_champion)
        monkeypatch.setattr(review_routes.Critic, "review_texts", critic_with_champion)
        monkeypatch.setattr(review_routes.ContinuityGuard, "check_texts", guard_with_champion)
        monkeypatch.setattr(review_routes.ChiefEditor, "finalize", editor_with_champion)
        monkeypatch.setattr(review_routes.MemoryKeeper, "update_state", keeper_with_champion)
        monkeypatch.setattr(
            review_routes.AutonomousLearningService,
            "run_post_chapter_cycle",
            learning_done,
        )

        _chapter, _version, approved = await review_routes._revise_and_recheck(
            db,
            item,
            review_routes.DecisionRequest(revision_instruction="补足人物主动选择"),
        )

        assert approved is True
        assessments = (await db.execute(select(QualityAssessment))).scalars().all()
        provenance_by_assessor = {
            assessment.assessor: assessment.raw_result["prompt_provenance"]
            for assessment in assessments
        }
        assert provenance_by_assessor["Critic"]["active_version_id"] == str(
            champions["Critic"].id
        )
        assert provenance_by_assessor["ContinuityGuard"]["active_version_id"] == str(
            champions["ContinuityGuard"].id
        )
        assert provenance_by_assessor["ChiefEditor"]["active_version_id"] == str(
            champions["ChiefEditor"].id
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_replayed_reject_or_takeover_does_not_pause_healthy_worker(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    pauses: list[str] = []

    async def record_pause(project_id, reason=""):
        pauses.append(reason)
        return {"desired_state": "paused"}

    monkeypatch.setattr(review_routes.continuous_production_service, "pause", record_pause)

    async with factory() as db:
        project = Project(title="审阅重放保护")
        db.add(project)
        await db.flush()
        item = ReviewQueueItem(
            project_id=project.id,
            item_type="quality_gate",
            title="已经处理",
            status="approved",
        )
        db.add(item)
        await db.flush()

        with pytest.raises(HTTPException) as reject_error:
            await review_routes.reject_item(project.id, item.id, None, db)
        with pytest.raises(HTTPException) as takeover_error:
            await review_routes.takeover_item(project.id, item.id, None, db)

        assert reject_error.value.status_code == 400
        assert takeover_error.value.status_code == 400
        assert pauses == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_chief_editor_reuses_latest_matching_snapshot_when_current_pointer_is_missing():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="终审版本复用")
        db.add(project)
        await db.flush()
        chapter = Chapter(
            project_id=project.id,
            chapter_no=1,
            title="第一章",
            status="review",
        )
        db.add(chapter)
        await db.flush()
        content = "终审前已经保存的不可变正文。" * 20
        latest = ChapterVersion(
            chapter_id=chapter.id,
            version_no=1,
            content=content,
            word_count=len(content),
            status="revision",
            created_by_agent="Rewriter",
        )
        db.add(latest)
        await db.flush()
        db.add(
            ManuscriptBlock(
                chapter_id=chapter.id,
                version_id=latest.id,
                block_no=1,
                content=content,
            )
        )
        await db.flush()
        assert chapter.current_version_id is None

        result = await review_routes.ChiefEditor(
            object(),
            db,
            project.id,
        ).finalize(
            chapter.id,
            {"overall_score": 91, "verdict": "pass", "issues": []},
            {"passed": True, "conflicts": [], "warnings": []},
            quality_threshold=85,
        )

        assert result["approved"] is True
        assert chapter.current_version_id == latest.id
        assert latest.status == "approved"
        assert await db.scalar(select(func.count(ChapterVersion.id))) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_recheck_keeps_same_review_item_pending_without_duplicates(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        project = Project(title="复检失败仍可继续修订")
        db.add(project)
        await db.flush()
        session = WorkSession(
            project_id=project.id,
            title="自动写作",
            goal="完成章节",
            status="paused",
            quality_threshold=85,
        )
        chapter = Chapter(
            project_id=project.id,
            chapter_no=2,
            title="第二章",
            status="review",
            word_count=120,
        )
        db.add_all([session, chapter])
        await db.flush()
        original = ChapterVersion(
            chapter_id=chapter.id,
            version_no=1,
            content="旧正文" * 60,
            word_count=180,
            status="draft",
        )
        db.add(original)
        await db.flush()
        chapter.current_version_id = original.id
        db.add(
            ManuscriptBlock(
                chapter_id=chapter.id,
                version_id=original.id,
                block_no=1,
                content=original.content,
            )
        )
        item = ReviewQueueItem(
            project_id=project.id,
            session_id=session.id,
            item_type="quality_gate",
            artifact_type="chapter",
            artifact_id=chapter.id,
            title="待修订",
            status="pending",
            chapter_no=2,
        )
        db.add(item)
        await db.flush()

        async def critic_fail(self, block_texts, chapter_plan=None):
            return {
                "scores": {"logic": 82},
                "issues": [
                    {
                        "severity": "high",
                        "category": "logic",
                        "description": "因果链仍不完整",
                    }
                ],
                "overall_score": 82,
                "verdict": "revise",
            }

        async def continuity_fail(self, block_texts, chapter_no):
            return {
                "passed": False,
                "conflicts": [{"type": "timeline", "description": "时间线冲突"}],
                "warnings": [{"type": "foreshadow", "description": "伏笔推进偏弱"}],
            }

        monkeypatch.setattr(review_routes.Critic, "review_texts", critic_fail)
        monkeypatch.setattr(review_routes.ContinuityGuard, "check_texts", continuity_fail)

        first_payload = review_routes.DecisionRequest(
            revised_content=("第一次修订仍保留完整正文与细节。" * 20),
            decision_notes="继续精修",
        )
        first = await review_routes.revise_item(project.id, item.id, first_payload, db)

        assert first.id == str(item.id)
        assert first.status == "pending"
        assert item.status == "pending"
        assert "82/85" in (item.description or "")
        assert await db.scalar(select(func.count(ReviewQueueItem.id))) == 1
        assert await db.scalar(select(func.count(ChapterVersion.id))) == 2

        second_payload = review_routes.DecisionRequest(
            revised_content=("第二次修订继续补足因果、时间线与人物动机。" * 20),
            decision_notes="再次复检",
        )
        second = await review_routes.revise_item(project.id, item.id, second_payload, db)

        assert second.id == str(item.id)
        assert second.status == "pending"
        assert await db.scalar(select(func.count(ReviewQueueItem.id))) == 1
        assert await db.scalar(select(func.count(ChapterVersion.id))) == 3
        assert await db.scalar(select(func.count(HumanFeedbackEvent.id))) == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_manual_takeover_edit_is_optimistic_versioned_and_requeued(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def paused_status(_project_id):
        return {"desired_state": "paused"}

    monkeypatch.setattr(
        cockpit_routes.continuous_production_service,
        "get_status",
        paused_status,
    )
    async with factory() as db:
        project = Project(title="人工接管编辑")
        db.add(project)
        await db.flush()
        chapter = Chapter(project_id=project.id, chapter_no=2, title="第二章", status="review")
        db.add(chapter)
        await db.flush()
        original = ChapterVersion(
            chapter_id=chapter.id,
            version_no=1,
            content="原始正文",
            word_count=4,
            status="draft",
        )
        db.add(original)
        await db.flush()
        chapter.current_version_id = original.id
        db.add(
            ManuscriptBlock(
                chapter_id=chapter.id,
                version_id=original.id,
                block_no=1,
                content=original.content,
            )
        )
        await db.flush()

        result = await cockpit_routes.save_manuscript(
            project.id,
            chapter.id,
            cockpit_routes.ManuscriptRequest(
                content="人工修订第一段。\n\n人工修订第二段。",
                base_version_number=1,
                submit_for_review=True,
                notes="接管后精修",
            ),
            db,
        )

        assert result["version_number"] == 2
        assert result["submitted_for_review"] is True
        assert result["review_item_id"]
        assert chapter.status == "review"
        assert await db.scalar(select(func.count(ChapterVersion.id))) == 2
        assert await db.scalar(select(func.count(ManuscriptBlock.id))) == 2
        assert await db.scalar(select(func.count(HumanFeedbackEvent.id))) == 1
        pending = await db.scalar(
            select(ReviewQueueItem).where(ReviewQueueItem.status == "pending")
        )
        assert pending is not None
        assert pending.item_type == "manual_edit_review"

        with pytest.raises(HTTPException) as conflict:
            await cockpit_routes.save_manuscript(
                project.id,
                chapter.id,
                cockpit_routes.ManuscriptRequest(
                    content="基于旧版本的覆盖",
                    base_version_number=1,
                ),
                db,
            )
        assert conflict.value.status_code == 409
        assert conflict.value.detail["code"] == "chapter_version_conflict"

    await engine.dispose()
