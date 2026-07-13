"""ContinuityGuard - 四步连续性校验引擎。

校验流程：
  Step 1: 从正文抽取事实断言（LLM）
  Step 2: 与 immutable facts 比对 — 冲突直接卡住（passed=False）
  Step 3: 与 evolving facts 比对 — 冲突可自动取代（can_auto_supersede=True）
  Step 4: 时间线校验 — 检查角色死亡后出场、物品销毁后使用等时序问题
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.context.canon_manager import CanonManager
from app.db.models.canon import CanonFact

logger = logging.getLogger("app.context.continuity")


@dataclass
class ContinuityResult:
    """四步校验结果。"""

    passed: bool
    extracted_facts: list[dict] = field(default_factory=list)
    immutable_conflicts: list[dict] = field(default_factory=list)
    evolving_conflicts: list[dict] = field(default_factory=list)
    timeline_issues: list[dict] = field(default_factory=list)
    can_auto_supersede: bool = False


class ContinuityGuard:
    """ContinuityGuard 四步校验。"""

    # 终态谓词集合 — 表示主体进入不可逆状态
    _TERMINAL_PREDICATES: set[str] = {
        "死亡",
        "销毁",
        "消失",
        "离开",
        "被捕",
        "阵亡",
        "牺牲",
        "陨落",
        "毁灭",
        "封印",
    }

    def __init__(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        canon_manager: CanonManager,
        gateway: Any = None,
    ) -> None:
        self.db = db
        self.project_id = project_id
        self.canon_manager = canon_manager
        self.gateway = gateway

    # ------------------------------------------------------------------
    # 主校验入口
    # ------------------------------------------------------------------

    async def check(self, blocks: list, chapter_no: int) -> ContinuityResult:
        """四步校验。

        Args:
            blocks: 正文块列表（ManuscriptBlock 对象 / dict / str 均可）。
            chapter_no: 当前章节号。

        Returns:
            ContinuityResult — passed 为 False 表示存在不可解决的 immutable 冲突。
        """
        # Step 1: 从正文抽取事实断言
        extracted_facts = await self._extract_facts(blocks, chapter_no)

        # Step 2: 比 immutable facts
        immutable_conflicts = await self._check_immutable(extracted_facts)

        # Step 3: 比 evolving facts
        evolving_conflicts = await self._check_evolving(extracted_facts)

        # Step 4: 时间线校验
        timeline_issues = await self._check_timeline(extracted_facts, chapter_no)

        # immutable 冲突直接卡住
        passed = len(immutable_conflicts) == 0

        result = ContinuityResult(
            passed=passed,
            extracted_facts=extracted_facts,
            immutable_conflicts=immutable_conflicts,
            evolving_conflicts=evolving_conflicts,
            timeline_issues=timeline_issues,
            can_auto_supersede=(len(immutable_conflicts) == 0 and len(evolving_conflicts) > 0),
        )

        logger.info(
            "连续性校验完成 (第%d章): passed=%s, 抽取事实=%d, "
            "immutable冲突=%d, evolving冲突=%d, 时间线问题=%d",
            chapter_no,
            passed,
            len(extracted_facts),
            len(immutable_conflicts),
            len(evolving_conflicts),
            len(timeline_issues),
        )

        return result

    # ------------------------------------------------------------------
    # Step 1: 事实抽取
    # ------------------------------------------------------------------

    async def _extract_facts(self, blocks: list, chapter_no: int) -> list[dict]:
        """Step 1: 用 LLM 从正文抽取事实断言。

        blocks 可以是 ManuscriptBlock 对象、dict 或 str。
        """
        # 拼接正文
        text_parts: list[str] = []
        for block in blocks:
            if hasattr(block, "content"):
                text_parts.append(block.content)
            elif isinstance(block, dict):
                text_parts.append(block.get("content", ""))
            elif isinstance(block, str):
                text_parts.append(block)
        text = "\n".join(text_parts)

        if not text.strip():
            return []

        return await self.canon_manager.extract_facts_from_text(text, chapter_no)

    # ------------------------------------------------------------------
    # Step 2: immutable 冲突检测
    # ------------------------------------------------------------------

    async def _check_immutable(self, facts: list[dict]) -> list[dict]:
        """Step 2: 与 immutable facts 比对。

        对每个抽取的事实，检查是否有矛盾的 immutable fact：
        同 subject_type + subject_id + predicate，但 object_value 不同。
        """
        if not facts:
            return []

        immutable_facts = await self.canon_manager.get_immutable_facts()
        if not immutable_facts:
            return []

        conflicts: list[dict] = []
        for fact in facts:
            for ef in immutable_facts:
                if self._is_same_subject_and_predicate(fact, ef):
                    if ef.object_value != fact.get("object_value", ""):
                        conflicts.append(
                            {
                                "extracted_fact": fact,
                                "conflicting_fact": self._fact_to_dict(ef),
                                "conflict_type": "immutable",
                                "message": (
                                    f"不可变设定冲突：{ef.subject_type}:{ef.subject_id or ''} 的 "
                                    f"{ef.predicate} 已设定为 '{ef.object_value}'，"
                                    f"但正文尝试改为 '{fact.get('object_value')}'"
                                ),
                            }
                        )
        return conflicts

    # ------------------------------------------------------------------
    # Step 3: evolving 冲突检测
    # ------------------------------------------------------------------

    async def _check_evolving(self, facts: list[dict]) -> list[dict]:
        """Step 3: 与 evolving facts 比对。

        检查是否有变化（可被取代）：同 subject + predicate，但 object_value 不同。
        """
        if not facts:
            return []

        evolving_facts = await self.canon_manager.get_evolving_facts()
        if not evolving_facts:
            return []

        conflicts: list[dict] = []
        for fact in facts:
            for ef in evolving_facts:
                if self._is_same_subject_and_predicate(fact, ef):
                    if ef.object_value != fact.get("object_value", ""):
                        conflicts.append(
                            {
                                "extracted_fact": fact,
                                "conflicting_fact": self._fact_to_dict(ef),
                                "conflict_type": "evolving",
                                "message": (
                                    f"可演进设定变化：{ef.subject_type}:{ef.subject_id or ''} 的 "
                                    f"{ef.predicate} 从 '{ef.object_value}' "
                                    f"变为 '{fact.get('object_value')}'"
                                ),
                            }
                        )
        return conflicts

    # ------------------------------------------------------------------
    # Step 4: 时间线校验
    # ------------------------------------------------------------------

    async def _check_timeline(self, facts: list[dict], chapter_no: int) -> list[dict]:
        """Step 4: 时间线校验。

        检查事实的时序合理性，如角色在死亡后出场、物品在被销毁后使用。
        通过查找已有的终态事实（死亡/销毁/消失等），检查新事实是否与之矛盾。
        """
        if not facts:
            return []

        all_facts = await self.canon_manager.get_active_facts()
        if not all_facts:
            return []

        issues: list[dict] = []

        # 构建终态事实索引: key = "subject_type:subject_id"
        terminal_facts: dict[str, CanonFact] = {}
        for ef in all_facts:
            if self._is_terminal_predicate(ef.predicate):
                key = f"{ef.subject_type}:{ef.subject_id or ''}"
                # 保留最早的终态事实
                existing = terminal_facts.get(key)
                if existing is None or (
                    ef.source_chapter_no is not None
                    and existing.source_chapter_no is not None
                    and ef.source_chapter_no < existing.source_chapter_no
                ):
                    terminal_facts[key] = ef

        # 检查抽取的事实是否与终态矛盾
        for fact in facts:
            key = f"{fact.get('subject_type', '')}:{fact.get('subject_id') or ''}"
            terminal = terminal_facts.get(key)
            if terminal is None:
                continue

            # 终态事实必须在当前章节之前确立
            terminal_chapter = terminal.source_chapter_no
            if terminal_chapter is None or terminal_chapter >= chapter_no:
                continue

            # 如果抽取的事实不是终态谓词，说明主体在终态后仍有活动
            if not self._is_terminal_predicate(fact.get("predicate", "")):
                issues.append(
                    {
                        "extracted_fact": fact,
                        "terminal_fact": {
                            "id": str(terminal.id),
                            "fact_type": terminal.fact_type,
                            "subject_type": terminal.subject_type,
                            "subject_id": terminal.subject_id,
                            "predicate": terminal.predicate,
                            "object_value": terminal.object_value,
                            "source_chapter_no": terminal.source_chapter_no,
                        },
                        "conflict_type": "timeline",
                        "message": (
                            f"时间线问题：{fact.get('subject_type')}:"
                            f"{fact.get('subject_id') or ''} "
                            f"在第{terminal_chapter}章已{terminal.predicate}"
                            f"（{terminal.object_value}），但在第{chapter_no}章的"
                            f"正文中有相关活动（{fact.get('predicate')}）"
                        ),
                    }
                )

        return issues

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _is_same_subject_and_predicate(fact: dict, ef: CanonFact) -> bool:
        """判断抽取事实与 canon 事实是否同 subject + predicate。"""
        return (
            ef.subject_type == fact.get("subject_type")
            and (ef.subject_id or "") == (fact.get("subject_id") or "")
            and ef.predicate == fact.get("predicate")
        )

    @staticmethod
    def _fact_to_dict(ef: CanonFact) -> dict:
        """将 CanonFact 转为可序列化的 dict。"""
        return {
            "id": str(ef.id),
            "fact_type": ef.fact_type,
            "subject_type": ef.subject_type,
            "subject_id": ef.subject_id,
            "predicate": ef.predicate,
            "object_value": ef.object_value,
            "mutability": ef.mutability,
            "source_chapter_no": ef.source_chapter_no,
            "last_confirmed_chapter_no": ef.last_confirmed_chapter_no,
        }

    def _is_terminal_predicate(self, predicate: str) -> bool:
        """判断谓词是否为终态谓词。"""
        if not predicate:
            return False
        if predicate in self._TERMINAL_PREDICATES:
            return True
        # 模糊匹配：谓词包含终态关键词
        for terminal in self._TERMINAL_PREDICATES:
            if terminal in predicate:
                return True
        return False
