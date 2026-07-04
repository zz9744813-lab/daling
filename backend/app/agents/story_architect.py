"""StoryArchitect Agent — 世界观圣经与大纲生成。

职责：
- generate_world_bible: 根据用户提示生成世界观圣经
- generate_outline: 生成卷→节拍→章三级大纲
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.models.chapter import Chapter
from app.db.models.storyline import StorylineBeat, StorylineVolume
from app.db.models.world import WorldBible
from app.prompts.templates.outline import OUTLINE_SYSTEM, OUTLINE_USER
from app.prompts.templates.world_bible import WORLD_BIBLE_SYSTEM, WORLD_BIBLE_USER

logger = logging.getLogger("app.agents.story_architect")


class StoryArchitect(BaseAgent):
    """故事架构师 Agent，负责世界观构建与大纲规划。"""

    agent_name = "StoryArchitect"

    # ------------------------------------------------------------------
    # 世界观圣经
    # ------------------------------------------------------------------
    async def generate_world_bible(self, hints: dict[str, Any]) -> WorldBible:
        """根据用户提示生成世界观圣经。

        Args:
            hints: 创作提示字典，可包含 genre、synopsis、tone、themes 等。

        Returns:
            创建的 WorldBible 实例。
        """
        hints_text = json.dumps(hints, ensure_ascii=False, indent=2)
        user_prompt = WORLD_BIBLE_USER.format(hints=hints_text)

        content = await self._llm_json(
            system_prompt=WORLD_BIBLE_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.8,
        )

        # 生成纯文本摘要
        summary_parts = []
        summary_parts.append(f"世界名称：{content.get('world_name', '未知')}")
        summary_parts.append(f"背景设定：{content.get('setting', '')}")
        summary_parts.append(f"力量体系：{content.get('power_system', '无')}")
        summary_parts.append(f"叙事基调：{content.get('tone', '')}")
        factions = content.get("factions", [])
        if factions:
            summary_parts.append(
                "主要势力：" + "、".join(f.get("name", "") for f in factions)
            )
        summary = "\n".join(summary_parts)

        world_bible = WorldBible(
            project_id=self.project_id,
            version=1,
            content=content,
            summary=summary,
            status="draft",
            created_by_agent=self.agent_name,
        )
        self.db.add(world_bible)
        await self.db.flush()

        logger.info(
            "项目 %s 世界观圣经已生成: %s",
            self.project_id,
            content.get("world_name", "未知"),
        )
        return world_bible

    # ------------------------------------------------------------------
    # 大纲生成
    # ------------------------------------------------------------------
    async def generate_outline(
        self,
        world_bible: WorldBible,
        volume_count: int = 1,
        chapters_per_volume: int = 10,
        hints: Optional[dict[str, Any]] = None,
    ) -> list[StorylineVolume]:
        """生成卷→节拍→章三级大纲。

        Args:
            world_bible: 世界观圣经实例。
            volume_count: 卷数。
            chapters_per_volume: 每卷章节数。
            hints: 额外提示。

        Returns:
            创建的 StorylineVolume 列表。
        """
        world_bible_text = world_bible.summary or json.dumps(
            world_bible.content, ensure_ascii=False
        )
        hints_text = json.dumps(hints or {}, ensure_ascii=False, indent=2)
        user_prompt = OUTLINE_USER.format(
            volume_count=volume_count,
            chapters_per_volume=chapters_per_volume,
            world_bible=world_bible_text,
            hints=hints_text,
        )

        result = await self._llm_json(
            system_prompt=OUTLINE_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.8,
        )

        volumes_data = result.get("volumes", [])
        created_volumes: list[StorylineVolume] = []
        global_chapter_no = 0

        for vol_data in volumes_data:
            volume = StorylineVolume(
                project_id=self.project_id,
                volume_no=vol_data.get("volume_no", len(created_volumes) + 1),
                title=vol_data.get("title", f"第{len(created_volumes) + 1}卷"),
                summary=vol_data.get("summary", ""),
                target_chapters=chapters_per_volume,
                status="planned",
            )
            self.db.add(volume)
            await self.db.flush()
            created_volumes.append(volume)

            # 创建 beat 与 chapter
            beats_data = vol_data.get("beats", [])
            for beat_data in beats_data:
                global_chapter_no += 1
                beat = StorylineBeat(
                    project_id=self.project_id,
                    volume_id=volume.id,
                    beat_no=beat_data.get("beat_no", global_chapter_no),
                    chapter_no=beat_data.get("chapter_no", global_chapter_no),
                    title=beat_data.get("title", f"第{global_chapter_no}章"),
                    description=beat_data.get("description", ""),
                    plot_threads=beat_data.get("plot_threads", []),
                    importance=beat_data.get("importance", "normal"),
                    status="planned",
                )
                self.db.add(beat)

                # 同时创建 Chapter 记录
                chapter = Chapter(
                    project_id=self.project_id,
                    chapter_no=global_chapter_no,
                    title=beat_data.get("title", f"第{global_chapter_no}章"),
                    status="draft",
                    word_count=0,
                    target_words=3000,
                )
                self.db.add(chapter)

            await self.db.flush()

        logger.info(
            "项目 %s 大纲已生成: %d 卷, %d 章",
            self.project_id,
            len(created_volumes),
            global_chapter_no,
        )
        return created_volumes

    # ------------------------------------------------------------------
    # 辅助查询
    # ------------------------------------------------------------------
    async def get_latest_world_bible(self) -> Optional[WorldBible]:
        """获取项目最新的世界观圣经。"""
        stmt = (
            select(WorldBible)
            .where(WorldBible.project_id == self.project_id)
            .order_by(WorldBible.version.desc())
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()
