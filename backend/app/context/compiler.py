"""Context Compiler - 按 v5.0 规范的固定预算比例分配上下文。

不再"塞得下多少塞多少"，而是按照预定义的预算比例为每个上下文区块分配 token：
  hard_constraints  5%  — 硬约束（字数、禁令）
  canon_facts      20%  — 设定事实检索
  recent_fulltext  30%  — 近期全文
  arc_summary      15%  — 弧线摘要
  character_cards  15%  — 角色卡
  foreshadow       10%  — 伏笔线索
  style             5%  — 文风要求
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context._llm import estimate_tokens, truncate_to_tokens
from app.context.canon_manager import CanonManager
from app.db.models.chapter import Chapter, ChapterVersion, ManuscriptBlock
from app.db.models.character import Character
from app.db.models.memory import BookMemory
from app.db.models.plot import PlotThread
from app.db.models.summary import NarrativeSummary

logger = logging.getLogger("app.context.compiler")


@dataclass
class CompiledContext:
    """编译后的上下文。"""

    system_prompt: str
    context_text: str
    total_tokens: int
    budget_breakdown: dict[str, int] = field(default_factory=dict)
    provenance: list[dict] = field(default_factory=list)


class ContextCompiler:
    """按预算比例组装上下文。"""

    BUDGET: dict[str, float] = {
        "hard_constraints": 0.05,
        "canon_facts": 0.20,
        "recent_fulltext": 0.30,
        "arc_summary": 0.15,
        "character_cards": 0.15,
        "foreshadow": 0.10,
        "style": 0.05,
    }

    # 各区块的标签
    _LABELS: dict[str, str] = {
        "hard_constraints": "\u3010\u786c\u7ea6\u675f\u3011",      # 【硬约束】
        "canon_facts": "\u3010\u8bbe\u5b9a\u4e8b\u5b9e\u3011",      # 【设定事实】
        "recent_fulltext": "\u3010\u8fd1\u671f\u6b63\u6587\u3011",  # 【近期正文】
        "arc_summary": "\u3010\u5f27\u7ebf\u6458\u8981\u3011",      # 【弧线摘要】
        "character_cards": "\u3010\u89d2\u8272\u5361\u3011",        # 【角色卡】
        "foreshadow": "\u3010\u4f0f\u7b14\u7ebf\u7d22\u3011",      # 【伏笔线索】
        "style": "\u3010\u6587\u98ce\u8981\u6c42\u3011",            # 【文风要求】
    }

    def __init__(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        gateway: Any = None,
        context_window: int = 8192,
    ) -> None:
        self.db = db
        self.project_id = project_id
        self.gateway = gateway
        self.context_window = context_window
        self.canon_manager = CanonManager(db, project_id)

    # ------------------------------------------------------------------
    # 主编译入口
    # ------------------------------------------------------------------

    async def compile(
        self, chapter_no: int, scene_plan: dict | None = None
    ) -> CompiledContext:
        """编译完整上下文。

        1. 计算各部分 token 预算
        2. 逐区块采集内容并截断到预算
        3. 组装为带来源追溯的 CompiledContext
        """
        budgets: dict[str, int] = {
            k: int(v * self.context_window) for k, v in self.BUDGET.items()
        }

        sections: dict[str, str] = {}
        breakdown: dict[str, int] = {}
        provenance: list[dict] = []

        # ---- 1. 硬约束 ----
        raw = self._get_hard_constraints(scene_plan, chapter_no)
        text = truncate_to_tokens(raw, budgets["hard_constraints"])
        sections["hard_constraints"] = text
        breakdown["hard_constraints"] = estimate_tokens(text)
        provenance.append({
            "type": "hard_constraints",
            "source_id": "scene_plan",
            "tokens": breakdown["hard_constraints"],
        })

        # ---- 2. Canon facts ----
        raw = await self._get_canon_facts_context(chapter_no)
        text = truncate_to_tokens(raw, budgets["canon_facts"])
        sections["canon_facts"] = text
        breakdown["canon_facts"] = estimate_tokens(text)
        provenance.append({
            "type": "canon_facts",
            "source_id": "canon_facts",
            "tokens": breakdown["canon_facts"],
        })

        # ---- 3. 近期全文 ----
        raw = await self._get_recent_fulltext(chapter_no)
        text = truncate_to_tokens(raw, budgets["recent_fulltext"])
        sections["recent_fulltext"] = text
        breakdown["recent_fulltext"] = estimate_tokens(text)
        provenance.append({
            "type": "recent_fulltext",
            "source_id": "chapters",
            "tokens": breakdown["recent_fulltext"],
        })

        # ---- 4. 弧线摘要 ----
        raw = await self._get_arc_summary(chapter_no)
        text = truncate_to_tokens(raw, budgets["arc_summary"])
        sections["arc_summary"] = text
        breakdown["arc_summary"] = estimate_tokens(text)
        provenance.append({
            "type": "arc_summary",
            "source_id": "narrative_summaries",
            "tokens": breakdown["arc_summary"],
        })

        # ---- 5. 角色卡 ----
        character_ids: list = []
        if scene_plan and isinstance(scene_plan.get("character_ids"), list):
            character_ids = scene_plan["character_ids"]
        raw = await self._get_character_cards(character_ids)
        text = truncate_to_tokens(raw, budgets["character_cards"])
        sections["character_cards"] = text
        breakdown["character_cards"] = estimate_tokens(text)
        provenance.append({
            "type": "character_cards",
            "source_id": "characters",
            "tokens": breakdown["character_cards"],
        })

        # ---- 6. 伏笔 ----
        raw = await self._get_foreshadow()
        text = truncate_to_tokens(raw, budgets["foreshadow"])
        sections["foreshadow"] = text
        breakdown["foreshadow"] = estimate_tokens(text)
        provenance.append({
            "type": "foreshadow",
            "source_id": "plot_threads",
            "tokens": breakdown["foreshadow"],
        })

        # ---- 7. 文风 ----
        raw = await self._get_style_memory()
        text = truncate_to_tokens(raw, budgets["style"])
        sections["style"] = text
        breakdown["style"] = estimate_tokens(text)
        provenance.append({
            "type": "style",
            "source_id": "book_memory",
            "tokens": breakdown["style"],
        })

        # ---- 组装上下文文本 ----
        context_parts: list[str] = []
        for key in self.BUDGET:
            content = sections.get(key, "")
            if content:
                context_parts.append(f"{self._LABELS[key]}\n{content}")

        context_text = "\n\n".join(context_parts)
        total_tokens = sum(breakdown.values())

        system_prompt = (
            "你是一个专业的小说创作 AI。请根据以下上下文信息撰写小说章节正文。"
            "严格遵守【硬约束】中的要求，保持与【设定事实】的一致性，"
            "延续【近期正文】的文风和剧情，参考【角色卡】中的角色设定，"
            "推进【伏笔线索】中的未完成情节。"
        )

        logger.info(
            "上下文编译完成: 总计 %d tokens, 预算分配 %s",
            total_tokens, breakdown,
        )

        return CompiledContext(
            system_prompt=system_prompt,
            context_text=context_text,
            total_tokens=total_tokens,
            budget_breakdown=breakdown,
            provenance=provenance,
        )

    # ------------------------------------------------------------------
    # 各区块采集
    # ------------------------------------------------------------------

    def _get_hard_constraints(
        self, scene_plan: dict | None, chapter_no: int
    ) -> str:
        """构建硬约束文本（字数要求、禁止内容等）。"""
        parts: list[str] = [f"当前章节：第 {chapter_no} 章"]

        if scene_plan:
            target_words = scene_plan.get("target_words")
            if target_words:
                parts.append(f"目标字数：{target_words} 字")

            forbidden = scene_plan.get("forbidden", [])
            if forbidden and isinstance(forbidden, list):
                parts.append("禁止内容：" + "；".join(str(f) for f in forbidden))

            required = scene_plan.get("required_elements", [])
            if required and isinstance(required, list):
                parts.append("必须包含：" + "；".join(str(r) for r in required))

            scene_description = scene_plan.get("scene_description")
            if scene_description:
                parts.append(f"场景要求：{scene_description}")

        parts.append("禁止出现与已有设定矛盾的内容")
        return "\n".join(parts)

    async def _get_canon_facts_context(self, chapter_no: int) -> str:
        """获取相关 canon facts（所有 active 事实）。"""
        facts = await self.canon_manager.get_active_facts()
        if not facts:
            return ""

        mutability_tag = {
            "immutable": "[不可变]",
            "soft": "[可演进]",
            "dynamic": "[动态]",
        }

        lines: list[str] = []
        for f in facts:
            tag = mutability_tag.get(f.mutability, "")
            confirmed = ""
            if f.last_confirmed_chapter_no:
                confirmed = f"（确认至第{f.last_confirmed_chapter_no}章）"
            lines.append(
                f"- {tag} {f.subject_type}:{f.subject_id or ''} 的 "
                f"{f.predicate} = {f.object_value}{confirmed}"
            )
        return "\n".join(lines)

    async def _get_recent_fulltext(
        self, chapter_no: int, count: int = 3
    ) -> str:
        """获取最近 N 章的正文。"""
        start_no = max(1, chapter_no - count)
        stmt = (
            select(Chapter)
            .where(
                Chapter.project_id == self.project_id,
                Chapter.chapter_no >= start_no,
                Chapter.chapter_no < chapter_no,
            )
            .order_by(Chapter.chapter_no.asc())
        )
        result = await self.db.execute(stmt)
        chapters = list(result.scalars().all())
        if not chapters:
            return ""

        parts: list[str] = []
        for ch in chapters:
            content = await self._get_chapter_text(ch)
            if content:
                parts.append(f"--- 第{ch.chapter_no}章 {ch.title} ---\n{content}")
        return "\n\n".join(parts)

    async def _get_chapter_text(self, chapter: Chapter) -> str:
        """获取单章正文（优先 current_version，其次 manuscript_blocks）。"""
        if chapter.current_version_id:
            version = await self.db.get(ChapterVersion, chapter.current_version_id)
            if version and version.content:
                return version.content

        blk_stmt = (
            select(ManuscriptBlock)
            .where(ManuscriptBlock.chapter_id == chapter.id)
            .order_by(ManuscriptBlock.block_no.asc())
        )
        result = await self.db.execute(blk_stmt)
        blocks = list(result.scalars().all())
        return "\n".join(b.content for b in blocks if b.content)

    async def _get_arc_summary(self, chapter_no: int) -> str:
        """获取当前弧线/卷的摘要。"""
        stmt = select(NarrativeSummary).where(
            NarrativeSummary.project_id == self.project_id,
        )
        result = await self.db.execute(stmt)
        summaries = list(result.scalars().all())
        if not summaries:
            return ""

        # 筛选与当前章节相关的摘要
        relevant: list[NarrativeSummary] = []
        for s in summaries:
            if s.scope_start is not None and s.scope_end is not None:
                if s.scope_start <= chapter_no <= s.scope_end:
                    relevant.append(s)
            elif s.scope_start is not None and s.scope_end is None:
                if s.scope_start <= chapter_no:
                    relevant.append(s)
            else:
                # scope_start / scope_end 均为 None → 全书级
                relevant.append(s)

        if not relevant:
            return ""

        # 按粒度排序：chapter_range → volume → book
        scope_order = {"chapter_range": 0, "volume": 1, "book": 2}
        relevant.sort(key=lambda s: scope_order.get(s.scope, 99))

        scope_labels = {
            "chapter_range": "章节范围",
            "volume": "卷",
            "book": "全书",
        }

        lines: list[str] = []
        for s in relevant[:3]:
            label = scope_labels.get(s.scope, s.scope)
            range_str = ""
            if s.scope_start and s.scope_end:
                range_str = f"（{s.scope_start}-{s.scope_end}）"
            lines.append(f"[{label}{range_str}] {s.summary}")
        return "\n".join(lines)

    async def _get_character_cards(self, character_ids: list) -> str:
        """获取角色状态卡。"""
        if character_ids:
            # 按指定 ID 查询
            id_set: set[uuid.UUID] = set()
            for cid in character_ids:
                try:
                    id_set.add(uuid.UUID(str(cid)))
                except (ValueError, TypeError):
                    pass

            if id_set:
                stmt = select(Character).where(
                    Character.project_id == self.project_id,
                    Character.id.in_(id_set),
                )
                result = await self.db.execute(stmt)
                chars = list(result.scalars().all())
            else:
                chars = []
        else:
            # 无指定则取全部活跃角色（最多 10 个）
            stmt = (
                select(Character)
                .where(
                    Character.project_id == self.project_id,
                    Character.status == "active",
                )
                .order_by(Character.created_at.asc())
                .limit(10)
            )
            result = await self.db.execute(stmt)
            chars = list(result.scalars().all())

        if not chars:
            return ""

        lines: list[str] = []
        for c in chars:
            parts = [f"- {c.name}（{c.role}）"]
            if c.description:
                parts.append(f"  描述：{c.description}")
            attrs = c.attributes or {}
            if attrs and isinstance(attrs, dict):
                attr_str = "；".join(f"{k}: {v}" for k, v in attrs.items())
                parts.append(f"  属性：{attr_str}")
            if c.first_appearance_chapter:
                parts.append(f"  首次出场：第{c.first_appearance_chapter}章")
            lines.append("\n".join(parts))
        return "\n".join(lines)

    async def _get_foreshadow(self) -> str:
        """获取未完成伏笔（planned / active 的 plot_threads）。"""
        stmt = (
            select(PlotThread)
            .where(
                PlotThread.project_id == self.project_id,
                PlotThread.status.in_(["planned", "active"]),
            )
            .order_by(PlotThread.importance.desc())
        )
        result = await self.db.execute(stmt)
        threads = list(result.scalars().all())
        if not threads:
            return ""

        type_labels = {"main": "主线", "sub": "支线", "foreshadow": "伏笔"}
        status_labels = {"planned": "待展开", "active": "进行中"}

        lines: list[str] = []
        for t in threads:
            type_label = type_labels.get(t.type, t.type)
            status_label = status_labels.get(t.status, t.status)
            line = f"- [{type_label}][{status_label}] {t.name}"
            if t.description:
                line += f"：{t.description}"
            if t.introduced_chapter:
                line += f"（始于第{t.introduced_chapter}章）"
            lines.append(line)
        return "\n".join(lines)

    async def _get_style_memory(self) -> str:
        """获取文风记忆（从 book_memory 提取 style/tone/convention）。"""
        stmt = select(BookMemory).where(
            BookMemory.project_id == self.project_id,
            BookMemory.memory_type.in_(["style", "tone", "convention"]),
        )
        result = await self.db.execute(stmt)
        memories = list(result.scalars().all())
        if not memories:
            return ""

        lines: list[str] = []
        for m in memories:
            value_str = ""
            if isinstance(m.value, dict):
                value_str = "；".join(f"{k}: {v}" for k, v in m.value.items())
            elif isinstance(m.value, str):
                value_str = m.value
            if value_str:
                lines.append(f"- [{m.key}] {value_str}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 公开工具方法
    # ------------------------------------------------------------------

    def _truncate_to_budget(self, text: str, token_budget: int) -> str:
        """按 token 预算截断文本（对外暴露的别名）。"""
        return truncate_to_tokens(text, token_budget)
