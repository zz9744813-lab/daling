"""ContinuityGuard Agent — 一致性校验（基础版）。

职责：检查正文与已有设定、前文内容的一致性。
Phase 5 会增强为基于 CanonFact 的完整一致性校验。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.models.chapter import ManuscriptBlock
from app.db.models.character import Character
from app.db.models.plot import PlotThread
from app.db.models.summary import ChapterSummary
from app.db.models.world import WorldBible
from app.prompts.templates.continuity import CONTINUITY_SYSTEM, CONTINUITY_USER

logger = logging.getLogger("app.agents.continuity_guard")


class ContinuityGuard(BaseAgent):
    """一致性守卫 Agent，负责跨章节一致性校验。"""

    agent_name = "ContinuityGuard"

    async def check_texts(
        self,
        block_texts: list[dict[str, Any]],
        chapter_no: int,
    ) -> dict[str, Any]:
        """对章节正文进行一致性校验（接受内容快照）。"""
        manuscript_text = "\n\n".join(
            b["content"] for b in block_texts if b.get("content")
        )
        world_summary = await self._get_world_summary()
        previous_summaries = await self._get_previous_summaries(chapter_no)
        characters_info = await self._get_characters_info()
        foreshadows = await self._get_foreshadows()

        user_prompt = CONTINUITY_USER.format(
            chapter_no=chapter_no,
            manuscript_text=manuscript_text,
            world_summary=world_summary,
            previous_summaries=previous_summaries,
            characters_info=characters_info,
            foreshadows=foreshadows,
        )

        try:
            result = await self._llm_json(
                system_prompt=CONTINUITY_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.2,
            )
        except Exception as exc:
            logger.warning(
                "项目 %s ContinuityGuard LLM 调用失败，使用默认结果: %s",
                self.project_id, exc,
            )
            result = {}

        result.setdefault("passed", True)
        result.setdefault("conflicts", [])
        result.setdefault("warnings", [])

        if result["conflicts"]:
            result["passed"] = False

        logger.info(
            "项目 %s 第 %d 章一致性校验: passed=%s, %d 个冲突, %d 个警告",
            self.project_id, chapter_no, result["passed"],
            len(result["conflicts"]), len(result["warnings"]),
        )
        return result

    async def check(
        self,
        blocks: list[ManuscriptBlock],
        chapter_no: int,
    ) -> dict[str, Any]:
        """对章节正文进行一致性校验（接受 ORM ManuscriptBlock）。"""
        block_texts = [
            {"content": b.content, "block_type": b.block_type, "block_no": b.block_no}
            for b in blocks
        ]
        return await self.check_texts(block_texts, chapter_no)

    # ------------------------------------------------------------------
    # 辅助查询
    # ------------------------------------------------------------------
    async def _get_world_summary(self) -> str:
        stmt = (
            select(WorldBible)
            .where(WorldBible.project_id == self.project_id)
            .order_by(WorldBible.version.desc())
        )
        result = await self.db.execute(stmt)
        bible = result.scalars().first()
        return bible.summary if bible and bible.summary else "（暂无世界观设定）"

    async def _get_previous_summaries(self, chapter_no: int) -> str:
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
            return "（无前章摘要，本章为开篇）"
        parts = []
        for s in reversed(summaries):
            parts.append(f"第{s.chapter_no}章：{s.summary}")
        return "\n\n".join(parts)

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
            parts.append(f"- {c.name}（{c.role}）")
        return "\n".join(parts)

    async def _get_foreshadows(self) -> str:
        stmt = select(PlotThread).where(
            PlotThread.project_id == self.project_id,
            PlotThread.status.in_(["planned", "active"]),
        )
        result = await self.db.execute(stmt)
        threads = result.scalars().all()
        if not threads:
            return "（暂无活跃情节线）"
        parts = []
        for t in threads:
            parts.append(f"- {t.name}（{t.type}）：{t.description or '无描述'}")
        return "\n".join(parts)
