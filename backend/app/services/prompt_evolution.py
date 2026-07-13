"""Auditable prompt evolution for long-running novel production.

Candidates are evaluated on a fixed holdout suite and never become production
prompts implicitly.  Promotion and rollback are serialized per prompt scope,
while production prompt resolution keeps the project-authored prompt and the
qualified champion chain visibly separated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.project import ProjectConfig
from app.db.models.quality import LearningCycle, PromptVersion
from app.pipeline.llm_client import LLMClient, get_llm_client

logger = logging.getLogger("app.services.prompt_evolution")

HOLDOUT_SUITE_VERSION = "novel-production-v1"
HOLDOUT_EVALUATOR_VERSION = "pairwise-judge-v1"
HOLDOUT_MINIMUM_GAIN = 3.0
HOLDOUT_MINIMUM_SCORE = 75.0
HOLDOUT_MAX_CASE_REGRESSION = 2.0

# These cases are deliberately static and independent from any one learning
# cycle.  The exact case payload and its SHA-256 are persisted with every
# result, so a later audit can reproduce precisely what was evaluated.
HOLDOUT_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "canon-injury-v1",
        "title": "不可变伤势与动作连续性",
        "input": (
            "写一段 350 至 500 字的紧张潜入场景。既定事实：顾临左手刚被弩箭贯穿，"
            "无法握持任何物品；他右手提着唯一的油灯；为避免惊动守卫，他全程没有拔剑。"
            "正文不得改变或治愈这些事实。正文末尾必须原样附上："
            "【核对：左手伤；右手灯；未拔剑】"
        ),
        "criteria": "动作清楚、紧张感真实、事实始终一致，且不是机械复述设定。",
        "required_markers": ["【核对：左手伤；右手灯；未拔剑】"],
        "forbidden_phrases": ["左手握剑", "左手提灯", "伤口已经痊愈"],
    },
    {
        "id": "timeline-rain-v1",
        "title": "时间线与环境状态连续性",
        "input": (
            "写一段 350 至 500 字的追踪场景。时间固定在暴雨中的三更，城楼钟刚响三次，"
            "距离黎明至少还有两个时辰。人物可误判方向，但叙述事实不能让天空放亮或进入白昼。"
            "正文末尾必须原样附上：【核对：雨夜；三更；黎明未至】"
        ),
        "criteria": "时间推进可感知、雨夜空间清楚、因果衔接自然。",
        "required_markers": ["【核对：雨夜；三更；黎明未至】"],
        "forbidden_phrases": ["太阳已经升起", "已是正午", "晨光照亮全城"],
    },
    {
        "id": "character-oath-v1",
        "title": "角色底线与因果链",
        "input": (
            "写一段 400 至 550 字的审问场景。阿岚一贯谨慎，立过不杀俘虏的誓言，"
            "并且必须向同伴隐瞒自己的密探身份。让她从俘虏口中取得关键线索，"
            "但不能靠突然失智、公开身份或杀死俘虏解决问题。正文末尾必须原样附上："
            "【核对：阿岚谨慎；不杀俘；隐瞒身份】"
        ),
        "criteria": "选择符合人物动机，线索取得有铺垫、有代价、有可追踪因果。",
        "required_markers": ["【核对：阿岚谨慎；不杀俘；隐瞒身份】"],
        "forbidden_phrases": ["当众承认自己是密探", "杀死俘虏", "毫无理由地相信"],
    },
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fixed_holdout_suite() -> dict[str, Any]:
    """Return a fresh, immutable-by-convention snapshot of the fixed suite."""
    cases = [dict(case) for case in HOLDOUT_CASES]
    payload = {"version": HOLDOUT_SUITE_VERSION, "cases": cases}
    return {**payload, "sha256": _json_hash(payload)}


def _response_text(response: Any) -> str:
    content = str(getattr(response, "content", "") or "").strip()
    if content:
        return content
    raw = getattr(response, "raw", {}) or {}
    try:
        message = raw.get("choices", [])[0].get("message", {})
        return str(message.get("reasoning_content", "") or "").strip()
    except (AttributeError, IndexError, TypeError):
        return ""


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


def _score(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


@dataclass(slots=True)
class PromptBundle:
    """Resolved prompt text plus exact production provenance."""

    text: str
    content_hash: str
    project_prompt_present: bool
    active_version_id: Optional[uuid.UUID] = None
    active_version_no: Optional[int] = None
    provenance: list[dict[str, Any]] = field(default_factory=list)

    def audit_payload(self) -> dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "project_prompt_present": self.project_prompt_present,
            "active_version_id": (
                str(self.active_version_id) if self.active_version_id else None
            ),
            "active_version_no": self.active_version_no,
            "sources": self.provenance,
        }


class PromptEvolutionService:
    """Resolve, evaluate, promote, and roll back prompt versions safely."""

    def __init__(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.db = db
        self.project_id = project_id
        self.llm = llm_client or get_llm_client()
        self.scope_key = f"project:{project_id}"

    async def current_champion(self, agent_role: str) -> Optional[PromptVersion]:
        """Return the one production winner selected by activation recency."""
        return await self.db.scalar(
            select(PromptVersion)
            .where(
                PromptVersion.project_id == self.project_id,
                PromptVersion.scope_key == self.scope_key,
                PromptVersion.agent_role == agent_role,
                PromptVersion.status == "champion",
            )
            .order_by(
                PromptVersion.activated_at.desc(),
                PromptVersion.version_no.desc(),
            )
            .limit(1)
        )

    async def resolve_production_prompt(self, agent_role: str) -> PromptBundle:
        """Combine the authored project prompt with the current champion chain."""
        champion = await self.current_champion(agent_role)
        return await self.resolve_prompt(agent_role, version=champion)

    async def resolve_prompt(
        self,
        agent_role: str,
        *,
        version: Optional[PromptVersion] = None,
    ) -> PromptBundle:
        project_prompt, project_source = await self._project_prompt()
        chain = await self._version_chain(version, agent_role)

        parts: list[str] = []
        provenance: list[dict[str, Any]] = []
        if project_prompt:
            parts.append(f"【项目自定义提示词｜用户维护】\n{project_prompt}")
            provenance.append(project_source)

        for prompt_version in chain:
            parts.append(
                "【已评测自主进化规则｜"
                f"{prompt_version.agent_role} v{prompt_version.version_no}｜"
                f"{prompt_version.id}】\n{prompt_version.template}"
            )
            provenance.append(
                {
                    "source": "prompt_version",
                    "version_id": str(prompt_version.id),
                    "version_no": prompt_version.version_no,
                    "agent_role": prompt_version.agent_role,
                    "content_hash": prompt_version.content_hash,
                    "status": prompt_version.status,
                    "learning_cycle_id": (
                        str(prompt_version.learning_cycle_id)
                        if prompt_version.learning_cycle_id
                        else None
                    ),
                }
            )

        text = "\n\n".join(parts)
        return PromptBundle(
            text=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            project_prompt_present=bool(project_prompt),
            active_version_id=version.id if version else None,
            active_version_no=version.version_no if version else None,
            provenance=provenance,
        )

    async def evaluate_holdout(
        self,
        version_id: uuid.UUID,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Explicitly run the fixed suite with real generation and judge calls."""
        version = await self.db.get(PromptVersion, version_id)
        if version is None or version.project_id != self.project_id:
            raise LookupError("Prompt 版本不存在")
        if version.status != "candidate":
            raise ValueError("只有 candidate 状态的版本可以运行 holdout")

        existing_metrics = dict(version.evaluation_metrics or {})
        if (
            not force
            and existing_metrics.get("holdout_status") in {"passed", "failed"}
            and existing_metrics.get("suite_hash") == fixed_holdout_suite()["sha256"]
        ):
            return existing_metrics

        suite = fixed_holdout_suite()
        champion = await self.current_champion(version.agent_role)
        champion_id = champion.id if champion else None
        if version.parent_version_id != champion_id:
            metrics = self._error_metrics(
                existing_metrics,
                suite,
                status="stale",
                error=(
                    "候选基线已变化；请基于当前 champion 重新生成候选，"
                    "旧候选不会被静默重定基线"
                ),
            )
            version.evaluation_metrics = metrics
            await self._update_cycle(version, metrics)
            await self.db.flush()
            return metrics

        if not getattr(self.llm, "is_configured", False):
            metrics = self._error_metrics(
                existing_metrics,
                suite,
                status="error",
                error="LLM 未配置，holdout 未执行；不会生成占位通过结果",
            )
            version.evaluation_metrics = metrics
            await self._update_cycle(version, metrics)
            await self.db.flush()
            return metrics

        baseline_bundle = await self.resolve_prompt(version.agent_role, version=champion)
        candidate_bundle = await self.resolve_prompt(version.agent_role, version=version)
        started_at = _now()
        case_results: list[dict[str, Any]] = []
        model_names: set[str] = set()
        input_tokens = 0
        output_tokens = 0
        total_cost = 0.0

        try:
            for case in suite["cases"]:
                baseline_response = await self._generate_case(baseline_bundle.text, case)
                candidate_response = await self._generate_case(candidate_bundle.text, case)
                for response in (baseline_response, candidate_response):
                    if not getattr(response, "ok", False) or not _response_text(response):
                        raise RuntimeError(
                            str(getattr(response, "error", "") or "模型返回空文本")
                        )
                    if getattr(response, "model", ""):
                        model_names.add(str(response.model))
                    input_tokens += int(getattr(response, "input_tokens", 0) or 0)
                    output_tokens += int(getattr(response, "output_tokens", 0) or 0)
                    total_cost += float(getattr(response, "cost", 0.0) or 0.0)

                baseline_text = _response_text(baseline_response)
                candidate_text = _response_text(candidate_response)
                judge_response = await self._judge_case(case, baseline_text, candidate_text)
                if not getattr(judge_response, "ok", False) or not _response_text(judge_response):
                    raise RuntimeError(
                        str(getattr(judge_response, "error", "") or "评审模型返回空结果")
                    )
                if getattr(judge_response, "model", ""):
                    model_names.add(str(judge_response.model))
                input_tokens += int(getattr(judge_response, "input_tokens", 0) or 0)
                output_tokens += int(getattr(judge_response, "output_tokens", 0) or 0)
                total_cost += float(getattr(judge_response, "cost", 0.0) or 0.0)

                judgment = _parse_json_object(_response_text(judge_response))
                if "baseline_score" not in judgment or "candidate_score" not in judgment:
                    raise RuntimeError("holdout 评审结果不是有效 JSON 分数")

                baseline_violations = self._hard_violations(case, baseline_text)
                candidate_violations = self._hard_violations(case, candidate_text)
                baseline_violations.extend(
                    self._normalise_violations(judgment.get("baseline_hard_violations"))
                )
                candidate_violations.extend(
                    self._normalise_violations(judgment.get("candidate_hard_violations"))
                )
                case_results.append(
                    {
                        "case_id": case["id"],
                        "title": case["title"],
                        "baseline_score": _score(judgment.get("baseline_score")),
                        "candidate_score": _score(judgment.get("candidate_score")),
                        "baseline_hard_violations": sorted(set(baseline_violations)),
                        "candidate_hard_violations": sorted(set(candidate_violations)),
                        "reasoning": str(judgment.get("reasoning", ""))[:1200],
                        "baseline_output": baseline_text[:1600],
                        "candidate_output": candidate_text[:1600],
                    }
                )
        except Exception as exc:  # noqa: BLE001 - failure becomes persisted gate evidence
            logger.warning("Prompt holdout failed for %s: %s", version.id, exc)
            metrics = self._error_metrics(
                existing_metrics,
                suite,
                status="error",
                error=str(exc)[:1000],
            )
            metrics.update(
                {
                    "evaluated_at": _now().isoformat(),
                    "baseline_prompt": baseline_bundle.audit_payload(),
                    "candidate_prompt": candidate_bundle.audit_payload(),
                    "models": sorted(model_names),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": round(total_cost, 8),
                }
            )
            version.evaluation_metrics = metrics
            await self._update_cycle(version, metrics)
            await self.db.flush()
            return metrics

        baseline_average = sum(item["baseline_score"] for item in case_results) / len(
            case_results
        )
        candidate_average = sum(item["candidate_score"] for item in case_results) / len(
            case_results
        )
        quality_gain = candidate_average - baseline_average
        candidate_hard = [
            f"{item['case_id']}:{violation}"
            for item in case_results
            for violation in item["candidate_hard_violations"]
        ]
        baseline_hard_count = sum(
            len(item["baseline_hard_violations"]) for item in case_results
        )
        hard_regression = bool(candidate_hard) or any(
            len(item["candidate_hard_violations"])
            > len(item["baseline_hard_violations"])
            for item in case_results
        )
        quality_regressions = [
            item["case_id"]
            for item in case_results
            if item["candidate_score"]
            < item["baseline_score"] - HOLDOUT_MAX_CASE_REGRESSION
        ]
        gate_passed = (
            quality_gain >= HOLDOUT_MINIMUM_GAIN
            and candidate_average >= HOLDOUT_MINIMUM_SCORE
            and not hard_regression
            and not quality_regressions
        )
        previous_history = list(existing_metrics.get("evaluation_history", []))[-4:]
        if existing_metrics.get("evaluated_at"):
            previous_history.append(
                {
                    "evaluated_at": existing_metrics.get("evaluated_at"),
                    "holdout_status": existing_metrics.get("holdout_status"),
                    "quality_gain": existing_metrics.get("quality_gain"),
                    "suite_hash": existing_metrics.get("suite_hash"),
                }
            )
        metrics = {
            **existing_metrics,
            "evaluation_id": str(uuid.uuid4()),
            "holdout_status": "passed" if gate_passed else "failed",
            "gate_passed": gate_passed,
            "suite_version": suite["version"],
            "suite_hash": suite["sha256"],
            "suite_case_count": len(suite["cases"]),
            "evaluator_version": HOLDOUT_EVALUATOR_VERSION,
            "evaluated_at": _now().isoformat(),
            "evaluation_started_at": started_at.isoformat(),
            "baseline_champion_id": str(champion_id) if champion_id else None,
            "baseline_prompt": baseline_bundle.audit_payload(),
            "candidate_prompt": candidate_bundle.audit_payload(),
            "baseline_score": round(baseline_average, 2),
            "candidate_score": round(candidate_average, 2),
            "quality_gain": round(quality_gain, 2),
            "minimum_quality_gain": HOLDOUT_MINIMUM_GAIN,
            "minimum_candidate_score": HOLDOUT_MINIMUM_SCORE,
            "maximum_case_regression": HOLDOUT_MAX_CASE_REGRESSION,
            "hard_constraint_regression": hard_regression,
            "hard_constraint_violations": candidate_hard,
            "baseline_hard_violation_count": baseline_hard_count,
            "quality_regressions": quality_regressions,
            "case_results": case_results,
            "models": sorted(model_names),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": round(total_cost, 8),
            "evaluation_history": previous_history,
            "error": None,
        }
        version.evaluation_metrics = metrics
        await self._update_cycle(version, metrics)
        await self.db.flush()
        return metrics

    async def promote(self, version_id: uuid.UUID) -> tuple[PromptVersion, Optional[PromptVersion]]:
        """Atomically replace the scoped champion after all fixed gates passed."""
        versions = await self._locked_scope_versions()
        version = next((item for item in versions if item.id == version_id), None)
        if version is None:
            raise LookupError("Prompt 版本不存在")
        if version.status == "champion":
            return version, version
        if version.status != "candidate":
            raise ValueError("只有 candidate 状态的版本可以晋升")
        if not self._qualified(version):
            raise PermissionError("候选尚未通过固定 holdout 与硬约束门禁")

        champions = [item for item in versions if item.status == "champion"]
        previous = max(
            champions,
            key=lambda item: (item.activated_at or item.created_at, item.version_no),
            default=None,
        )
        now = _now()
        for champion in champions:
            champion.status = "retired"
            champion.retired_at = now

        metrics = dict(version.evaluation_metrics or {})
        metrics["promotion"] = {
            "promoted_at": now.isoformat(),
            "previous_champion_id": str(previous.id) if previous else None,
            "suite_hash": metrics.get("suite_hash"),
            "evaluation_id": metrics.get("evaluation_id"),
        }
        version.evaluation_metrics = metrics
        version.status = "champion"
        version.activated_at = now
        version.retired_at = None
        await self._mark_cycle(version, "promoted", metrics)
        await self.db.flush()
        return version, previous

    async def rollback(
        self,
        version_id: uuid.UUID,
        *,
        reason: str = "用户在学习中心回滚",
    ) -> tuple[PromptVersion, Optional[PromptVersion]]:
        """Roll back a version and restore the last qualified champion atomically."""
        versions = await self._locked_scope_versions()
        version = next((item for item in versions if item.id == version_id), None)
        if version is None:
            raise LookupError("Prompt 版本不存在")

        restored: Optional[PromptVersion] = None
        now = _now()
        if version.status == "champion":
            promotion = (version.evaluation_metrics or {}).get("promotion", {})
            previous_id = promotion.get("previous_champion_id")
            if previous_id:
                restored = next(
                    (item for item in versions if str(item.id) == str(previous_id)),
                    None,
                )
                if restored is not None and not self._qualified(restored):
                    restored = None
            if restored is None:
                eligible = [
                    item
                    for item in versions
                    if item.id != version.id
                    and item.status == "retired"
                    and self._qualified(item)
                ]
                restored = max(
                    eligible,
                    key=lambda item: (item.activated_at or item.created_at, item.version_no),
                    default=None,
                )

            # Defensive repair: rollback always leaves at most one champion.
            for item in versions:
                if item.status == "champion":
                    item.status = "retired"
                    item.retired_at = now
            if restored is not None:
                restored.status = "champion"
                restored.activated_at = now
                restored.retired_at = None

        version.status = "rolled_back"
        version.retired_at = now
        metrics = dict(version.evaluation_metrics or {})
        metrics["rollback"] = {
            "rolled_back_at": now.isoformat(),
            "reason": reason,
            "restored_champion_id": str(restored.id) if restored else None,
        }
        version.evaluation_metrics = metrics
        await self._mark_cycle(version, "rolled_back", metrics, rollback_reason=reason)
        await self.db.flush()
        return version, restored

    async def _project_prompt(self) -> tuple[str, dict[str, Any]]:
        config = await self.db.scalar(
            select(ProjectConfig)
            .where(
                ProjectConfig.project_id == self.project_id,
                ProjectConfig.key == "custom_system_prompt",
            )
            .order_by(ProjectConfig.updated_at.desc(), ProjectConfig.created_at.desc())
            .limit(1)
        )
        if config is None or not config.value:
            return "", {}
        text = (
            str(config.value.get("text", ""))
            if isinstance(config.value, dict)
            else str(config.value)
        ).strip()
        source = {
            "source": "project_config",
            "config_id": str(config.id),
            "key": config.key,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "updated_at": config.updated_at.isoformat() if config.updated_at else None,
        }
        return text, source

    async def _version_chain(
        self,
        version: Optional[PromptVersion],
        agent_role: str,
    ) -> list[PromptVersion]:
        if version is None:
            return []
        chain: list[PromptVersion] = []
        seen: set[uuid.UUID] = set()
        current: Optional[PromptVersion] = version
        while current is not None and len(chain) < 25:
            if current.id in seen:
                logger.error("Prompt version parent cycle detected at %s", current.id)
                break
            seen.add(current.id)
            if (
                current.project_id != self.project_id
                or current.scope_key != self.scope_key
                or current.agent_role != agent_role
            ):
                break
            chain.append(current)
            current = (
                await self.db.get(PromptVersion, current.parent_version_id)
                if current.parent_version_id
                else None
            )
        chain.reverse()
        return chain

    async def _generate_case(self, prompt: str, case: dict[str, Any]) -> Any:
        system = prompt or "你是严谨的长篇小说作者，必须遵守用户给出的全部既定事实。"
        return await self.llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": str(case["input"])},
            ],
            temperature=0.0,
            max_tokens=1200,
        )

    async def _judge_case(
        self,
        case: dict[str, Any],
        baseline_text: str,
        candidate_text: str,
    ) -> Any:
        system = (
            "你是长篇小说生产系统的固定 holdout 评审器。独立按 0-100 评分，"
            "关注设定一致性、人物动机、因果、叙事质量和完成度。"
            "hard violation 只记录与题目既定事实明确冲突的内容。"
            "只输出 JSON："
            '{"baseline_score":0,"candidate_score":0,'
            '"baseline_hard_violations":[],"candidate_hard_violations":[],'
            '"reasoning":""}'
        )
        prompt = (
            f"CASE_ID: {case['id']}\n"
            f"任务：{case['input']}\n"
            f"评判标准：{case['criteria']}\n\n"
            f"=== BASELINE ===\n{baseline_text}\n\n"
            f"=== CANDIDATE ===\n{candidate_text}"
        )
        return await self.llm.judge(prompt, system=system)

    @staticmethod
    def _hard_violations(case: dict[str, Any], output: str) -> list[str]:
        violations = [
            f"missing_marker:{marker}"
            for marker in case.get("required_markers", [])
            if marker not in output
        ]
        violations.extend(
            f"forbidden_phrase:{phrase}"
            for phrase in case.get("forbidden_phrases", [])
            if phrase in output
        )
        return violations

    @staticmethod
    def _normalise_violations(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:300] for item in value if str(item).strip()]

    @staticmethod
    def _qualified(version: PromptVersion) -> bool:
        metrics = version.evaluation_metrics or {}
        suite = fixed_holdout_suite()
        return bool(
            metrics.get("holdout_status") == "passed"
            and metrics.get("gate_passed") is True
            and metrics.get("suite_hash") == suite["sha256"]
            and metrics.get("evaluator_version") == HOLDOUT_EVALUATOR_VERSION
            and bool(metrics.get("evaluation_id"))
            and bool(metrics.get("evaluated_at"))
            and float(metrics.get("quality_gain", 0) or 0) >= HOLDOUT_MINIMUM_GAIN
            and not metrics.get("hard_constraint_regression", True)
            and not metrics.get("quality_regressions", ["missing"])
        )

    async def _locked_scope_versions(self) -> list[PromptVersion]:
        result = await self.db.execute(
            select(PromptVersion)
            .where(
                PromptVersion.project_id == self.project_id,
                PromptVersion.scope_key == self.scope_key,
            )
            .order_by(PromptVersion.version_no.asc())
            .with_for_update()
        )
        return list(result.scalars().all())

    @staticmethod
    def _error_metrics(
        existing: dict[str, Any],
        suite: dict[str, Any],
        *,
        status: str,
        error: str,
    ) -> dict[str, Any]:
        return {
            **existing,
            "holdout_status": status,
            "gate_passed": False,
            "suite_version": suite["version"],
            "suite_hash": suite["sha256"],
            "suite_case_count": len(suite["cases"]),
            "evaluator_version": HOLDOUT_EVALUATOR_VERSION,
            "hard_constraint_regression": True,
            "error": error,
        }

    async def _update_cycle(
        self,
        version: PromptVersion,
        metrics: dict[str, Any],
    ) -> None:
        decision = {
            "passed": "holdout_passed_requires_manual_promotion",
            "failed": "holdout_failed",
            "stale": "candidate_stale",
            "error": "holdout_error",
        }.get(str(metrics.get("holdout_status")), "candidate_requires_holdout")
        await self._mark_cycle(version, decision, metrics)

    async def _mark_cycle(
        self,
        version: PromptVersion,
        decision: str,
        metrics: dict[str, Any],
        *,
        rollback_reason: Optional[str] = None,
    ) -> None:
        if not version.learning_cycle_id:
            return
        cycle = await self.db.get(LearningCycle, version.learning_cycle_id)
        if cycle is None or cycle.project_id != self.project_id:
            return
        cycle.holdout_metrics = {
            "prompt_version_id": str(version.id),
            "holdout_status": metrics.get("holdout_status"),
            "gate_passed": metrics.get("gate_passed", False),
            "suite_version": metrics.get("suite_version"),
            "suite_hash": metrics.get("suite_hash"),
            "baseline_score": metrics.get("baseline_score"),
            "candidate_score": metrics.get("candidate_score"),
            "quality_gain": metrics.get("quality_gain"),
            "hard_constraint_regression": metrics.get(
                "hard_constraint_regression", True
            ),
            "quality_regressions": metrics.get("quality_regressions", []),
            "evaluated_at": metrics.get("evaluated_at"),
            "error": metrics.get("error"),
        }
        cycle.promotion_decision = decision
        if rollback_reason is not None:
            cycle.rollback_reason = rollback_reason


__all__ = [
    "HOLDOUT_CASES",
    "HOLDOUT_EVALUATOR_VERSION",
    "HOLDOUT_MINIMUM_GAIN",
    "HOLDOUT_SUITE_VERSION",
    "PromptBundle",
    "PromptEvolutionService",
    "fixed_holdout_suite",
]
