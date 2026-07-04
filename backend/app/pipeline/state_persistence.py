"""章节间状态持久化。

负责：
1. 章节完成后保存状态（ChapterVersion / ManuscriptBlock / ChapterSummary）
2. 加载章节上下文（前章摘要、角色状态、伏笔状态等）
3. 创建审批队列条目
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chapter import Chapter, ChapterVersion, ManuscriptBlock
from app.db.models.character import Character
from app.db.models.plot import CurrentStoryState, PlotThread
from app.db.models.project import Project
from app.db.models.session import ReviewQueueItem
from app.db.models.summary import ChapterSummary, NarrativeSummary

logger = logging.getLogger("app.pipeline.state_persistence")


class StatePersistence:
    """章节间状态持久化管理器。

    使用方式::

        sp = StatePersistence(db, project_id)
        version = await sp.save_chapter_state(chapter_id, blocks, summary_data)
        context = await sp.load_context_for_chapter(chapter_no=5)
    """

    # 加载上下文时回看的前文章节数
    CONTEXT_LOOKBACK = 5

    def __init__(self, db: AsyncSession, project_id: uuid.UUID):
        self.db = db
        self.project_id = project_id

    # ------------------------------------------------------------------
    # 保存章节状态
    # ------------------------------------------------------------------
    async def save_chapter_state(
        self,
        chapter_id: uuid.UUID,
        blocks: list[dict[str, Any]],
        summary_data: dict[str, Any],
        created_by_agent: str = "pipeline",
    ) -> ChapterVersion:
        """章节完成后保存状态。

        Args:
            chapter_id: 章节 ID
            blocks: 正文分块列表，每个元素::

                {"content": "...", "block_type": "paragraph", "block_no": 0}
            summary_data: 摘要数据::

                {
                    "summary": "本章摘要文本",
                    "entities_involved": [...],
                    "facts_asserted": [...],
                    "facts_referenced": [...],
                    "word_count": 3000,
                }
            created_by_agent: 生成该版本的 Agent 名称

        Returns:
            创建的 ChapterVersion
        """
        # 1. 查找章节
        chapter = await self._get_chapter(chapter_id)

        # 2. 计算下一个版本号
        version_no = await self._next_version_no(chapter_id)

        # 3. 合并全文内容
        full_content = "\n\n".join(
            b.get("content", "") for b in blocks
        )
        word_count = summary_data.get("word_count", len(full_content))

        # 4. 创建 ChapterVersion
        version = ChapterVersion(
            chapter_id=chapter_id,
            version_no=version_no,
            content=full_content,
            word_count=word_count,
            status="draft",
            created_by_agent=created_by_agent,
        )
        self.db.add(version)
        await self.db.flush()

        # 5. 保存 ManuscriptBlock
        for block_data in blocks:
            block = ManuscriptBlock(
                chapter_id=chapter_id,
                version_id=version.id,
                block_no=block_data.get("block_no", 0),
                content=block_data.get("content", ""),
                block_type=block_data.get("block_type", "paragraph"),
            )
            self.db.add(block)

        # 6. 创建 ChapterSummary
        summary = ChapterSummary(
            project_id=self.project_id,
            chapter_id=chapter_id,
            chapter_no=chapter.chapter_no,
            summary=summary_data.get("summary", ""),
            word_count=word_count,
            entities_involved=summary_data.get("entities_involved", []),
            facts_asserted=summary_data.get("facts_asserted", []),
            facts_referenced=summary_data.get("facts_referenced", []),
        )
        self.db.add(summary)

        # 7. 更新 Chapter 状态
        chapter.status = "draft"
        chapter.word_count = word_count
        chapter.current_version_id = version.id

        # 8. 更新 Project.current_chapter_no
        await self._update_project_chapter_no(chapter.chapter_no)

        await self.db.flush()
        logger.info(
            "章节状态已保存: chapter_no=%d version_no=%d words=%d",
            chapter.chapter_no, version_no, word_count,
        )
        return version

    # ------------------------------------------------------------------
    # 加载章节上下文
    # ------------------------------------------------------------------
    async def load_context_for_chapter(
        self,
        chapter_no: int,
        lookback: Optional[int] = None,
    ) -> dict[str, Any]:
        """加载指定章节的上下文。

        返回前章摘要、角色状态、伏笔状态等，供 Agent 生成时参考。

        Args:
            chapter_no: 即将生成的章节号
            lookback: 回看的前文章节数（默认 CONTEXT_LOOKBACK）

        Returns:
            上下文字典::

                {
                    "target_chapter_no": int,
                    "previous_summaries": [...],   # 前 N 章摘要
                    "characters": [...],           # 活跃角色
                    "plot_threads": [...],         # 情节线状态
                    "story_state": {...},          # 最近的故事状态快照
                    "narrative_summary": str,      # 叙事摘要
                }
        """
        n = lookback or self.CONTEXT_LOOKBACK

        # 1. 前章摘要
        prev_summaries = await self._load_previous_summaries(chapter_no, n)

        # 2. 活跃角色
        characters = await self._load_characters()

        # 3. 情节线状态
        plot_threads = await self._load_plot_threads(chapter_no)

        # 4. 最近故事状态快照
        story_state = await self._load_story_state(chapter_no)

        # 5. 叙事摘要
        narrative_summary = await self._load_narrative_summary()

        context = {
            "target_chapter_no": chapter_no,
            "previous_summaries": prev_summaries,
            "characters": characters,
            "plot_threads": plot_threads,
            "story_state": story_state,
            "narrative_summary": narrative_summary,
        }
        logger.info(
            "已加载第 %d 章上下文: %d 章摘要, %d 角色, %d 情节线",
            chapter_no,
            len(prev_summaries),
            len(characters),
            len(plot_threads),
        )
        return context

    # ------------------------------------------------------------------
    # 创建审批队列条目
    # ------------------------------------------------------------------
    async def create_review_item(
        self,
        project_id: uuid.UUID,
        session_id: Optional[uuid.UUID],
        item_type: str,
        title: str,
        description: str = "",
        risk_level: str = "low",
        chapter_no: Optional[int] = None,
        artifact_type: Optional[str] = None,
        artifact_id: Optional[uuid.UUID] = None,
    ) -> ReviewQueueItem:
        """创建审批队列条目。"""
        item = ReviewQueueItem(
            project_id=project_id,
            session_id=session_id,
            item_type=item_type,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            title=title,
            description=description,
            risk_level=risk_level,
            status="pending",
            chapter_no=chapter_no,
        )
        self.db.add(item)
        await self.db.flush()
        logger.info("创建审批条目: type=%s title='%s' risk=%s", item_type, title, risk_level)
        return item

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    async def _get_chapter(self, chapter_id: uuid.UUID) -> Chapter:
        stmt = select(Chapter).where(
            Chapter.id == chapter_id,
            Chapter.project_id == self.project_id,
        )
        result = await self.db.execute(stmt)
        chapter = result.scalar_one_or_none()
        if chapter is None:
            raise ValueError(f"章节 {chapter_id} 不存在")
        return chapter

    async def _next_version_no(self, chapter_id: uuid.UUID) -> int:
        stmt = (
            select(ChapterVersion.version_no)
            .where(ChapterVersion.chapter_id == chapter_id)
            .order_by(ChapterVersion.version_no.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        current = result.scalar_one_or_none()
        return (current or 0) + 1

    async def _update_project_chapter_no(self, chapter_no: int) -> None:
        stmt = select(Project).where(Project.id == self.project_id)
        result = await self.db.execute(stmt)
        project = result.scalar_one_or_none()
        if project and chapter_no > project.current_chapter_no:
            project.current_chapter_no = chapter_no

    async def _load_previous_summaries(
        self,
        chapter_no: int,
        lookback: int,
    ) -> list[dict[str, Any]]:
        """加载前 N 章的摘要。"""
        start_no = max(1, chapter_no - lookback)
        stmt = (
            select(ChapterSummary)
            .where(
                ChapterSummary.project_id == self.project_id,
                ChapterSummary.chapter_no >= start_no,
                ChapterSummary.chapter_no < chapter_no,
            )
            .order_by(ChapterSummary.chapter_no.asc())
        )
        result = await self.db.execute(stmt)
        return [
            {
                "chapter_no": s.chapter_no,
                "summary": s.summary,
                "entities_involved": s.entities_involved,
                "facts_asserted": s.facts_asserted,
                "facts_referenced": s.facts_referenced,
            }
            for s in result.scalars().all()
        ]

    async def _load_characters(self) -> list[dict[str, Any]]:
        """加载活跃角色列表。"""
        stmt = select(Character).where(
            Character.project_id == self.project_id,
            Character.status == "active",
        )
        result = await self.db.execute(stmt)
        return [
            {
                "id": str(c.id),
                "name": c.name,
                "role": c.role,
                "description": c.description,
                "attributes": c.attributes,
            }
            for c in result.scalars().all()
        ]

    async def _load_plot_threads(self, chapter_no: int) -> list[dict[str, Any]]:
        """加载活跃情节线。"""
        stmt = select(PlotThread).where(
            PlotThread.project_id == self.project_id,
            PlotThread.status.in_(["planned", "active"]),
        )
        result = await self.db.execute(stmt)
        return [
            {
                "id": str(t.id),
                "name": t.name,
                "type": t.type,
                "status": t.status,
                "description": t.description,
                "introduced_chapter": t.introduced_chapter,
                "importance": t.importance,
            }
            for t in result.scalars().all()
        ]

    async def _load_story_state(self, chapter_no: int) -> dict[str, Any]:
        """加载最近的故事状态快照。"""
        stmt = (
            select(CurrentStoryState)
            .where(
                CurrentStoryState.project_id == self.project_id,
                CurrentStoryState.chapter_no < chapter_no,
            )
            .order_by(CurrentStoryState.chapter_no.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        state = result.scalar_one_or_none()
        if state is None:
            return {}
        return {
            "chapter_no": state.chapter_no,
            "state": state.state,
            "agent_states": state.agent_states,
            "summary": state.summary,
        }

    async def _load_narrative_summary(self) -> str:
        """加载最近的叙事摘要。"""
        stmt = (
            select(NarrativeSummary)
            .where(NarrativeSummary.project_id == self.project_id)
            .order_by(NarrativeSummary.scope_start.desc().nulls_last())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        ns = result.scalar_one_or_none()
        return ns.summary if ns else ""
