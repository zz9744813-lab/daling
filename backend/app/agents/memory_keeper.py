"""MemoryKeeper Agent — 状态更新。

职责：章节完成后更新故事状态、角色状态、情节线进度，
生成章节摘要与叙事摘要（每 5 章汇总）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.context.canon_manager import CanonManager
from app.db.models.chapter import Chapter, ManuscriptBlock
from app.db.models.character import Character
from app.db.models.plot import CurrentStoryState, PlotThread
from app.db.models.summary import ChapterSummary, NarrativeSummary
from app.domain.errors import AgentExecutionError, EmptyResultError
from app.prompts.templates.summary import SUMMARY_SYSTEM, SUMMARY_USER

logger = logging.getLogger("app.agents.memory_keeper")


class MemoryKeeper(BaseAgent):
    """记忆管理者 Agent，负责章节后的状态更新与摘要生成。"""

    agent_name = "MemoryKeeper"

    async def update_state(
        self,
        chapter_id: uuid.UUID,
        blocks: list[ManuscriptBlock] | list[dict[str, Any]],
    ) -> dict[str, Any]:
        """章节完成后更新所有状态。

        1. 生成 ChapterSummary（含 entities_involved, facts_asserted）
        2. 更新 CurrentStoryState
        3. 更新 Character 状态
        4. 更新 PlotThread 进度
        5. 每 5 章生成 NarrativeSummary

        Args:
            chapter_id: 章节 ID。
            blocks: 章节 ManuscriptBlock 列表或内容快照 dict 列表。

        Returns:
            更新结果摘要 dict。
        """
        # 加载章节信息
        chapter = await self.db.get(Chapter, chapter_id)
        if not chapter:
            return {"error": "章节不存在"}

        prepared = await self.prepare_state_update(chapter.chapter_no, blocks)
        return await self.apply_prepared_state(chapter_id, prepared)

    async def prepare_state_update(
        self,
        chapter_no: int,
        blocks: list[ManuscriptBlock] | list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate and validate the memory payload without mutating Canon.

        This is deliberately safe to run while the continuous supervisor keeps
        its orchestration session read-only.  The returned payload is applied
        together with the selected manuscript version in one later transaction.
        """
        if blocks and isinstance(blocks[0], dict):
            manuscript_text = "\n\n".join(
                b.get("content", "") for b in blocks if b.get("content")
            )
        else:
            manuscript_text = "\n\n".join(b.content for b in blocks if b.content)
        word_count = len(manuscript_text.replace("\n", "").replace(" ", ""))

        characters_info = await self._get_characters_info()
        known_facts = await self._get_known_facts()

        user_prompt = SUMMARY_USER.format(
            chapter_no=chapter_no,
            manuscript_text=manuscript_text,
            characters_info=characters_info,
            known_facts=known_facts,
        )

        try:
            summary_result = await self._llm_json(
                system_prompt=SUMMARY_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.3,
            )
        except Exception as exc:
            logger.error("项目 %s MemoryKeeper LLM 调用失败: %s", self.project_id, exc)
            raise AgentExecutionError(
                "MemoryKeeper LLM 调用失败，不使用空数据更新状态",
                agent_name="MemoryKeeper",
                project_id=str(self.project_id),
                chapter_no=chapter_no,
                cause=exc,
            ) from exc

        # 校验：摘要为空时不应继续
        if not summary_result.get("summary", "").strip():
            raise EmptyResultError(
                "MemoryKeeper 返回空摘要，不使用空数据更新状态",
                agent_name="MemoryKeeper",
                project_id=str(self.project_id),
                chapter_no=chapter_no,
            )

        return {
            "chapter_no": chapter_no,
            "manuscript_text": manuscript_text,
            "word_count": word_count,
            "summary_result": summary_result,
        }

    async def apply_prepared_state(
        self,
        chapter_id: uuid.UUID,
        prepared: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply a validated memory payload inside the caller's transaction."""
        chapter = await self.db.get(Chapter, chapter_id)
        if not chapter:
            return {"error": "章节不存在"}
        chapter_no = int(prepared["chapter_no"])
        manuscript_text = str(prepared["manuscript_text"])
        word_count = int(prepared["word_count"])
        summary_result = dict(prepared["summary_result"])
        chapter.word_count = word_count

        # 2. 幂等保存 ChapterSummary。恢复任务重放同一 approved version 时
        # 更新同一条记录，而不是让摘要、事实与角色变化成倍累积。
        summary_stmt = select(ChapterSummary).where(
            ChapterSummary.project_id == self.project_id,
            ChapterSummary.chapter_no == chapter_no,
        )
        summary_query = await self.db.execute(summary_stmt)
        chapter_summary = summary_query.scalar_one_or_none()
        if chapter_summary is None:
            chapter_summary = ChapterSummary(
                project_id=self.project_id,
                chapter_id=chapter_id,
                chapter_no=chapter_no,
            )
            self.db.add(chapter_summary)
        chapter_summary.chapter_id = chapter_id
        chapter_summary.summary = summary_result.get("summary", "")
        chapter_summary.word_count = word_count
        chapter_summary.entities_involved = summary_result.get("entities_involved", [])
        chapter_summary.facts_asserted = summary_result.get("facts_asserted", [])
        chapter_summary.facts_referenced = summary_result.get("facts_referenced", [])

        # 结构化事实同步进入 Canon；immutable 冲突会抛错并回滚整章，
        # 不允许一边批准正文、一边留下矛盾设定。
        canon_facts = await self._update_canon_facts(
            summary_result.get("facts_asserted", []),
            chapter_no,
        )

        # 3. 更新 CurrentStoryState
        await self._update_story_state(chapter_no, summary_result, manuscript_text)

        # 4. 更新角色状态
        character_updates = summary_result.get("character_updates", [])
        await self._update_characters(character_updates, chapter_no)

        # 5. 更新情节线进度
        plot_progress = summary_result.get("plot_progress", [])
        await self._update_plot_threads(plot_progress, chapter_no)

        await self.db.flush()

        # 6. 每 5 章生成 NarrativeSummary
        narrative_created = False
        if chapter_no > 0 and chapter_no % 5 == 0:
            narrative_created = await self._create_narrative_summary(chapter_no)

        logger.info(
            "项目 %s 第 %d 章状态已更新: 摘要=%d字, 角色=%d, 情节线=%d%s",
            self.project_id,
            chapter_no,
            len(summary_result.get("summary", "")),
            len(character_updates),
            len(plot_progress),
            "（含叙事摘要）" if narrative_created else "",
        )

        return {
            "chapter_no": chapter_no,
            "summary_length": len(summary_result.get("summary", "")),
            "entities_involved": len(summary_result.get("entities_involved", [])),
            "facts_asserted": len(summary_result.get("facts_asserted", [])),
            "canon_facts_updated": canon_facts,
            "character_updates": len(character_updates),
            "plot_progress": len(plot_progress),
            "narrative_summary_created": narrative_created,
        }

    # ------------------------------------------------------------------
    # 状态更新
    # ------------------------------------------------------------------
    async def _update_story_state(
        self,
        chapter_no: int,
        summary_result: dict[str, Any],
        manuscript_text: str,
    ) -> None:
        """幂等创建或更新 CurrentStoryState 记录。"""
        stmt = select(CurrentStoryState).where(
            CurrentStoryState.project_id == self.project_id,
            CurrentStoryState.chapter_no == chapter_no,
        )
        result = await self.db.execute(stmt)
        state = result.scalar_one_or_none()
        if state is None:
            state = CurrentStoryState(project_id=self.project_id, chapter_no=chapter_no)
            self.db.add(state)
        state.state = {
            "entities": summary_result.get("entities_involved", []),
            "facts_asserted": summary_result.get("facts_asserted", []),
            "character_updates": summary_result.get("character_updates", []),
        }
        state.agent_states = {
            "memory_keeper": {
                "chapter_no": chapter_no,
                "plot_progress": summary_result.get("plot_progress", []),
            }
        }
        state.summary = summary_result.get("summary", "")

    async def _update_canon_facts(
        self,
        facts: list[Any],
        chapter_no: int,
    ) -> int:
        manager = CanonManager(self.db, self.project_id)
        updated = 0
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            predicate = str(fact.get("predicate", "")).strip()
            object_value = str(
                fact.get("object_value", fact.get("object", fact.get("value", "")))
            ).strip()
            if not predicate or not object_value:
                continue
            subject_id = fact.get("subject_id", fact.get("subject"))
            await manager.assert_fact(
                fact_type=str(fact.get("fact_type", "setting"))[:40],
                subject_type=str(fact.get("subject_type", "设定"))[:40],
                subject_id=str(subject_id)[:100] if subject_id else None,
                predicate=predicate[:100],
                object_value=object_value,
                mutability=(
                    fact.get("mutability")
                    if fact.get("mutability") in {"immutable", "soft", "dynamic"}
                    else "soft"
                ),
                confidence=float(fact.get("confidence", 0.9)),
                source_chapter_no=chapter_no,
                tags=["memory_keeper", f"chapter:{chapter_no}"],
            )
            updated += 1
        return updated

    async def _update_characters(
        self,
        character_updates: list[dict[str, Any]],
        chapter_no: int,
    ) -> None:
        """更新角色状态。"""
        if not character_updates:
            return
        for update in character_updates:
            name = update.get("name")
            if not name:
                continue
            stmt = select(Character).where(
                Character.project_id == self.project_id,
                Character.name == name,
            )
            result = await self.db.execute(stmt)
            character = result.scalar_one_or_none()
            if character:
                # 更新角色属性中的状态变更记录
                attrs = dict(character.attributes) if character.attributes else {}
                changes = attrs.get("recent_changes", [])
                changes = [item for item in changes if item.get("chapter_no") != chapter_no]
                changes.append(
                    {
                        "chapter_no": chapter_no,
                        "changes": update.get("changes", ""),
                        "new_status": update.get("new_status", ""),
                    }
                )
                # 只保留最近 10 条
                attrs["recent_changes"] = changes[-10:]
                attrs["current_status"] = update.get("new_status", attrs.get("current_status", ""))
                character.attributes = attrs
                # 如果角色首次出场，更新 first_appearance_chapter
                if character.first_appearance_chapter is None:
                    character.first_appearance_chapter = chapter_no

    async def _update_plot_threads(
        self,
        plot_progress: list[dict[str, Any]],
        chapter_no: int,
    ) -> None:
        """更新情节线进度。"""
        if not plot_progress:
            return
        for progress in plot_progress:
            thread_name = progress.get("thread_name")
            if not thread_name:
                continue
            stmt = select(PlotThread).where(
                PlotThread.project_id == self.project_id,
                PlotThread.name == thread_name,
            )
            result = await self.db.execute(stmt)
            thread = result.scalar_one_or_none()
            if thread:
                new_status = progress.get("new_status", "")
                if new_status == "resolved":
                    thread.status = "resolved"
                    thread.resolved_chapter = chapter_no
                elif new_status in ("active", "advanced"):
                    if thread.status == "planned":
                        thread.status = "active"
                    if thread.introduced_chapter is None:
                        thread.introduced_chapter = chapter_no
                # 记录进度到 meta
                meta = dict(thread.meta) if thread.meta else {}
                progress_log = meta.get("progress_log", [])
                progress_log = [
                    item for item in progress_log if item.get("chapter_no") != chapter_no
                ]
                progress_log.append(
                    {
                        "chapter_no": chapter_no,
                        "progress": progress.get("progress", ""),
                    }
                )
                meta["progress_log"] = progress_log[-20:]  # 保留最近 20 条
                thread.meta = meta

    async def _create_narrative_summary(self, chapter_no: int) -> bool:
        """每 5 章生成叙事摘要。

        汇总最近 5 章的 ChapterSummary，生成跨章节叙事摘要。
        """
        start_no = chapter_no - 4
        stmt = (
            select(ChapterSummary)
            .where(
                ChapterSummary.project_id == self.project_id,
                ChapterSummary.chapter_no >= start_no,
                ChapterSummary.chapter_no <= chapter_no,
            )
            .order_by(ChapterSummary.chapter_no)
        )
        result = await self.db.execute(stmt)
        summaries = result.scalars().all()
        if not summaries:
            return False

        # 合并章节摘要
        combined = "\n\n".join(f"第{s.chapter_no}章：{s.summary}" for s in summaries)

        # 检查是否已存在该范围的 NarrativeSummary
        exist_stmt = select(NarrativeSummary).where(
            NarrativeSummary.project_id == self.project_id,
            NarrativeSummary.scope == "chapter_range",
            NarrativeSummary.scope_start == start_no,
            NarrativeSummary.scope_end == chapter_no,
        )
        exist_result = await self.db.execute(exist_stmt)
        if exist_result.scalar_one_or_none():
            return False

        narrative = NarrativeSummary(
            project_id=self.project_id,
            scope="chapter_range",
            scope_start=start_no,
            scope_end=chapter_no,
            summary=combined,
        )
        self.db.add(narrative)
        return True

    # ------------------------------------------------------------------
    # 辅助查询
    # ------------------------------------------------------------------
    async def _get_characters_info(self) -> str:
        stmt = select(Character).where(
            Character.project_id == self.project_id,
        )
        result = await self.db.execute(stmt)
        characters = result.scalars().all()
        if not characters:
            return "（暂无角色信息）"
        parts = []
        for c in characters:
            parts.append(f"- {c.name}（{c.role}）：{c.description or '无描述'}")
        return "\n".join(parts)

    async def _get_known_facts(self) -> str:
        """获取最近的设定事实（从 ChapterSummary 的 facts_asserted 中提取）。"""
        stmt = (
            select(ChapterSummary)
            .where(ChapterSummary.project_id == self.project_id)
            .order_by(ChapterSummary.chapter_no.desc())
            .limit(5)
        )
        result = await self.db.execute(stmt)
        summaries = result.scalars().all()
        if not summaries:
            return "（暂无已知事实）"
        parts = []
        for s in summaries:
            for fact in s.facts_asserted:
                if isinstance(fact, dict):
                    parts.append(
                        f"- 第{s.chapter_no}章: "
                        f"{fact.get('subject', '?')} {fact.get('predicate', '?')} "
                        f"= {fact.get('object', '?')}"
                    )
                elif isinstance(fact, str):
                    parts.append(f"- 第{s.chapter_no}章: {fact}")
        return "\n".join(parts) if parts else "（暂无已知事实）"
