"""Drafter Agent — 正文起草。

职责：根据写作计划逐场景生成正文，返回 ManuscriptBlock 列表。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.models.chapter import Chapter, ManuscriptBlock
from app.db.models.character import Character
from app.db.models.world import WorldBible
from app.prompts.templates.draft import DRAFT_SYSTEM, DRAFT_USER, SCENE_DRAFT_SYSTEM

logger = logging.getLogger("app.agents.drafter")


class Drafter(BaseAgent):
    """起草者 Agent，负责根据计划生成章节正文。"""

    agent_name = "Drafter"

    async def draft_chapter(
        self,
        plan: dict[str, Any],
        chapter_id: Optional[uuid.UUID] = None,
    ) -> list[ManuscriptBlock]:
        """根据写作计划生成整章正文。

        按场景逐个生成，将正文切分为 ManuscriptBlock 列表。

        Args:
            plan: 写作计划 dict（由 ChapterPlanner 生成）。
            chapter_id: 章节 ID，用于关联 ManuscriptBlock。

        Returns:
            ManuscriptBlock 列表。
        """
        # 准备上下文
        world_summary = await self._get_world_summary()
        characters_info = await self._get_characters_info()
        previous_text = await self._get_previous_text(plan.get("chapter_no", 1))

        # 整章生成模式：一次性生成全部正文
        user_prompt = DRAFT_USER.format(
            world_summary=world_summary,
            chapter_plan=self._format_plan(plan),
            previous_text=previous_text,
            characters_info=characters_info,
        )

        full_text = await self._llm_complete(
            system_prompt=DRAFT_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.8,
            max_tokens=8192,
        )

        # 将正文切分为 ManuscriptBlock
        blocks = self._split_into_blocks(full_text, chapter_id)

        logger.info(
            "项目 %s 第 %d 章正文已生成: %d 个 block, 约 %d 字",
            self.project_id,
            plan.get("chapter_no", 0),
            len(blocks),
            sum(len(b.content) for b in blocks),
        )
        return blocks

    async def draft_scene(
        self,
        scene_plan: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """单场景正文生成。

        Args:
            scene_plan: 单场景计划 dict。
            context: 上下文信息（world_summary, characters_info, previous_text 等）。

        Returns:
            场景正文文本。
        """
        user_prompt = (
            f"【世界观摘要】\n{context.get('world_summary', '无')}\n\n"
            f"【场景计划】\n{self._format_scene(scene_plan)}\n\n"
            f"【前文】\n{context.get('previous_text', '无')}\n\n"
            f"【角色信息】\n{context.get('characters_info', '无')}\n\n"
            f"请生成该场景的正文（800-1500字），直接输出正文文本。"
        )

        text = await self._llm_complete(
            system_prompt=SCENE_DRAFT_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.8,
            max_tokens=4096,
        )
        return text

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _format_plan(self, plan: dict[str, Any]) -> str:
        """格式化写作计划为文本。"""
        lines = [
            f"章节标题：{plan.get('chapter_title', '')}",
            f"总体目标：{plan.get('overall_goal', '')}",
            f"主视角：{plan.get('pov', '未指定')}",
            "",
            "场景列表：",
        ]
        for scene in plan.get("scene_list", []):
            lines.append(
                f"  场景{scene.get('scene_no', '?')}：{scene.get('summary', '')}\n"
                f"    角色：{', '.join(scene.get('characters', []))}\n"
                f"    地点：{scene.get('location', '')}\n"
                f"    氛围：{scene.get('mood', '')}\n"
                f"    情节推进：{scene.get('plot_advancement', '')}\n"
                f"    目标字数：{scene.get('target_words', 1000)}"
            )
        lines.append(f"\n章末钩子：{plan.get('ending_hook', '')}")
        return "\n".join(lines)

    def _format_scene(self, scene: dict[str, Any]) -> str:
        """格式化单场景计划。"""
        return (
            f"场景{scene.get('scene_no', '?')}：{scene.get('summary', '')}\n"
            f"角色：{', '.join(scene.get('characters', []))}\n"
            f"地点：{scene.get('location', '')}\n"
            f"氛围：{scene.get('mood', '')}\n"
            f"情节推进：{scene.get('plot_advancement', '')}"
        )

    def _split_into_blocks(
        self, text: str, chapter_id: Optional[uuid.UUID]
    ) -> list[ManuscriptBlock]:
        """将正文文本切分为 ManuscriptBlock 列表。

        切分规则：
        - 以空行分段
        - 包含对话标记的段为 dialogue 类型
        - 场景分隔标记（*** 或 ---）为 scene_break 类型
        - 其余为 paragraph 类型
        """
        blocks: list[ManuscriptBlock] = []
        block_no = 0

        # 按空行分段
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        for para in paragraphs:
            block_no += 1

            # 场景分隔标记
            if para in ("***", "---", "* * *") or para.startswith("***") or para.startswith("---"):
                block_type = "scene_break"
                content = ""
            # 对话段（含中文引号或常见对话标记）
            elif any(
                marker in para
                for marker in ['「', '」', '"', '"', '"', '"', "——", "说：", "道："]
            ) and len(para) < 500:
                block_type = "dialogue"
                content = para
            else:
                block_type = "paragraph"
                content = para

            block = ManuscriptBlock(
                chapter_id=chapter_id,
                block_no=block_no,
                content=content,
                block_type=block_type,
            )
            blocks.append(block)

        # 如果没有切分出任何 block，将整段作为一个 paragraph
        if not blocks and text.strip():
            blocks.append(
                ManuscriptBlock(
                    chapter_id=chapter_id,
                    block_no=1,
                    content=text.strip(),
                    block_type="paragraph",
                )
            )

        return blocks

    async def _get_world_summary(self) -> str:
        """获取世界观摘要。"""
        stmt = (
            select(WorldBible)
            .where(WorldBible.project_id == self.project_id)
            .order_by(WorldBible.version.desc())
        )
        result = await self.db.execute(stmt)
        bible = result.scalars().first()
        return bible.summary if bible and bible.summary else "（暂无世界观设定）"

    async def _get_characters_info(self) -> str:
        """获取角色信息。"""
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
            parts.append(f"- {c.name}（{c.role}）：{c.description or '无描述'}")
        return "\n".join(parts)

    async def _get_previous_text(self, chapter_no: int) -> str:
        """获取前一章正文节选（最后 500 字）。"""
        if chapter_no <= 1:
            return "（本章为第一章，无前文）"
        stmt = (
            select(ManuscriptBlock)
            .join(Chapter, ManuscriptBlock.chapter_id == Chapter.id)
            .where(
                Chapter.project_id == self.project_id,
                Chapter.chapter_no == chapter_no - 1,
            )
            .order_by(ManuscriptBlock.block_no.desc())
            .limit(20)
        )
        result = await self.db.execute(stmt)
        blocks = list(reversed(result.scalars().all()))
        if not blocks:
            return "（前章无正文）"
        text = "\n\n".join(b.content for b in blocks if b.content)
        # 取最后 500 字
        if len(text) > 500:
            text = "..." + text[-500:]
        return text
