"""Persistence service for structured quality and learning evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.quality import (
    HumanFeedbackEvent,
    QualityAssessment,
    QualityIssue,
    RevisionAttempt,
)

_ISSUE_FIELDS = {
    "severity",
    "category",
    "type",
    "description",
    "message",
    "location",
    "quote",
    "quoted_text",
    "excerpt",
    "expected",
    "actual",
    "suggestion",
    "block_id",
    "block_no",
    "source",
    "kind",
}


def _clean_text(value: Any) -> str:
    """Return stable compact text for fingerprints and database fields."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_severity(value: Any, default: str = "medium") -> str:
    text = _clean_text(value).lower()
    aliases = {
        "critical": "critical",
        "fatal": "critical",
        "blocker": "critical",
        "严重": "critical",
        "致命": "critical",
        "high": "high",
        "高": "high",
        "medium": "medium",
        "warning": "medium",
        "warn": "medium",
        "中": "medium",
        "low": "low",
        "低": "low",
        "info": "info",
        "提示": "info",
    }
    return aliases.get(text, default)


def _optional_text(value: Any) -> Optional[str]:
    text = _clean_text(value)
    return text or None


def _optional_uuid(value: Any) -> Optional[uuid.UUID]:
    if value in (None, ""):
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_score(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score):
        return None
    return max(0.0, min(100.0, score))


