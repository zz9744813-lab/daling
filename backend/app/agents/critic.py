"""Critic Agent — 质量审查。

职责：对生成的章节正文进行多维度评分与问题诊断。
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Optional

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.models.chapter import ManuscriptBlock
from app.db.models.character import Character
from app.domain.errors import EmptyResultError, QualityCheckError
from app.prompts.templates.critic import CRITIC_SYSTEM, CRITIC_USER

logger = logging.getLogger("app.agents.critic")

_SEVERITY_ALIASES = {
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


def _normalize_score(value: Any, fallback: float = 0.0) -> float:
    """Coerce an arbitrary model score into the deterministic 0-100 range."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = fallback
    if not math.isfinite(score):
        score = fallback
    return round(max(0.0, min(100.0, score)), 2)


def _normalize_issue(raw_issue: Any) -> dict[str, Any]:
    """Return a stable issue object and canonical severity."""
    if isinstance(raw_issue, dict):
        issue = dict(raw_issue)
    else:
        issue = {"description": str(raw_issue)}
    severity = str(issue.get("severity", "medium")).strip().lower()
    issue["severity"] = _SEVERITY_ALIASES.get(severity, "medium")
    issue.setdefault("category", "quality")
    issue.setdefault("description", "")
    return issue


class Critic(BaseAgent):
    """审稿评论员 Agent，负责质量评分与问题诊断。"""

    agent_name = "Critic"

    async def review_texts(
        self,
        block_texts: list[dict[str, Any]],
        chapter_plan: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """对章节正文进行质量审查（接受内容快照）。

        Args:
            block_texts: block 内容快照列表 [{"content": ..., "block_type": ..., "block_no": ...}]。
            chapter_plan: 章节写作计划（供参考）。

        Returns:
            审查结果 dict，包含 scores, issues, overall_score, verdict。
        """
        manuscript_text = "\n\n".join(b["content"] for b in block_texts if b.get("content"))
        plan_payload = (
            {
                key: chapter_plan.get(key)
                for key in (
                    "chapter_no",
                    "chapter_title",
                    "overall_goal",
                    "pov",
                    "scene_list",
                    "ending_hook",
                    "_quality_repair_context",
                )
                if key in chapter_plan
            }
            if chapter_plan
            else None
        )
        plan_text = (
            json.dumps(plan_payload, ensure_ascii=False, indent=2)
            if plan_payload
            else "（无计划）"
        )
        characters_info = await self._get_characters_info()

        user_prompt = CRITIC_USER.format(
            manuscript_text=manuscript_text,
            chapter_plan=plan_text,
            characters_info=characters_info,
        )

        try:
            result = await self._llm_json(
                system_prompt=CRITIC_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.3,
            )
        except Exception as exc:
            logger.error(
                "项目 %s Critic LLM 调用失败: %s",
                self.project_id,
                exc,
            )
            raise QualityCheckError(
                "Critic LLM 调用失败",
                agent_name="critic",
                project_id=self.project_id,
                cause=exc,
            ) from exc

        # 校验：评分不能为空
        scores = result.get("scores", {})
        if not scores:
            raise EmptyResultError(
                "Critic 返回空评分",
                agent_name="critic",
                project_id=self.project_id,
            )

        raw_issues = result.get("issues", [])
        if not isinstance(raw_issues, list):
            raw_issues = []
        issues = [_normalize_issue(issue) for issue in raw_issues]
        overall_score = result.get("overall_score")
        if overall_score is None:
            raise EmptyResultError(
                "Critic 返回的评分缺少 overall_score",
                agent_name="critic",
                project_id=self.project_id,
            )

        # LLM verdict 只是非可信建议。分数和硬规则必须在代码层重新计算，
        # 防止模型在存在 high/critical 问题时错误地返回 pass。
        normalized_scores = {
            str(name): _normalize_score(value) for name, value in scores.items()
        }
        fallback_score = (
            sum(normalized_scores.values()) / len(normalized_scores)
            if normalized_scores
            else 0.0
        )
        overall_score = _normalize_score(overall_score, fallback=fallback_score)
        has_blocking = any(issue["severity"] in {"high", "critical"} for issue in issues)
        has_medium = any(issue["severity"] == "medium" for issue in issues)
        if has_blocking or overall_score < 70:
            verdict = "rewrite"
        elif has_medium or overall_score < 85:
            verdict = "revise"
        else:
            verdict = "pass"

        result["scores"] = normalized_scores
        result["issues"] = issues
        result["overall_score"] = overall_score
        result["verdict"] = verdict

        logger.info(
            "项目 %s 章节审查完成: 总分 %.1f, verdict=%s, %d 个问题",
            self.project_id,
            overall_score,
            verdict,
            len(issues),
        )
        return result

    async def review(
        self,
        blocks: list[ManuscriptBlock],
        chapter_plan: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """对章节正文进行质量审查（接受 ORM ManuscriptBlock）。"""
        block_texts = [
            {"content": b.content, "block_type": b.block_type, "block_no": b.block_no}
            for b in blocks
        ]
        return await self.review_texts(block_texts, chapter_plan)

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
