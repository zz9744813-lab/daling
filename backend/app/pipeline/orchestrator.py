"""PipelineOrchestrator — 单章/多章生成流程编排器。

编排单章生成的完整 Pipeline：
1. ChapterPlanner.plan_chapter()   — 生成写作计划
2. Drafter.draft_chapter()         — 起草正文
3. Critic.review()                 — 质量审查
4. ContinuityGuard.check()         — 一致性校验
5. if issues: Rewriter.rewrite() → goto 3（最多重试 2 次）
6. ChiefEditor.finalize()          — 最终审定
7. MemoryKeeper.update_state()     — 状态更新
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chapter_planner import ChapterPlanner
from app.agents.chief_editor import ChiefEditor
from app.agents.continuity_guard import ContinuityGuard
from app.agents.critic import Critic
from app.agents.drafter import Drafter
from app.agents.memory_keeper import MemoryKeeper
from app.agents.rewriter import Rewriter
from app.agents.story_architect import StoryArchitect
from app.db.models.chapter import Chapter, ManuscriptBlock
from app.db.models.session import WorkSession
from app.db.models.storyline import StorylineBeat
from app.db.models.world import WorldBible
from app.model_gateway import Gateway

logger = logging.getLogger("app.pipeline.orchestrator")

# 单章重写最大次数
MAX_REWRITE_ROUNDS = 2


class PipelineOrchestrator:
    """Pipeline 编排器，协调各 Agent 完成章节生成。"""

    def __init__(
        self,
        gateway: Gateway,
        db: AsyncSession,
        project_id: uuid.UUID,
        session_id: Optional[uuid.UUID] = None,
    ) -> None:
        """初始化编排器。

        Args:
            gateway: LLM Gateway 实例。
            db: 异步数据库会话。
            project_id: 项目 ID。
            session_id: 关联的 WorkSession ID。
        """
        self.gateway = gateway
        self.db = db
        self.project_id = project_id
        self.session_id = session_id

    # ------------------------------------------------------------------
    # Phase: 生成世界观
    # ------------------------------------------------------------------
    async def generate_bible(self, hints: dict[str, Any]) -> dict[str, Any]:
        """生成世界观圣经。

        Args:
            hints: 创作提示字典。

        Returns:
            结果 dict，包含 world_bible 信息。
        """
        agent = StoryArchitect(
            gateway=self.gateway,
            db=self.db,
            project_id=self.project_id,
            session_id=self.session_id,
        )
        world_bible = await agent.generate_world_bible(hints)
        return {
            "job": "generate_bible",
            "status": "completed",
            "world_bible_id": str(world_bible.id),
            "world_name": world_bible.content.get("world_name", ""),
            "summary": world_bible.summary or "",
        }

    # ------------------------------------------------------------------
    # Phase: 生成大纲
    # ------------------------------------------------------------------
    async def generate_outline(
        self,
        volume_count: int = 1,
        chapters_per_volume: int = 10,
        hints: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """生成故事大纲。

        Args:
            volume_count: 卷数（如未指定，则从项目配置自动推算）。
            chapters_per_volume: 每卷章节数（如未指定，则自动推算）。
            hints: 额外提示。

        Returns:
            结果 dict，包含大纲信息。
        """
        agent = StoryArchitect(
            gateway=self.gateway,
            db=self.db,
            project_id=self.project_id,
            session_id=self.session_id,
        )
        world_bible = await agent.get_latest_world_bible()
        if not world_bible:
            return {
                "job": "generate_outline",
                "status": "failed",
                "error": "请先生成世界观圣经",
            }

        # 从项目 extra 读取篇幅配置，自动推算卷数与每卷章数
        from app.db.models.project import Project

        proj_stmt = select(Project).where(Project.id == self.project_id)
        proj_result = await self.db.execute(proj_stmt)
        project = proj_result.scalar_one_or_none()
        extra = (project.extra or {}) if project else {}

        chapter_range = extra.get("chapter_range")
        length_label = extra.get("length_label", "")

        if chapter_range:
            rng_min = chapter_range.get("min", 10)
            rng_max = chapter_range.get("max", 50)
            target_total = (rng_min + rng_max) // 2
            # 每卷 30-50 章较合理，据此推算卷数
            if target_total <= 30:
                volume_count = 1
                chapters_per_volume = max(target_total, 5)
            elif target_total <= 100:
                volume_count = max(target_total // 30, 1)
                chapters_per_volume = target_total // volume_count
            else:
                chapters_per_volume = 40  # 每卷约 40 章
                volume_count = max(target_total // chapters_per_volume, 1)
            logger.info(
                "项目 %s 篇幅=%s, 目标 %d 章 → %d 卷 × %d 章/卷",
                self.project_id, length_label, target_total,
                volume_count, chapters_per_volume,
            )

        # 将篇幅信息传给 StoryArchitect
        if hints is None:
            hints = {}
        if chapter_range:
            hints["chapter_range"] = chapter_range
            hints["length_label"] = length_label
            hints["target_total_chapters"] = (chapter_range.get("min", 10) + chapter_range.get("max", 50)) // 2

        # 如果用户上传了详细大纲，直接传给 StoryArchitect 解析
        outline_text = extra.get("outline_text")
        if outline_text and outline_text.strip():
            hints["outline_text"] = outline_text
            logger.info(
                "项目 %s 使用上传的大纲生成章节结构 (%d 字符)",
                self.project_id, len(outline_text),
            )

        volumes = await agent.generate_outline(
            world_bible=world_bible,
            volume_count=volume_count,
            chapters_per_volume=chapters_per_volume,
            hints=hints,
        )

        return {
            "job": "generate_outline",
            "status": "completed",
            "volume_count": len(volumes),
            "total_chapters": sum(v.target_chapters for v in volumes),
            "volumes": [
                {
                    "volume_no": v.volume_no,
                    "title": v.title,
                    "summary": v.summary or "",
                    "target_chapters": v.target_chapters,
                }
                for v in volumes
            ],
        }

    # ------------------------------------------------------------------
    # 单章生成 Pipeline
    # ------------------------------------------------------------------
    async def run_chapter(self, chapter_no: int, mode: str = "L2") -> dict[str, Any]:
        """执行单章生成 Pipeline。

        流程：计划 → 起草 → 审查 → 一致性 → (重写) → 审定 → 状态更新

        Args:
            chapter_no: 章节编号。
            mode: 自治等级（L0/L1/L2）。

        Returns:
            结果 dict，包含 chapter_no, status, score, blocks, issues。
        """
        logger.info(
            "项目 %s 开始生成第 %d 章 (mode=%s)",
            self.project_id, chapter_no, mode,
        )

        try:
            # 获取或创建 Chapter
            chapter = await self._get_or_create_chapter(chapter_no)
            chapter.status = "generating"
            await self.db.flush()

            # 1. ChapterPlanner — 生成写作计划
            planner = ChapterPlanner(
                gateway=self.gateway, db=self.db,
                project_id=self.project_id, session_id=self.session_id,
            )
            plan = await planner.plan_chapter(chapter_no)

            # 2. Drafter — 起草正文
            drafter = Drafter(
                gateway=self.gateway, db=self.db,
                project_id=self.project_id, session_id=self.session_id,
            )
            blocks = await drafter.draft_chapter(plan, chapter_id=chapter.id)

            # 持久化初始 blocks，并创建内容快照
            for block in blocks:
                self.db.add(block)
            await self.db.flush()

            # 创建 blocks 的内容快照（避免 ORM session 状态问题）
            def _snapshot(blks):
                """从 ORM blocks 创建简单内容列表。"""
                return [
                    {"content": b.content, "block_type": b.block_type, "block_no": b.block_no}
                    for b in blks
                ]

            block_texts = _snapshot(blocks)

            # 3-5. Critic → (Rewriter → Critic) 循环
            critic = Critic(
                gateway=self.gateway, db=self.db,
                project_id=self.project_id, session_id=self.session_id,
            )
            rewriter = Rewriter(
                gateway=self.gateway, db=self.db,
                project_id=self.project_id, session_id=self.session_id,
            )

            critic_result: dict[str, Any] = {}
            continuity_result: dict[str, Any] = {}
            all_issues: list[dict[str, Any]] = []

            for round_no in range(MAX_REWRITE_ROUNDS + 1):
                # 3. Critic — 质量审查（传内容快照，避免 ORM 状态问题）
                critic_result = await critic.review_texts(block_texts, chapter_plan=plan)
                all_issues.extend(critic_result.get("issues", []))

                verdict = critic_result.get("verdict", "revise")
                score = critic_result.get("overall_score", 0)

                # 4. ContinuityGuard — 一致性校验
                guard = ContinuityGuard(
                    gateway=self.gateway, db=self.db,
                    project_id=self.project_id, session_id=self.session_id,
                )
                continuity_result = await guard.check_texts(block_texts, chapter_no)

                # 判断是否需要重写
                needs_rewrite = (
                    verdict == "rewrite"
                    or (verdict == "revise" and round_no < MAX_REWRITE_ROUNDS)
                    or not continuity_result.get("passed", True)
                )

                if not needs_rewrite:
                    break

                if round_no >= MAX_REWRITE_ROUNDS:
                    logger.warning(
                        "项目 %s 第 %d 章已达最大重写次数 %d，继续审定",
                        self.project_id, chapter_no, MAX_REWRITE_ROUNDS,
                    )
                    break

                # 5. Rewriter — 重写
                logger.info(
                    "项目 %s 第 %d 章第 %d 轮重写 (score=%d, verdict=%s)",
                    self.project_id, chapter_no, round_no + 1, score, verdict,
                )

                # 传内容快照给 Rewriter（而非 ORM 引用）
                blocks = await rewriter.rewrite_texts(
                    block_texts=block_texts,
                    issues=critic_result.get("issues", []),
                    plan=plan,
                    chapter_id=chapter.id,
                )
                # 删除旧 blocks（在 Rewriter 创建新 blocks 之后）
                await self._delete_blocks(chapter.id)
                for block in blocks:
                    self.db.add(block)
                await self.db.flush()
                block_texts = _snapshot(blocks)

            # 6. ChiefEditor — 最终审定
            editor = ChiefEditor(
                gateway=self.gateway, db=self.db,
                project_id=self.project_id, session_id=self.session_id,
            )
            quality_threshold = 80 if mode == "L2" else 70
            finalize_result = await editor.finalize(
                chapter_id=chapter.id,
                critic_result=critic_result,
                continuity_result=continuity_result,
                quality_threshold=quality_threshold,
            )

            # 7. MemoryKeeper — 状态更新
            keeper = MemoryKeeper(
                gateway=self.gateway, db=self.db,
                project_id=self.project_id, session_id=self.session_id,
            )
            memory_result = await keeper.update_state(chapter.id, blocks)

            final_score = finalize_result.get("final_score", 0)
            approved = finalize_result.get("approved", False)

            logger.info(
                "项目 %s 第 %d 章生成完成: approved=%s, score=%d",
                self.project_id, chapter_no, approved, final_score,
            )

            return {
                "chapter_no": chapter_no,
                "chapter_id": str(chapter.id),
                "status": "approved" if approved else "review",
                "score": final_score,
                "verdict": critic_result.get("verdict", ""),
                "block_count": len(blocks),
                "word_count": finalize_result.get("word_count", 0),
                "issues_count": len(all_issues),
                "continuity_passed": continuity_result.get("passed", True),
                "version_no": finalize_result.get("version_no", 1),
                "notes": finalize_result.get("notes", ""),
            }

        except Exception as exc:
            logger.exception(
                "项目 %s 第 %d 章生成失败: %s",
                self.project_id, chapter_no, exc,
            )
            # 更新章节状态为失败
            try:
                chapter = await self._get_or_create_chapter(chapter_no)
                chapter.status = "draft"
                await self.db.flush()
            except Exception:
                pass

            return {
                "chapter_no": chapter_no,
                "status": "failed",
                "error": str(exc),
                "score": 0,
            }

    # ------------------------------------------------------------------
    # 多章连续生成
    # ------------------------------------------------------------------
    async def run_pipeline(
        self,
        target_chapters: int,
        mode: str = "L2",
        start_chapter: Optional[int] = None,
    ) -> dict[str, Any]:
        """多章连续生成。

        Args:
            target_chapters: 目标生成章节数。
            mode: 自治等级。
            start_chapter: 起始章节号（为 None 则从当前进度继续）。

        Returns:
            汇总结果 dict。
        """
        # 确定起始章节
        if start_chapter is None:
            start_chapter = await self._get_next_chapter_no()

        results: list[dict[str, Any]] = []
        success_count = 0
        failed_count = 0

        for i in range(target_chapters):
            chapter_no = start_chapter + i
            result = await self.run_chapter(chapter_no, mode=mode)
            results.append(result)

            if result.get("status") == "failed":
                failed_count += 1
                break  # 失败则停止
            else:
                success_count += 1

        # 更新 WorkSession 进度
        await self._update_session_progress(success_count, target_chapters)

        return {
            "job": "run_pipeline",
            "status": "completed" if failed_count == 0 else "partial",
            "mode": mode,
            "start_chapter": start_chapter,
            "target_chapters": target_chapters,
            "success_count": success_count,
            "failed_count": failed_count,
            "chapters": results,
        }

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    async def _get_or_create_chapter(self, chapter_no: int) -> Chapter:
        """获取或创建章节记录。"""
        stmt = select(Chapter).where(
            Chapter.project_id == self.project_id,
            Chapter.chapter_no == chapter_no,
        ).order_by(Chapter.created_at.desc()).limit(1)
        result = await self.db.execute(stmt)
        chapter = result.scalar_one_or_none()
        if not chapter:
            chapter = Chapter(
                project_id=self.project_id,
                chapter_no=chapter_no,
                title=f"第{chapter_no}章",
                status="draft",
                word_count=0,
                target_words=3000,
            )
            self.db.add(chapter)
            await self.db.flush()
        return chapter

    async def _delete_blocks(self, chapter_id: uuid.UUID) -> None:
        """删除章节的所有 ManuscriptBlock。"""
        stmt = select(ManuscriptBlock).where(
            ManuscriptBlock.chapter_id == chapter_id,
        )
        result = await self.db.execute(stmt)
        blocks = result.scalars().all()
        for block in blocks:
            await self.db.delete(block)
        await self.db.flush()

    async def _get_next_chapter_no(self) -> int:
        """获取下一章编号。

        优先返回第一个 word_count=0 的章节（未生成的已有章节），
        如果所有已有章节都已生成，则返回最大章节号 + 1。
        """
        # 查找第一个未生成的章节（word_count=0 或 status='draft'）
        stmt = (
            select(Chapter.chapter_no)
            .where(Chapter.project_id == self.project_id)
            .where(Chapter.word_count == 0)
            .order_by(Chapter.chapter_no.asc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        first_unfinished = result.scalar_one_or_none()
        if first_unfinished is not None:
            return first_unfinished

        # 所有已有章节都已完成，返回下一章
        stmt = (
            select(Chapter.chapter_no)
            .where(Chapter.project_id == self.project_id)
            .order_by(Chapter.chapter_no.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        max_no = result.scalar_one_or_none()
        return (max_no or 0) + 1

    async def _update_session_progress(
        self, completed: int, total: int
    ) -> None:
        """更新 WorkSession 进度。"""
        if not self.session_id:
            return
        session = await self.db.get(WorkSession, self.session_id)
        if session:
            session.progress_percent = (completed / total * 100) if total > 0 else 0
            if completed >= total:
                session.status = "completed"
            elif completed > 0:
                session.status = "running"
            await self.db.flush()