def fingerprint_issue(issue: dict[str, Any], source: str = "quality") -> str:
    """Create a stable SHA-256 fingerprint from the semantic issue identity."""
    payload = {
        "source": _clean_text(issue.get("source") or source).lower(),
        "category": _clean_text(issue.get("category") or issue.get("type") or "quality").lower(),
        "severity": _normalize_severity(issue.get("severity")),
        "description": _clean_text(issue.get("description") or issue.get("message")),
        "location": _clean_text(issue.get("location")),
        "quoted_text": _clean_text(
            issue.get("quoted_text") or issue.get("quote") or issue.get("excerpt")
        ),
        "expected": _clean_text(issue.get("expected")),
        "actual": _clean_text(issue.get("actual")),
        "block_no": _optional_int(issue.get("block_no")),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class QualityLedger:
    """Write-once ledger with project-scoped idempotency keys."""

    def __init__(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        self.db = db
        self.project_id = project_id

    async def record_critic_assessment(
        self,
        *,
        idempotency_key: str,
        result: dict[str, Any],
        chapter_id: Optional[uuid.UUID] = None,
        version_id: Optional[uuid.UUID] = None,
        session_id: Optional[uuid.UUID] = None,
        agent_run_id: Optional[uuid.UUID] = None,
        round_no: int = 0,
        rubric_version: str = "critic-v1",
        model_name: Optional[str] = None,
    ) -> QualityAssessment:
        """Persist normalized Critic output and its individual issues."""
        verdict = _clean_text(result.get("verdict") or "unknown").lower()
        issues = result.get("issues") if isinstance(result.get("issues"), list) else []
        return await self.record_assessment(
            idempotency_key=idempotency_key,
            assessor="Critic",
            assessment_type="critic",
            dimension_scores=(
                result.get("scores") if isinstance(result.get("scores"), dict) else {}
            ),
            overall_score=_optional_score(result.get("overall_score")),
            verdict=verdict,
            passed=verdict == "pass",
            issues=issues,
            raw_result=result,
            chapter_id=chapter_id,
            version_id=version_id,
            session_id=session_id,
            agent_run_id=agent_run_id,
            round_no=round_no,
            rubric_version=rubric_version,
            model_name=model_name,
        )

    async def record_continuity_assessment(
        self,
        *,
        idempotency_key: str,
        result: dict[str, Any],
        chapter_id: Optional[uuid.UUID] = None,
        version_id: Optional[uuid.UUID] = None,
        session_id: Optional[uuid.UUID] = None,
        agent_run_id: Optional[uuid.UUID] = None,
        round_no: int = 0,
        rubric_version: str = "continuity-v1",
        model_name: Optional[str] = None,
    ) -> QualityAssessment:
        """Persist continuity conflicts and warnings under one assessment."""
        passed = bool(result.get("passed", False))
        issues: list[dict[str, Any]] = []
        for item in result.get("conflicts", []):
            issue = dict(item) if isinstance(item, dict) else {"description": str(item)}
            issue.setdefault("severity", "high")
            issue.setdefault("category", issue.get("type", "continuity"))
            issue["source"] = "continuity"
            issue["kind"] = "conflict"
            issues.append(issue)
        for item in result.get("warnings", []):
            issue = dict(item) if isinstance(item, dict) else {"description": str(item)}
            issue.setdefault("severity", "medium")
            issue.setdefault("category", issue.get("type", "continuity"))
            issue["source"] = "continuity"
            issue["kind"] = "warning"
            issues.append(issue)

        overall_score = _optional_score(result.get("overall_score"))
        if overall_score is None:
            overall_score = 100.0 if passed else 0.0
        return await self.record_assessment(
            idempotency_key=idempotency_key,
            assessor="ContinuityGuard",
            assessment_type="continuity",
            dimension_scores={"continuity": overall_score},
            overall_score=overall_score,
            verdict="pass" if passed else "rewrite",
            passed=passed,
            issues=issues,
            raw_result=result,
            chapter_id=chapter_id,
            version_id=version_id,
            session_id=session_id,
            agent_run_id=agent_run_id,
            round_no=round_no,
            rubric_version=rubric_version,
            model_name=model_name,
        )

    async def record_assessment(
        self,
        *,
        idempotency_key: str,
        assessor: str,
        assessment_type: str,
        dimension_scores: dict[str, Any],
        overall_score: Optional[float],
        verdict: str,
        passed: bool,
        issues: list[Any],
        raw_result: dict[str, Any],
        chapter_id: Optional[uuid.UUID] = None,
        version_id: Optional[uuid.UUID] = None,
        session_id: Optional[uuid.UUID] = None,
        agent_run_id: Optional[uuid.UUID] = None,
        round_no: int = 0,
        rubric_version: str = "v1",
        model_name: Optional[str] = None,
    ) -> QualityAssessment:
        """Persist a generic assessment, returning the original on retries."""
        key = self._require_key(idempotency_key)
        existing = await self._find_assessment(key)
        if existing is not None:
            return existing

        assessment = QualityAssessment(
            project_id=self.project_id,
            session_id=session_id,
            chapter_id=chapter_id,
            version_id=version_id,
            agent_run_id=agent_run_id,
            idempotency_key=key,
            assessor=assessor,
            assessment_type=assessment_type,
            round_no=max(0, int(round_no)),
            rubric_version=rubric_version,
            model_name=model_name,
            dimension_scores={
                str(name): score
                for name, value in dimension_scores.items()
                if (score := _optional_score(value)) is not None
            },
            overall_score=_optional_score(overall_score),
            verdict=verdict,
            passed=bool(passed),
            raw_result=raw_result,
        )

        try:
            async with self.db.begin_nested():
                self.db.add(assessment)
                await self.db.flush()
                await self._add_issues(
                    assessment=assessment,
                    issues=issues,
                    source=assessment_type,
                )
                await self.db.flush()
        except IntegrityError:
            existing = await self._find_assessment(key)
            if existing is None:
                raise
            return existing
        return assessment

    async def sync_chapter_issue_statuses(
        self,
        *,
        chapter_id: uuid.UUID,
        current_version_id: uuid.UUID,
        approved: bool,
    ) -> dict[str, int]:
        """Synchronize active issue state with the chapter's immutable version.

        A newer immutable version does not prove that every prior issue was fixed,
        so historical rows are marked ``superseded`` rather than ``resolved``.
        When the exact current version passes final review, its remaining active
        blockers are then marked ``resolved``.
        """
        superseded = await self.db.execute(
            update(QualityIssue)
            .execution_options(synchronize_session="fetch")
            .where(
                QualityIssue.project_id == self.project_id,
                QualityIssue.chapter_id == chapter_id,
                QualityIssue.status == "open",
                QualityIssue.version_id.is_not(None),
                QualityIssue.version_id != current_version_id,
            )
            .values(status="superseded")
        )
        reactivated = await self.db.execute(
            update(QualityIssue)
            .execution_options(synchronize_session="fetch")
            .where(
                QualityIssue.project_id == self.project_id,
                QualityIssue.chapter_id == chapter_id,
                QualityIssue.version_id == current_version_id,
                QualityIssue.status == "superseded",
            )
            .values(status="open")
        )
        resolved_count = 0
        if approved:
            resolved = await self.db.execute(
                update(QualityIssue)
                .execution_options(synchronize_session="fetch")
                .where(
                    QualityIssue.project_id == self.project_id,
                    QualityIssue.chapter_id == chapter_id,
                    QualityIssue.version_id == current_version_id,
                    QualityIssue.status == "open",
                )
                .values(status="resolved")
            )
            resolved_count = int(resolved.rowcount or 0)
        return {
            "superseded": int(superseded.rowcount or 0),
            "reactivated": int(reactivated.rowcount or 0),
            "resolved": resolved_count,
        }

    async def record_revision_attempt(
        self,
        *,
        idempotency_key: str,
        status: str,
        round_no: int = 0,
        chapter_id: Optional[uuid.UUID] = None,
        session_id: Optional[uuid.UUID] = None,
        input_version_id: Optional[uuid.UUID] = None,
        output_version_id: Optional[uuid.UUID] = None,
        trigger_issue_ids: Optional[list[Any]] = None,
        instruction_source: str = "critic",
        instruction: Optional[str] = None,
        score_before: Optional[float] = None,
        score_after: Optional[float] = None,
        diff_summary: Optional[str] = None,
        error: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> RevisionAttempt:
        """Persist one revision attempt, idempotently."""
        key = self._require_key(idempotency_key)
        existing = await self._find_revision(key)
        if existing is not None:
            return existing

        attempt = RevisionAttempt(
            project_id=self.project_id,
            session_id=session_id,
            chapter_id=chapter_id,
            input_version_id=input_version_id,
            output_version_id=output_version_id,
            idempotency_key=key,
            round_no=max(0, int(round_no)),
            status=status,
            instruction_source=instruction_source,
            instruction=instruction,
            trigger_issue_ids=[str(value) for value in (trigger_issue_ids or [])],
            score_before=_optional_score(score_before),
            score_after=_optional_score(score_after),
            diff_summary=diff_summary,
            error=error,
            extra=extra or {},
        )
        return await self._insert_idempotent(attempt, self._find_revision, key)

    async def record_feedback(
        self,
        *,
        idempotency_key: str,
        action: str,
        actor: str = "user",
        chapter_id: Optional[uuid.UUID] = None,
        version_id: Optional[uuid.UUID] = None,
        block_id: Optional[uuid.UUID] = None,
        session_id: Optional[uuid.UUID] = None,
        review_item_id: Optional[uuid.UUID] = None,
        original_text: Optional[str] = None,
        edited_text: Optional[str] = None,
        instruction: Optional[str] = None,
        rating: Optional[float] = None,
        tags: Optional[list[Any]] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> HumanFeedbackEvent:
        """Append a human signal exactly once for later learning."""
        key = self._require_key(idempotency_key)
        existing = await self._find_feedback(key)
        if existing is not None:
            return existing

        feedback = HumanFeedbackEvent(
            project_id=self.project_id,
            session_id=session_id,
            chapter_id=chapter_id,
            version_id=version_id,
            block_id=block_id,
            review_item_id=review_item_id,
            idempotency_key=key,
            action=action,
            actor=actor,
            original_text=original_text,
            edited_text=edited_text,
            instruction=instruction,
            rating=float(rating) if rating is not None else None,
            tags=tags or [],
            extra=extra or {},
            learning_status="pending",
        )
        return await self._insert_idempotent(feedback, self._find_feedback, key)

    async def _add_issues(
        self,
        *,
        assessment: QualityAssessment,
        issues: list[Any],
        source: str,
    ) -> None:
        seen: set[str] = set()
        for raw_issue in issues:
            issue = (
                dict(raw_issue)
                if isinstance(raw_issue, dict)
                else {"description": _clean_text(raw_issue)}
            )
            fingerprint = fingerprint_issue(issue, source)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            actual_source = _clean_text(issue.get("source") or source) or source
            description = _clean_text(issue.get("description") or issue.get("message"))
            extra = {key: value for key, value in issue.items() if key not in _ISSUE_FIELDS}
            kind = _clean_text(issue.get("kind")).lower()
            if kind:
                extra["kind"] = issue["kind"]
            issue_status = "open"
            if actual_source.lower() in {"continuity", "continuityguard"} and kind in {
                "warning",
                "advisory",
            }:
                issue_status = "advisory"
            self.db.add(
                QualityIssue(
                    assessment_id=assessment.id,
                    project_id=self.project_id,
                    chapter_id=assessment.chapter_id,
                    version_id=assessment.version_id,
                    block_id=_optional_uuid(issue.get("block_id")),
                    issue_fingerprint=fingerprint,
                    source=actual_source,
                    category=(
                        _clean_text(issue.get("category") or issue.get("type") or "quality")
                    ),
                    severity=_normalize_severity(issue.get("severity")),
                    block_no=_optional_int(issue.get("block_no")),
                    location=_optional_text(issue.get("location")),
                    quoted_text=_optional_text(
                        issue.get("quoted_text") or issue.get("quote") or issue.get("excerpt")
                    ),
                    description=description,
                    expected=_optional_text(issue.get("expected")),
                    actual=_optional_text(issue.get("actual")),
                    suggestion=_optional_text(issue.get("suggestion")),
                    status=issue_status,
                    extra=extra,
                )
            )

    async def _insert_idempotent(self, instance: Any, finder: Any, key: str) -> Any:
        try:
            async with self.db.begin_nested():
                self.db.add(instance)
                await self.db.flush()
        except IntegrityError:
            existing = await finder(key)
            if existing is None:
                raise
            return existing
        return instance

    async def _find_assessment(self, key: str) -> Optional[QualityAssessment]:
        return await self.db.scalar(
            select(QualityAssessment).where(
                QualityAssessment.project_id == self.project_id,
                QualityAssessment.idempotency_key == key,
            )
        )

    async def _find_revision(self, key: str) -> Optional[RevisionAttempt]:
        return await self.db.scalar(
            select(RevisionAttempt).where(
                RevisionAttempt.project_id == self.project_id,
                RevisionAttempt.idempotency_key == key,
            )
        )

    async def _find_feedback(self, key: str) -> Optional[HumanFeedbackEvent]:
        return await self.db.scalar(
            select(HumanFeedbackEvent).where(
                HumanFeedbackEvent.project_id == self.project_id,
                HumanFeedbackEvent.idempotency_key == key,
            )
        )

    @staticmethod
    def _require_key(value: str) -> str:
        key = _clean_text(value)
        if not key:
            raise ValueError("idempotency_key must not be empty")
        if len(key) > 255:
            raise ValueError("idempotency_key must be at most 255 characters")
        return key


__all__ = ["QualityLedger", "fingerprint_issue"]
