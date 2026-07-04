"""Critic Agent — 质量审查。

职责：对生成的章节正文进行多维度评分与问题诊断。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.models.chapter import ManuscriptBlock
from app.db.models.character import Character
from app.prompts.templates.critic import CRITIC_SYSTEM, CRITIC_USER

logger = logging.getLogger("app.agents.critic")


class Critic(BaseAgent):
    """审稿评论员 Agent，负责质量评分与问题诊断。"""

    agent_name = "Critic"

    async def review(
        self,
        blocks: list[ManuscriptBlock],
        chapter_plan: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """对章节正文进行质量审查。

        Args:
            blocks: ManuscriptBlock 列表。
            chapter_plan: 章节写作计划（供参考）。

        Returns:
            审查结果 dict，包含 scores, issues, overall_score, verdict。
        """
        manuscript_text = "\n\n".join(b.content for b in blocks if b.content)
        plan_text = json.dumps(chapter_plan, ensure_ascii=False, indent=2) if chapter_plan else "（无计划）"
        characters_info = await self._get_characters_info()

        user_prompt = CRITIC_USER.format(
            manuscript_text=manuscript_text,
            chapter_plan=plan_text,
            characters_info=characters_info,
        )

        result = await self._llm_json(
            system_prompt=CRITIC_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.3,
        )

        # 确保关键字段存在
        scores = result.get("scores", {})
        for key in ("plot_coherence", "character_consistency", "prose_quality", "pacing", "emotional_impact"):
            scores.setdefault(key, 70)

        issues = result.get("issues", [])
        overall_score = result.get("overall_score")
        if overall_score is None:
            # 加权平均
            overall_score = int(sum(scores.values()) / len(scores)) if scores else 0

        verdict = result.get("verdict")
        if not verdict:
            # 自动判定
            has_high = any(i.get("severity") == "high" for i in issues)
            if overall_score >= 85 and not has_high:
                verdict = "pass"
            elif overall_score >= 70:
                verdict = "revise"
            else:
                verdict = "rewrite"

        result["scores"] = scores
        result["issues"] = issues
        result["overall_score"] = overall_score
        result["verdict"] = verdict

        logger.info(
            "项目 %s 章节审查完成: 总分 %d, verdict=%s, %d 个问题",
            self.project_id, overall_score, verdict, len(issues),
        )
        return result

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
