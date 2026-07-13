"""Evidence-driven post-chapter learning for autonomous novel production.

The service immediately feeds stable lessons and explicit user preferences into
BookMemory (which ContextCompiler reads on the next chapter), while risky prompt
changes remain versioned candidates until they have holdout evidence.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context.book_memory_manager import memory_governance
from app.db.models.memory import BookMemory, PlanningReflection
from app.db.models.quality import (
    HumanFeedbackEvent,
    LearningCycle,
    PromptVersion,
    QualityAssessment,
    QualityIssue,
)
from app.services.prompt_evolution import PromptEvolutionService, fixed_holdout_suite


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _compact(value: Any, limit: int = 800) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value.lower()).strip("-")
    if normalized:
        return normalized[:80]
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


class AutonomousLearningService:
    """Turn quality/feedback evidence into auditable next-chapter guidance."""

    def __init__(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        self.db = db
        self.project_id = project_id

    async def run_post_chapter_cycle(
        self,
        *,
        chapter_no: int,
        session_id: Optional[uuid.UUID] = None,
        prompt_evaluation_interval: int = 1,
    ) -> dict[str, Any]:
        key = f"post-chapter:{chapter_no}"
        existing = await self.db.scalar(
            select(LearningCycle).where(
                LearningCycle.project_id == self.project_id,
                LearningCycle.idempotency_key == key,
            )
        )
        if existing:
            return self._cycle_result(existing, reused=True)

        assessments = (
            (
                await self.db.execute(
                    select(QualityAssessment).where(
                        QualityAssessment.project_id == self.project_id,
                        QualityAssessment.chapter_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        issues = (
            (
                await self.db.execute(
                    select(QualityIssue)
                    .where(
                        QualityIssue.project_id == self.project_id,
                        QualityIssue.status.in_(["open", "fixed", "accepted"]),
                    )
                    .order_by(QualityIssue.created_at.desc())
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )
        feedback = (
            (
                await self.db.execute(
                    select(HumanFeedbackEvent).where(
                        HumanFeedbackEvent.project_id == self.project_id,
                        HumanFeedbackEvent.learning_status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )

        cycle = LearningCycle(
            project_id=self.project_id,
            idempotency_key=key,
            status="running",
            feedback_count=len(feedback),
            assessment_count=len(assessments),
            started_at=_now(),
        )
        self.db.add(cycle)
        await self.db.flush()

        learned_memories = await self._learn_issue_patterns(issues, chapter_no)
        learned_preferences = await self._learn_feedback(feedback, chapter_no)
        lessons = [*learned_memories, *learned_preferences]

        candidate_ids: list[str] = []
        # Prompt changes are generated autonomously but are not promoted without
        # enough repeated evidence and a future holdout evaluation.
        interval = max(1, min(50, int(prompt_evaluation_interval)))
        if len(lessons) >= 3 and chapter_no % interval == 0:
            candidate = await self._create_prompt_candidate(cycle, lessons)
            candidate_ids.append(str(candidate.id))

        reflection = PlanningReflection(
            project_id=self.project_id,
            session_id=session_id,
            chapter_no=chapter_no,
            reflection_type="post_chapter",
            content=(
                f"第{chapter_no}章学习周期：读取 {len(assessments)} 次质量评估、"
                f"{len(issues)} 条问题证据和 {len(feedback)} 条人工反馈；"
                f"更新 {len(lessons)} 条下一章可用规则。"
            ),
            decisions=[
                {
                    "type": "memory_update",
                    "count": len(lessons),
                    "prompt_candidate_count": len(candidate_ids),
                }
            ],
            lessons_learned=lessons,
        )
        self.db.add(reflection)

        cycle.status = "completed"
        cycle.candidate_prompt_version_ids = candidate_ids
        cycle.candidate_memory_ids = [item["memory_id"] for item in lessons]
        cycle.holdout_metrics = {
            "status": "pending" if candidate_ids else "not_required",
            "hard_constraint_regression_allowed": False,
            "minimum_quality_gain": 3,
        }
        cycle.promotion_decision = (
            "candidate_requires_holdout" if candidate_ids else "memory_applied"
        )
        cycle.completed_at = _now()
        await self.db.flush()
        return self._cycle_result(cycle, reused=False)

    async def _learn_issue_patterns(
        self,
        issues: list[QualityIssue],
        chapter_no: int,
    ) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str], list[QualityIssue]] = {}
        for issue in issues:
            groups.setdefault((issue.source, issue.category), []).append(issue)

        lessons: list[dict[str, Any]] = []
        for (source, category), group in sorted(
            groups.items(), key=lambda item: len(item[1]), reverse=True
        )[:12]:
            severe = [item for item in group if item.severity in {"critical", "high"}]
            if len(group) < 2 and not severe:
                continue
            example = _compact(group[0].description, 320)
            instruction = self._instruction_for(category, example)
            memory = await self._upsert_memory(
                memory_type="lesson",
                key=f"avoid-{_slug(source)}-{_slug(category)}",
                value={
                    "instruction": instruction,
                    "evidence_count": len(group),
                    "open_count": sum(item.status == "open" for item in group),
                    "last_chapter": chapter_no,
                    "example": example,
                    "issue_ids": [str(item.id) for item in group[:20]],
                },
                source=f"quality-ledger:{source}:{category}",
                confidence=min(0.95, 0.55 + len(group) * 0.08),
            )
            lessons.append(
                {
                    "memory_id": str(memory.id),
                    "type": "lesson",
                    "instruction": instruction,
                    "evidence_count": len(group),
                }
            )
        return lessons

    async def _learn_feedback(
        self,
        feedback: list[HumanFeedbackEvent],
        chapter_no: int,
    ) -> list[dict[str, Any]]:
        learned: list[dict[str, Any]] = []
        for event in feedback:
            action = str(event.action or "").strip().lower()
            explicit_instruction = _compact(event.instruction, 1200)
            memory_type: Optional[str] = None
            polarity: Optional[str] = None

            if action in {"approve", "edit", "revise"}:
                signal = explicit_instruction or _compact(event.edited_text, 1200)
                memory_type = "preference"
                polarity = "positive"
            elif action == "reject":
                # The rejected manuscript is evidence of what *not* to copy, never a
                # positive preference.  Without an explicit reason there is no safe
                # rule to infer, so mark the event handled without learning text.
                signal = (
                    f"避免重复用户指出的问题：{explicit_instruction}"
                    if explicit_instruction
                    else ""
                )
                memory_type = "lesson"
                polarity = "negative"
            elif action == "takeover":
                signal = (
                    f"人工接管时明确的创作约束：{explicit_instruction}"
                    if explicit_instruction
                    else ""
                )
                memory_type = "lesson"
                polarity = "neutral"
            else:
                signal = ""

            if not signal:
                event.learning_status = "skipped"
                event.processed_at = _now()
                continue
            digest = hashlib.sha256(signal.encode("utf-8")).hexdigest()[:16]
            memory = await self._upsert_memory(
                memory_type=memory_type or "lesson",
                key=f"user-{event.action}-{digest}",
                value={
                    "instruction": signal,
                    "action": event.action,
                    "polarity": polarity,
                    "last_chapter": chapter_no,
                    "feedback_id": str(event.id),
                },
                source=f"human-feedback:{event.id}",
                confidence=(
                    1.0
                    if polarity == "positive"
                    else 0.95 if polarity == "negative" else 0.8
                ),
            )
            event.learning_status = "processed"
            event.processed_at = _now()
            learned.append(
                {
                    "memory_id": str(memory.id),
                    "type": memory_type,
                    "polarity": polarity,
                    "instruction": signal,
                    "evidence_count": 1,
                }
            )
        return learned

    async def _upsert_memory(
        self,
        *,
        memory_type: str,
        key: str,
        value: dict[str, Any],
        source: str,
        confidence: float,
    ) -> BookMemory:
        memory = await self.db.scalar(
            select(BookMemory).where(
                BookMemory.project_id == self.project_id,
                BookMemory.memory_type == memory_type,
                BookMemory.key == key,
            )
        )
        if memory is None:
            memory = BookMemory(
                project_id=self.project_id,
                memory_type=memory_type,
                key=key,
            )
            self.db.add(memory)
            governance = {
                "status": "active",
                "origin": "autonomous_learning",
                "reviewed_by": "system",
                "reviewed_at": _now().isoformat(),
                "reason": "依据结构化质量证据自动激活，可由用户驳回或回滚",
                "history": [],
            }
        else:
            # A rejected/rolled-back rule must not silently reactivate merely
            # because the same evidence pattern appears again.
            governance = memory_governance(memory)
        memory.value = {**value, "_governance": governance}
        memory.source = source
        memory.confidence = confidence
        await self.db.flush()
        return memory

    async def _create_prompt_candidate(
        self,
        cycle: LearningCycle,
        lessons: list[dict[str, Any]],
    ) -> PromptVersion:
        scope_key = f"project:{self.project_id}"
        prompt_evolution = PromptEvolutionService(self.db, self.project_id)
        current_champion = await prompt_evolution.current_champion("Drafter")
        max_version = await self.db.scalar(
            select(func.max(PromptVersion.version_no)).where(
                PromptVersion.scope_key == scope_key,
                PromptVersion.agent_role == "Drafter",
            )
        )
        template = "【自动学习候选补充规则】\n" + "\n".join(
            f"- {item['instruction']}" for item in lessons[:12]
        )
        content_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()
        holdout_suite = fixed_holdout_suite()
        candidate = PromptVersion(
            project_id=self.project_id,
            parent_version_id=current_champion.id if current_champion else None,
            learning_cycle_id=cycle.id,
            scope_key=scope_key,
            idempotency_key=f"cycle:{cycle.id}:drafter",
            agent_role="Drafter",
            version_no=(max_version or 0) + 1,
            content_hash=content_hash,
            template=template,
            status="candidate",
            evaluation_metrics={
                "evidence_count": sum(item["evidence_count"] for item in lessons),
                "holdout_status": "pending",
                "gate_passed": False,
                "suite_version": holdout_suite["version"],
                "suite_hash": holdout_suite["sha256"],
                "suite_case_count": len(holdout_suite["cases"]),
                "baseline_champion_id": (
                    str(current_champion.id) if current_champion else None
                ),
                "hard_constraint_regression": None,
            },
        )
        self.db.add(candidate)
        await self.db.flush()
        return candidate

    @staticmethod
    def _instruction_for(category: str, example: str) -> str:
        name = category.lower()
        if "continu" in name or "一致" in name:
            prefix = "写作和重写前逐项核对 Canon、角色状态与时间线"
        elif "character" in name or "角色" in name:
            prefix = "保持角色动机、能力边界、说话方式和当前状态一致"
        elif "pace" in name or "节奏" in name:
            prefix = "在场景目标、冲突升级与情绪缓冲之间保持清晰节奏"
        elif "logic" in name or "逻辑" in name:
            prefix = "确保因果链完整，关键转折必须有前置动机和证据"
        else:
            prefix = f"生成后专项检查“{category}”并在交付前修正"
        return f"{prefix}。历史示例：{example}" if example else prefix

    @staticmethod
    def _cycle_result(cycle: LearningCycle, *, reused: bool) -> dict[str, Any]:
        return {
            "cycle_id": str(cycle.id),
            "status": cycle.status,
            "reused": reused,
            "feedback_count": cycle.feedback_count,
            "assessment_count": cycle.assessment_count,
            "memory_count": len(cycle.candidate_memory_ids or []),
            "prompt_candidate_count": len(cycle.candidate_prompt_version_ids or []),
            "promotion_decision": cycle.promotion_decision,
            "completed_at": cycle.completed_at.isoformat() if cycle.completed_at else None,
        }


__all__ = ["AutonomousLearningService"]
