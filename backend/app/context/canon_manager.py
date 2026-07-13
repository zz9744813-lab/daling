"""Canon 事实管理器 - 管理 canon_facts 表的 CRUD 和查询。

CanonManager 提供：
- 事实断言（assert_fact）— 自动检测冲突，immutable 冲突抛异常，soft/evolving 冲突自动取代
- 事实查询（get_active_facts / get_immutable_facts / get_evolving_facts / list_facts）
- 事实取代（supersede_fact）— 用新事实取代旧事实，维护取代链
- 事实确认（confirm_fact）— 更新 last_confirmed_chapter_no
- 冲突检测（check_conflict）— 检查新事实是否与现有事实冲突
- 事实抽取（extract_facts_from_text）— 调用 LLM 从正文抽取 SPO 三元组
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context._llm import call_llm, is_llm_configured, parse_json_response
from app.db.models.canon import CanonFact

logger = logging.getLogger("app.context.canon")


class CanonConflictError(Exception):
    """Canon 事实冲突异常（与 immutable 事实冲突时抛出）。"""

    def __init__(self, message: str, conflicting_facts: list[dict]) -> None:
        self.message = message
        self.conflicting_facts = conflicting_facts
        super().__init__(message)


class CanonManager:
    """管理 canon_facts 表的 CRUD 和查询。"""

    def __init__(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        self.db = db
        self.project_id = project_id

    # ------------------------------------------------------------------
    # 事实断言
    # ------------------------------------------------------------------

    async def assert_fact(
        self,
        fact_type: str,
        subject_type: str,
        subject_id: Optional[str],
        predicate: str,
        object_value: str,
        mutability: str = "soft",
        confidence: float = 1.0,
        source_chapter_no: Optional[int] = None,
        tags: Optional[list[str]] = None,
    ) -> CanonFact:
        """断言一条新事实。

        - 如果与现有 immutable 事实冲突，抛出 ``CanonConflictError``。
        - 如果与现有 soft/evolving 事实冲突，自动取代旧事实。
        - 无冲突则直接创建。
        """
        # 同一事实由重试、记忆恢复或人工批准重复提交时只确认来源，
        # 不制造重复 Canon。这个幂等边界对 24 小时恢复尤其重要。
        exact_stmt = select(CanonFact).where(
            CanonFact.project_id == self.project_id,
            CanonFact.status == "active",
            CanonFact.fact_type == fact_type,
            CanonFact.subject_type == subject_type,
            CanonFact.subject_id == subject_id,
            CanonFact.predicate == predicate,
            CanonFact.object_value == object_value,
        )
        exact_result = await self.db.execute(exact_stmt)
        exact = exact_result.scalar_one_or_none()
        if exact:
            if source_chapter_no is not None:
                exact.last_confirmed_chapter_no = max(
                    exact.last_confirmed_chapter_no or 0,
                    source_chapter_no,
                )
            exact.confidence = max(exact.confidence, confidence)
            await self.db.flush()
            return exact

        new_fact_data = {
            "fact_type": fact_type,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "predicate": predicate,
            "object_value": object_value,
            "mutability": mutability,
        }

        conflict = await self.check_conflict(new_fact_data)

        if conflict["has_conflict"]:
            immutable_conflicts = [
                f for f in conflict["conflicting_facts"] if f.get("mutability") == "immutable"
            ]
            if immutable_conflicts:
                raise CanonConflictError(
                    f"新事实与 {len(immutable_conflicts)} 条 immutable 事实冲突",
                    immutable_conflicts,
                )

        # 先创建新事实
        fact = CanonFact(
            project_id=self.project_id,
            fact_type=fact_type,
            subject_type=subject_type,
            subject_id=subject_id,
            predicate=predicate,
            object_value=object_value,
            mutability=mutability,
            confidence=confidence,
            source_chapter_no=source_chapter_no,
            last_confirmed_chapter_no=source_chapter_no,
            tags=tags or [],
            status="active",
        )
        self.db.add(fact)
        await self.db.flush()
        await self.db.refresh(fact)

        # 如果有 soft/evolving 冲突，将旧事实标记为 superseded 并指向新事实
        if conflict["has_conflict"] and conflict["can_supersede"]:
            for old_fact_info in conflict["conflicting_facts"]:
                try:
                    old_fact = await self.db.get(CanonFact, uuid.UUID(old_fact_info["id"]))
                    if old_fact and old_fact.status == "active":
                        old_fact.superseded_by_fact_id = fact.id
                        old_fact.status = "superseded"
                except (ValueError, TypeError):
                    logger.warning("取代事实失败: %s", old_fact_info.get("id"))
            await self.db.flush()

        return fact

    # ------------------------------------------------------------------
    # 事实查询
    # ------------------------------------------------------------------

    async def list_facts(
        self,
        status: Optional[str] = None,
        fact_type: Optional[str] = None,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        mutability: Optional[str] = None,
        offset: int = 0,
        limit: int = 200,
    ) -> list[CanonFact]:
        """查询事实列表，支持多维度过滤。"""
        stmt = select(CanonFact).where(CanonFact.project_id == self.project_id)
        if status:
            stmt = stmt.where(CanonFact.status == status)
        if fact_type:
            stmt = stmt.where(CanonFact.fact_type == fact_type)
        if subject_type:
            stmt = stmt.where(CanonFact.subject_type == subject_type)
        if subject_id:
            stmt = stmt.where(CanonFact.subject_id == subject_id)
        if mutability:
            stmt = stmt.where(CanonFact.mutability == mutability)
        stmt = stmt.order_by(CanonFact.created_at.desc()).offset(offset).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_active_facts(
        self,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        fact_type: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[CanonFact]:
        """查询所有 active 状态的事实。"""
        stmt = select(CanonFact).where(
            CanonFact.project_id == self.project_id,
            CanonFact.status == "active",
        )
        if subject_type:
            stmt = stmt.where(CanonFact.subject_type == subject_type)
        if subject_id:
            stmt = stmt.where(CanonFact.subject_id == subject_id)
        if fact_type:
            stmt = stmt.where(CanonFact.fact_type == fact_type)
        stmt = stmt.order_by(CanonFact.created_at.desc())
        result = await self.db.execute(stmt)
        facts = list(result.scalars().all())

        # tags 过滤（JSON 字段，需要 Python 侧过滤）
        if tags:
            tag_set = set(tags)
            facts = [f for f in facts if tag_set & set(f.tags or [])]
        return facts

    async def get_immutable_facts(self) -> list[CanonFact]:
        """获取所有不可变事实（active + immutable）。"""
        stmt = (
            select(CanonFact)
            .where(
                CanonFact.project_id == self.project_id,
                CanonFact.mutability == "immutable",
                CanonFact.status == "active",
            )
            .order_by(CanonFact.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_evolving_facts(self) -> list[CanonFact]:
        """获取所有可演进事实（active + soft/dynamic）。"""
        stmt = (
            select(CanonFact)
            .where(
                CanonFact.project_id == self.project_id,
                CanonFact.mutability.in_(["soft", "dynamic"]),
                CanonFact.status == "active",
            )
            .order_by(CanonFact.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # 事实取代与确认
    # ------------------------------------------------------------------

    async def supersede_fact(
        self, old_fact_id: uuid.UUID, new_fact_data: dict[str, Any]
    ) -> CanonFact:
        """用新事实取代旧事实。

        - 旧事实 ``superseded_by_fact_id`` 指向新事实 ID，``status`` 变为 ``"superseded"``。
        - 新事实 ``last_confirmed_chapter_no`` 设为旧事实的 ``source_chapter_no``。
        """
        old_fact = await self.db.get(CanonFact, old_fact_id)
        if old_fact is None:
            raise ValueError(f"事实 {old_fact_id} 不存在")
        if old_fact.project_id != self.project_id:
            raise ValueError(f"事实 {old_fact_id} 不属于当前项目")

        # 创建新事实
        new_fact = CanonFact(
            project_id=self.project_id,
            fact_type=new_fact_data.get("fact_type", old_fact.fact_type),
            subject_type=new_fact_data.get("subject_type", old_fact.subject_type),
            subject_id=new_fact_data.get("subject_id", old_fact.subject_id),
            predicate=new_fact_data.get("predicate", old_fact.predicate),
            object_value=new_fact_data.get("object_value", ""),
            mutability=new_fact_data.get("mutability", old_fact.mutability),
            confidence=new_fact_data.get("confidence", old_fact.confidence),
            source_chapter_no=new_fact_data.get("source_chapter_no"),
            last_confirmed_chapter_no=old_fact.source_chapter_no,
            tags=new_fact_data.get("tags", old_fact.tags or []),
            status="active",
        )
        self.db.add(new_fact)
        await self.db.flush()
        await self.db.refresh(new_fact)

        # 更新旧事实
        old_fact.superseded_by_fact_id = new_fact.id
        old_fact.status = "superseded"
        await self.db.flush()
        await self.db.refresh(old_fact)

        logger.info(
            "事实取代: %s -> %s (%s:%s %s='%s' -> '%s')",
            old_fact_id,
            new_fact.id,
            old_fact.subject_type,
            old_fact.subject_id,
            old_fact.predicate,
            old_fact.object_value[:30],
            new_fact.object_value[:30],
        )
        return new_fact

    async def confirm_fact(self, fact_id: uuid.UUID, chapter_no: int) -> CanonFact:
        """确认事实在某章仍然成立，更新 ``last_confirmed_chapter_no``。"""
        fact = await self.db.get(CanonFact, fact_id)
        if fact is None:
            raise ValueError(f"事实 {fact_id} 不存在")
        if fact.project_id != self.project_id:
            raise ValueError(f"事实 {fact_id} 不属于当前项目")

        fact.last_confirmed_chapter_no = chapter_no
        await self.db.flush()
        await self.db.refresh(fact)
        return fact

    # ------------------------------------------------------------------
    # 冲突检测
    # ------------------------------------------------------------------

    async def check_conflict(self, new_fact: dict[str, Any]) -> dict:
        """检查新事实是否与现有事实冲突。

        查找同 ``subject_type`` + ``subject_id`` + ``predicate`` 的现有 active 事实：
        - 如果现有事实是 immutable 且 object_value 不同 → 冲突（不可取代）
        - 如果现有事实是 soft/evolving 且 object_value 不同 → 冲突（可取代）

        返回::

            {
                "has_conflict": bool,
                "conflicting_facts": [...],
                "can_supersede": bool,
            }
        """
        stmt = select(CanonFact).where(
            CanonFact.project_id == self.project_id,
            CanonFact.status == "active",
            CanonFact.subject_type == new_fact.get("subject_type"),
            CanonFact.predicate == new_fact.get("predicate"),
        )
        subject_id = new_fact.get("subject_id")
        if subject_id:
            stmt = stmt.where(CanonFact.subject_id == subject_id)

        result = await self.db.execute(stmt)
        existing_facts = list(result.scalars().all())

        conflicts: list[dict] = []
        can_supersede = True

        new_object = new_fact.get("object_value", "")
        for ef in existing_facts:
            if ef.object_value != new_object:
                conflicts.append(
                    {
                        "id": str(ef.id),
                        "fact_type": ef.fact_type,
                        "subject_type": ef.subject_type,
                        "subject_id": ef.subject_id,
                        "predicate": ef.predicate,
                        "object_value": ef.object_value,
                        "mutability": ef.mutability,
                        "new_object_value": new_object,
                        "source_chapter_no": ef.source_chapter_no,
                        "last_confirmed_chapter_no": ef.last_confirmed_chapter_no,
                    }
                )
                if ef.mutability == "immutable":
                    can_supersede = False

        return {
            "has_conflict": len(conflicts) > 0,
            "conflicting_facts": conflicts,
            "can_supersede": can_supersede,
        }

    # ------------------------------------------------------------------
    # 事实抽取（LLM）
    # ------------------------------------------------------------------

    async def extract_facts_from_text(
        self, text: str, chapter_no: Optional[int] = None
    ) -> list[dict]:
        """从章节正文中抽取事实断言（调用 LLM）。

        返回::

            [{fact_type, subject_type, subject_id, predicate, object_value, mutability}]

        LLM 未配置或正文为空时返回空列表。
        """
        if not is_llm_configured():
            logger.debug("LLM 未配置，跳过事实抽取")
            return []

        if not text or not text.strip():
            return []

        chapter_label = f"第{chapter_no}章" if chapter_no else "未标明章节"

        system_prompt = (
            "你是一个小说设定事实抽取器。从给定的小说正文中提取设定事实（SPO 三元组）。"
            "每条事实包含：\n"
            "- fact_type: 事实类型（setting/character/item/rule/relationship/event/location）\n"
            "- subject_type: 主体类型（如 角色/物品/地点/规则/势力/事件）\n"
            "- subject_id: 主体标识（通常为名称）\n"
            "- predicate: 谓词（如 职业/位置/状态/拥有/关系）\n"
            "- object_value: 客体值\n"
            "- mutability: 可变性（immutable=不可变核心设定 / "
            "soft=可随剧情演进 / dynamic=频繁变化的状态）\n"
            "只提取明确陈述的事实，不要推测。"
        )

        user_prompt = f"""请从以下小说正文（{chapter_label}）中提取设定事实。

