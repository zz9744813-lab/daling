"""ChapterPlanner Agent — 章节写作计划生成。

职责：为单章生成详细的场景级写作计划。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.models.character import Character
from app.db.models.chapter import Chapter
from app.db.models.plot import CurrentStoryState, PlotThread
from app.db.models.storyline import StorylineBeat
from app.db.models.summary import ChapterSummary
from app.prompts.templates.chapter_plan import CHAPTER_PLAN_SYSTEM, CHAPTER_PLAN_USER

logger = logging.getLogger("app.agents.chapter_planner")


class ChapterPlanner(BaseAgent):
    """章节规划师 Agent，负责为单章生成详细写作计划。"""

    agent_name = "ChapterPlanner"

    async def plan_chapter(self, chapter_no: int) -> dict[str, Any]:
        """为指定章节生成详细写作计划。

        读取本章节拍、前章摘要、角色状态与伏笔，调用 LLM 生成场景计划。

        Args:
            chapter_no: 章节编号。

        Returns:
            写作计划 dict，包含 chapter_no, chapter_title, scene_list 等。
        """
        # 1. 读取本章节拍
        beat_info = await self._get_beat_info(chapter_no)

        # 2. 读取前章摘要（最近 5 章）
        previous_summaries = await self._get_previous_summaries(chapter_no)

        # 3. 读取角色状态
        character_states = await self._get_character_states()

        # 4. 读取当前故事状态
        story_state = await self._get_story_state(chapter_no)

        # 5. 读取活跃伏笔
        foreshadows = await self._get_foreshadows()

        # 6. 调用 LLM 生成计划
        user_prompt = CHAPTER_PLAN_USER.format(
            chapter_no=chapter_no,
            beat_info=beat_info,
            previous_summaries=previous_summaries,
            character_states=character_states,
            story_state=story_state,
            foreshadows=foreshadows,
        )

        try:
            plan = await self._llm_json(
                system_prompt=CHAPTER_PLAN_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.6,
            )
        except Exception as exc:
            logger.warning("项目 %s ChapterPlanner LLM 失败，使用默认计划: %s", self.project_id, exc)
            plan = {}

        # 确保关键字段存在
        plan.setdefault("chapter_no", chapter_no)
        plan.setdefault("chapter_title", f"第{chapter_no}章")
        plan.setdefault("scene_list", [])
        plan.setdefault("overall_goal", "")
        plan.setdefault("ending_hook", "")

        logger.info(
            "项目 %s 第 %d 章写作计划已生成: %d 个场景",
            self.project_id, chapter_no, len(plan.get("scene_list", [])),
        )
        return plan

    # ------------------------------------------------------------------
    # 辅助查询
    # ------------------------------------------------------------------
    async def _get_beat_info(self, chapter_no: int) -> str:
        """获取本章节拍信息。"""
        stmt = select(StorylineBeat).where(
            StorylineBeat.project_id == self.project_id,
            StorylineBeat.chapter_no == chapter_no,
        )
        result = await self.db.execute(stmt)
        beat = result.scalar_one_or_none()
        if not beat:
            return f"第{chapter_no}章（无对应节拍信息）"
        return (
            f"标题：{beat.title}\n"
            f"描述：{beat.description or '无'}\n"
            f"重要性：{beat.importance}\n"
            f"关联情节线：{json.dumps(beat.plot_threads, ensure_ascii=False)}"
        )

    async def _get_previous_summaries(self, chapter_no: int) -> str:
        """获取前章摘要（最近 5 章）。"""
        stmt = (
            select(ChapterSummary)
            .where(
                ChapterSummary.project_id == self.project_id,
                ChapterSummary.chapter_no < chapter_no,
            )
            .order_by(ChapterSummary.chapter_no.desc())
            .limit(5)
        )
        result = await self.db.execute(stmt)
        summaries = result.scalars().all()
        if not summaries:
            return "（无前章，本章为开篇）"
        parts = []
        for s in reversed(summaries):
            parts.append(f"第{s.chapter_no}章：{s.summary}")
        return "\n\n".join(parts)

    async def _get_character_states(self) -> str:
        """获取角色列表与状态。"""
        stmt = select(Character).where(
            Character.project_id == self.project_id,
            Character.status == "active",
        )
        result = await self.db.execute(stmt)
        characters = result.scalars().all()
        if not characters:
            return "（暂无角色信息）"
        parts = []
        for c in characters:
            parts.append(
                f"- {c.name}（{c.role}）：{c.description or '无描述'}"
            )
        return "\n".join(parts)

    async def _get_story_state(self, chapter_no: int) -> str:
        """获取最新故事状态。"""
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
        if not state:
            return "（暂无故事状态，本章为开篇）"
        return (
            f"最新章节：第{state.chapter_no}章\n"
            f"状态摘要：{state.summary or '无'}\n"
            f"详细状态：{json.dumps(state.state, ensure_ascii=False)}"
        )

    async def _get_foreshadows(self) -> str:
        """获取活跃伏笔。"""
        stmt = select(PlotThread).where(
            PlotThread.project_id == self.project_id,
            PlotThread.status.in_(["planned", "active"]),
            PlotThread.type == "foreshadow",
        )
        result = await self.db.execute(stmt)
        threads = result.scalars().all()
        if not threads:
            return "（暂无活跃伏笔）"
        parts = []
        for t in threads:
            parts.append(
                f"- {t.name}：{t.description or '无描述'}"
                f"（引入于第{t.introduced_chapter or '?'}章）"
            )
        return "\n".join(parts)
