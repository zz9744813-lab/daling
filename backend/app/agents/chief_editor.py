"""ChiefEditor Agent — 最终审定。

职责：检查质量分数与一致性，更新章节状态，创建版本快照。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.models.chapter import Chapter, ChapterVersion, ManuscriptBlock

logger = logging.getLogger("app.agents.chief_editor")


class ChiefEditor(BaseAgent):
    """主编 Agent，负责最终审定与版本管理。"""

    agent_name = "ChiefEditor"

    async def finalize(
        self,
        chapter_id: uuid.UUID,
        critic_result: Optional[dict[str, Any]] = None,
        continuity_result: Optional[dict[str, Any]] = None,
        quality_threshold: int = 85,
    ) -> dict[str, Any]:
        """最终审定章节。

        检查 Critic 分数是否达标、ContinuityGuard 是否通过，
        更新章节状态并创建 ChapterVersion。

        Args:
            chapter_id: 章节 ID。
            critic_result: Critic 审查结果。
            continuity_result: ContinuityGuard 校验结果。
            quality_threshold: 质量阈值（默认 85）。

        Returns:
            审定结果 dict，包含 approved, final_score, notes。
        """
        # 加载章节
        chapter = await self.db.get(Chapter, chapter_id)
        if not chapter:
            return {"approved": False, "final_score": 0, "notes": "章节不存在"}

        notes: list[str] = []
        approved = True

        # 检查 Critic 分数
        final_score = 0
        if critic_result:
            final_score = critic_result.get("overall_score", 0)
            verdict = critic_result.get("verdict", "revise")
            if final_score < quality_threshold:
                approved = False
                notes.append(f"质量分数 {final_score} 低于阈值 {quality_threshold}")
            if verdict == "rewrite":
                approved = False
                notes.append("Critic 判定为需要重写")
            if verdict == "revise":
                notes.append("Critic 判定为需要修改（但分数达标）")
        else:
            notes.append("未提供 Critic 审查结果")

        # 检查 ContinuityGuard
        if continuity_result:
            if not continuity_result.get("passed", True):
                approved = False
                conflicts = continuity_result.get("conflicts", [])
                notes.append(f"一致性校验未通过，{len(conflicts)} 个冲突")
        else:
            notes.append("未提供一致性校验结果")

        # 获取最新 blocks
        stmt = (
            select(ManuscriptBlock)
            .where(ManuscriptBlock.chapter_id == chapter_id)
            .order_by(ManuscriptBlock.block_no)
        )
        result = await self.db.execute(stmt)
        blocks = result.scalars().all()
        full_text = "\n\n".join(b.content for b in blocks if b.content)
        word_count = len(full_text.replace("\n", "").replace(" ", ""))

        # 更新章节状态
        if approved:
            chapter.status = "approved"
            notes.append("章节已通过审定")
        else:
            chapter.status = "review"
            notes.append("章节需进一步修改")

        chapter.word_count = word_count

        # 创建 ChapterVersion
        # 查询当前最大版本号
        version_stmt = (
            select(ChapterVersion)
            .where(ChapterVersion.chapter_id == chapter_id)
            .order_by(ChapterVersion.version_no.desc())
            .limit(1)
        )
        version_result = await self.db.execute(version_stmt)
        latest_version = version_result.scalar_one_or_none()
        next_version_no = (latest_version.version_no + 1) if latest_version else 1

        version = ChapterVersion(
            chapter_id=chapter_id,
            version_no=next_version_no,
            content=full_text,
            word_count=word_count,
            status="approved" if approved else "draft",
            created_by_agent=self.agent_name,
        )
        self.db.add(version)
        await self.db.flush()

        # 更新章节的 current_version_id
        chapter.current_version_id = version.id
        await self.db.flush()

        logger.info(
            "项目 %s 第 %d 章审定完成: approved=%s, score=%d, version=%d",
            self.project_id, chapter.chapter_no, approved, final_score, next_version_no,
        )

        return {
            "approved": approved,
            "final_score": final_score,
            "chapter_id": str(chapter_id),
            "chapter_no": chapter.chapter_no,
            "version_no": next_version_no,
            "word_count": word_count,
            "notes": "; ".join(notes),
        }