正文：
---
{text[:6000]}
---

请以 JSON 数组格式返回，每个元素格式如下：

```json
[
  {{
    "fact_type": "character",
    "subject_type": "角色",
    "subject_id": "角色名",
    "predicate": "职业",
    "object_value": "剑士",
    "mutability": "soft"
  }}
]
```

注意：
1. 只提取明确在正文中陈述的事实，不要推测
2. mutability 判断标准：核心世界观规则为 immutable，角色属性/关系为 soft，临时状态为 dynamic
3. 如果没有可提取的事实，返回空数组 []"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        raw = await call_llm(messages, temperature=0.2, max_tokens=4096)
        if raw is None:
            return []

        parsed = parse_json_response(raw)
        if not isinstance(parsed, list):
            return []

        # 规范化每条事实
        facts: list[dict] = []
        valid_mutabilities = {"immutable", "soft", "dynamic"}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            predicate = item.get("predicate", "")
            object_value = item.get("object_value", "")
            if not predicate or not object_value:
                continue
            mutability = item.get("mutability", "soft")
            if mutability not in valid_mutabilities:
                mutability = "soft"
            facts.append(
                {
                    "fact_type": item.get("fact_type", "setting"),
                    "subject_type": item.get("subject_type", ""),
                    "subject_id": item.get("subject_id"),
                    "predicate": predicate,
                    "object_value": object_value,
                    "mutability": mutability,
                }
            )

        logger.info("从 %s 抽取到 %d 条事实", chapter_label, len(facts))
        return facts
