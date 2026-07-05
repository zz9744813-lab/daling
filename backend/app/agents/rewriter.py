"""Rewriter Agent — 根据审查意见修改正文。

职责：按 issue 定位段落，调用 LLM 重写有问题的段落。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.models.chapter import ManuscriptBlock
from app.db.models.character import Character
from app.prompts.templates.rewriter import REWRITER_SYSTEM, REWRITER_USER

logger = logging.getLogger("app.agents.rewriter")


class Rewriter(BaseAgent):
    """修订编辑 Agent，负责根据审查意见修改正文。"""

    agent_name = "Rewriter"

    async def rewrite_texts(
        self,
        block_texts: list[dict[str, Any]],
        issues: list[dict[str, Any]],
        plan: Optional[dict[str, Any]] = None,
        chapter_id: Optional[uuid.UUID] = None,
    ) -> list[ManuscriptBlock]:
        """根据审查意见重写有问题的段落（接受内容快照）。"""
        if not issues:
            return []
        manuscript_text = "\n\n".join(
            b["content"] for b in block_texts if b.get("content")
        )
        issues_text = json.dumps(issues, ensure_ascii=False, indent=2)
        plan_text = json.dumps(plan, ensure_ascii=False, indent=2) if plan else "（无计划）"
        characters_info = await self._get_characters_info()

        user_prompt = REWRITER_USER.format(
            manuscript_text=manuscript_text,
            issues=issues_text,
            chapter_plan=plan_text,
            characters_info=characters_info,
        )

        try:
            revised_text = await self._llm_complete(
                system_prompt=REWRITER_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.7,
                max_tokens=8192,
            )
        except Exception as exc:
            logger.warning("项目 %s Rewriter LLM 调用失败: %s", self.project_id, exc)
            return []

        new_blocks = self._split_into_blocks(revised_text, chapter_id)
        logger.info(
            "项目 %s 正文修订完成: %d 个 block → %d 个 block",
            self.project_id, len(block_texts), len(new_blocks),
        )
        return new_blocks

    async def rewrite(
        self,
        blocks: list[ManuscriptBlock],
        issues: list[dict[str, Any]],
        plan: Optional[dict[str, Any]] = None,
        chapter_id: Optional[uuid.UUID] = None,
    ) -> list[ManuscriptBlock]:
        """根据审查意见重写有问题的段落。

        Args:
            blocks: 原始 ManuscriptBlock 列表。
            issues: 审查问题列表。
            plan: 章节写作计划（供参考）。
            chapter_id: 章节 ID。

        Returns:
            修改后的 ManuscriptBlock 列表。
        """
        if not issues:
            return blocks

        # 提取内容文本（避免 ORM 状态问题）
        try:
            manuscript_text = "\n\n".join(b.content for b in blocks if b.content)
        except Exception:
            # blocks 可能已被删除，使用 block_texts
            manuscript_text = "\n\n".join(
                b.get("content", "") for b in blocks if isinstance(b, dict) and b.get("content")
            )
        issues_text = json.dumps(issues, ensure_ascii=False, indent=2)
        plan_text = json.dumps(plan, ensure_ascii=False, indent=2) if plan else "（无计划）"
        characters_info = await self._get_characters_info()

        user_prompt = REWRITER_USER.format(
            manuscript_text=manuscript_text,
            issues=issues_text,
            chapter_plan=plan_text,
            characters_info=characters_info,
        )

        revised_text = await self._llm_complete(
            system_prompt=REWRITER_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.7,
            max_tokens=8192,
        )

        # 重新切分为 blocks
        new_blocks = self._split_into_blocks(revised_text, chapter_id)

        logger.info(
            "项目 %s 正文修订完成: %d 个 block → %d 个 block",
            self.project_id, len(blocks), len(new_blocks),
        )
        return new_blocks

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _split_into_blocks(
        self, text: str, chapter_id: Optional[uuid.UUID]
    ) -> list[ManuscriptBlock]:
        """将修订后正文切分为 ManuscriptBlock 列表。"""
        blocks: list[ManuscriptBlock] = []
        block_no = 0
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        for para in paragraphs:
            block_no += 1
            if para in ("***", "---", "* * *") or para.startswith("***") or para.startswith("---"):
                block_type = "scene_break"
                content = ""
            elif any(
                marker in para
                for marker in ['「', '」', '"', '"', '"', '"', "——", "说：", "道："]
            ) and len(para) < 500:
                block_type = "dialogue"
                content = para
            else:
                block_type = "paragraph"
                content = para

            blocks.append(
                ManuscriptBlock(
                    chapter_id=chapter_id,
                    block_no=block_no,
                    content=content,
                    block_type=block_type,
                )
            )

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

    async def _get_characters_info(self) -> str:
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
