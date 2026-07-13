"""Drafter Agent — 正文起草。

职责：根据写作计划逐场景生成正文，返回 ManuscriptBlock 列表。
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.models.chapter import Chapter, ManuscriptBlock
from app.db.models.character import Character
from app.db.models.world import WorldBible
from app.domain.errors import AgentExecutionError, EmptyResultError, TruncationError
from app.prompts.templates.draft import DRAFT_SYSTEM, DRAFT_USER, SCENE_DRAFT_SYSTEM

logger = logging.getLogger("app.agents.drafter")


class Drafter(BaseAgent):
    """起草者 Agent，负责根据计划生成章节正文。"""

    agent_name = "Drafter"
    _SCENE_SEPARATOR = "\n\n***\n\n"
    _CONTINUITY_TAIL_CHARS = 3500

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
        production_context = plan.get("_compiled_context", {})
        if isinstance(production_context, dict) and production_context.get("context_text"):
            world_summary = (
                f"{world_summary}\n\n【长篇生产上下文】\n{production_context['context_text']}"
            )

        # 整章生成模式：一次性生成全部正文
        user_prompt = DRAFT_USER.format(
            world_summary=world_summary,
            chapter_plan=self._format_plan(plan),
            previous_text=previous_text,
            characters_info=characters_info,
        )

        try:
            context_system_prompt = (
                production_context.get("system_prompt", "")
                if isinstance(production_context, dict)
                else ""
            )
            full_text = await self._llm_complete(
                system_prompt=(
                    f"{DRAFT_SYSTEM}\n\n{context_system_prompt}"
                    if context_system_prompt
                    else DRAFT_SYSTEM
                ),
                user_prompt=user_prompt,
                temperature=0.8,
                max_tokens=16384,
                stream=True,
            )
        except TruncationError as exc:
            logger.warning(
                "项目 %s 第 %d 章整章输出触顶，丢弃截断稿并切换逐场景原子生成",
                self.project_id,
                plan.get("chapter_no", 0),
            )
            return await self._draft_chapter_by_scene(
                plan=plan,
                chapter_id=chapter_id,
                world_summary=world_summary,
                characters_info=characters_info,
                previous_text=previous_text,
                context_system_prompt=context_system_prompt,
                whole_chapter_error=exc,
            )
        except Exception as exc:
            logger.error("项目 %s Drafter LLM 调用失败: %s", self.project_id, exc)
            raise AgentExecutionError(
                "Drafter LLM 调用失败",
                agent_name="drafter",
                project_id=self.project_id,
                cause=exc,
            ) from exc

        # 校验：正文不能为空或过短
        if not full_text or not full_text.strip():
            raise EmptyResultError(
                "Drafter 返回空正文",
                agent_name="drafter",
                project_id=self.project_id,
            )
        if len(full_text) < 100:
            raise EmptyResultError(
                f"Drafter 正文过短（{len(full_text)}字），可能生成不完整",
                agent_name="drafter",
                project_id=self.project_id,
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
        target_words = self._scene_target_words(scene_plan)
        chapter_context = str(context.get("chapter_context") or "无")
        user_prompt = (
            f"【世界观摘要】\n{context.get('world_summary', '无')}\n\n"
            f"【本章全局目标】\n{chapter_context}\n\n"
            f"【场景进度】\n{context.get('scene_progress', '未指定')}\n\n"
            f"【场景计划】\n{self._format_scene(scene_plan)}\n\n"
            f"【必须无缝承接的前文】\n{context.get('previous_text', '无')}\n\n"
            f"【角色信息】\n{context.get('characters_info', '无')}\n\n"
            f"【下一场景方向】\n{context.get('next_scene_summary', '本场景为章末')}\n\n"
            f"请生成该场景约 {target_words} 字的正文（合理范围 800-1500 字），"
            "直接输出正文文本；不要输出场景标题、编号、解释或写作元信息。"
        )

        context_system_prompt = str(context.get("context_system_prompt") or "").strip()
        system_prompt = SCENE_DRAFT_SYSTEM
        if context_system_prompt:
            system_prompt = f"{system_prompt}\n\n{context_system_prompt}"

        text = await self._llm_complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.8,
            max_tokens=8192,
            stream=True,
        )
        text = (text or "").strip()
        if not text:
            raise EmptyResultError(
                "Drafter 逐场景后备返回空正文",
                agent_name="drafter",
                project_id=str(self.project_id),
            )
        char_count = self._narrative_char_count(text)
        minimum_chars = max(200, min(600, int(target_words * 0.35)))
        if char_count < minimum_chars:
            raise EmptyResultError(
                f"Drafter 场景正文过短（{char_count}字，至少需要{minimum_chars}字）",
                agent_name="drafter",
                project_id=str(self.project_id),
            )
        return text

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    async def _draft_chapter_by_scene(
        self,
        *,
        plan: dict[str, Any],
        chapter_id: Optional[uuid.UUID],
        world_summary: str,
        characters_info: str,
        previous_text: str,
        context_system_prompt: str,
        whole_chapter_error: TruncationError,
    ) -> list[ManuscriptBlock]:
        """Generate every planned scene in memory after a whole-chapter truncation.

        No manuscript block is constructed until every scene has completed and
        the merged length has passed validation.  The orchestrator therefore
        cannot flush a partial fallback chapter.  AgentRun audit rows remain in
        the surrounding chapter savepoint and are rolled back with it on error.
        """
        raw_scenes = plan.get("scene_list")
        if not isinstance(raw_scenes, list) or not raw_scenes:
            logger.error(
                "项目 %s 整章输出已截断，但计划没有可用 scene_list，无法安全降级",
                self.project_id,
            )
            raise whole_chapter_error
        if any(not isinstance(scene, dict) for scene in raw_scenes):
            raise AgentExecutionError(
                "Drafter 逐场景后备失败：scene_list 包含无效场景",
                agent_name="drafter",
                project_id=str(self.project_id),
                chapter_no=self._chapter_no(plan),
            )

        scenes: list[dict[str, Any]] = raw_scenes
        drafted_scenes: list[str] = []
        chapter_context = self._fallback_chapter_context(plan)
        for index, scene_plan in enumerate(scenes):
            scene_label = scene_plan.get("scene_no", index + 1)
            continuity_text = self._fallback_continuity_text(
                previous_text=previous_text,
                drafted_scenes=drafted_scenes,
            )
            next_summary = "本场景为章末，必须兑现章末钩子"
            if index + 1 < len(scenes):
                next_summary = str(scenes[index + 1].get("summary") or "按计划自然过渡")
            context = {
                "world_summary": world_summary,
                "characters_info": characters_info,
                "previous_text": continuity_text,
                "chapter_context": chapter_context,
                "scene_progress": f"第 {index + 1}/{len(scenes)} 场；计划编号 {scene_label}",
                "next_scene_summary": next_summary,
                "context_system_prompt": context_system_prompt,
            }
            try:
                scene_text = await self.draft_scene(scene_plan, context)
            except TruncationError as exc:
                raise TruncationError(
                    f"Drafter 逐场景后备的第 {scene_label} 场仍达到输出上限",
                    agent_name="drafter",
                    project_id=str(self.project_id),
                    chapter_no=self._chapter_no(plan),
                    cause=exc,
                ) from exc
            except AgentExecutionError:
                raise
            except Exception as exc:
                raise AgentExecutionError(
                    f"Drafter 逐场景后备的第 {scene_label} 场生成失败",
                    agent_name="drafter",
                    project_id=str(self.project_id),
                    chapter_no=self._chapter_no(plan),
                    cause=exc,
                ) from exc
            drafted_scenes.append(scene_text)

        full_text = self._SCENE_SEPARATOR.join(drafted_scenes)
        actual_chars = self._narrative_char_count(full_text)
        target_chars = sum(self._scene_target_words(scene) for scene in scenes)
        minimum_chars = max(500, len(scenes) * 200, int(target_chars * 0.45))
        maximum_chars = max(minimum_chars + 1, int(target_chars * 2.5))
        if not minimum_chars <= actual_chars <= maximum_chars:
            raise EmptyResultError(
                "Drafter 逐场景合并稿总长异常："
                f"{actual_chars}字，不在安全范围 {minimum_chars}-{maximum_chars} 字",
                agent_name="drafter",
                project_id=str(self.project_id),
                chapter_no=self._chapter_no(plan),
            )

        # ManuscriptBlock objects are created only after the complete in-memory
        # chapter passes validation; callers persist the returned list atomically.
        blocks = self._split_into_blocks(full_text, chapter_id)
        if not blocks:
            raise EmptyResultError(
                "Drafter 逐场景合并稿无法切分为正文块",
                agent_name="drafter",
                project_id=str(self.project_id),
                chapter_no=self._chapter_no(plan),
            )
        logger.info(
            "项目 %s 第 %d 章逐场景后备成功: %d 个场景, %d 个 block, 约 %d 字",
            self.project_id,
            self._chapter_no(plan),
            len(drafted_scenes),
            len(blocks),
            actual_chars,
        )
        return blocks

    def _fallback_chapter_context(self, plan: dict[str, Any]) -> str:
        """Keep every scene aligned with the same chapter-level contract."""
        parts = [
            f"章节：第 {self._chapter_no(plan)} 章《{plan.get('chapter_title', '')}》",
            f"总体目标：{plan.get('overall_goal', '')}",
            f"主视角：{plan.get('pov', '未指定')}",
            f"章末钩子：{plan.get('ending_hook', '')}",
        ]
        repair_context = str(plan.get("_quality_repair_context") or "").strip()
        if repair_context:
            parts.append(f"历史质检禁区（必须规避）：\n{repair_context}")
        return "\n".join(parts)

    def _fallback_continuity_text(
        self,
        *,
        previous_text: str,
        drafted_scenes: list[str],
    ) -> str:
        """Build a bounded hand-off containing the real end of prior prose."""
        if not drafted_scenes:
            return previous_text
        current_chapter = self._SCENE_SEPARATOR.join(drafted_scenes)
        current_tail = current_chapter[-self._CONTINUITY_TAIL_CHARS :]
        prior_tail = previous_text[-800:]
        return (
            f"【前章末尾】\n{prior_tail}\n\n"
            "【本章已经定稿的前置场景末尾】\n"
            f"{current_tail}\n\n"
            "从最后一句继续，不得重写、复述或推翻已经完成的场景。"
        )

    @staticmethod
    def _scene_target_words(scene: dict[str, Any]) -> int:
        """Normalize planner targets so prompt and validation share one bound."""
        try:
            target = int(scene.get("target_words") or 1000)
        except (TypeError, ValueError):
            target = 1000
        return max(300, min(target, 2500))

    @staticmethod
    def _narrative_char_count(text: str) -> int:
        """Count visible narrative characters, excluding whitespace/separators."""
        without_breaks = text.replace("***", "").replace("---", "")
        return len(re.sub(r"\s+", "", without_breaks))

    @staticmethod
    def _chapter_no(plan: dict[str, Any]) -> int:
        try:
            return int(plan.get("chapter_no") or 0)
        except (TypeError, ValueError):
            return 0

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
        repair_context = str(plan.get("_quality_repair_context") or "").strip()
        if repair_context:
            lines.extend(
                [
                    "",
                    "【本章历史质检禁区】",
                    "这是此前版本未通过终审的真实原因。本次必须重新设计并逐项规避：",
                    repair_context,
                ]
            )
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
            elif (
                any(
                    marker in para
                    for marker in ["「", "」", '"', '"', '"', '"', "——", "说：", "道："]
                )
                and len(para) < 500
            ):
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
