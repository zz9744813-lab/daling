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
            hints: 创作提示字典，可包含 genre、synopsis、tone、themes、outline_text 等。
                outline_text: 用户上传的详细大纲文本，会作为重要参考。

        Returns:
            创建的 WorldBible 实例。
        """
        # 提取上传的大纲文本（如果有）
        outline_text = hints.pop("outline_text", None)

        hints_text = json.dumps(hints, ensure_ascii=False, indent=2)

        # 如果有上传的大纲，在 prompt 中加入专门的部分
        if outline_text and outline_text.strip():
            # 截取前 12000 字符（避免 prompt 过长）
            outline_snippet = outline_text[:12000]
            if len(outline_text) > 12000:
                outline_snippet += "\n\n（大纲后续部分已截断...）"

            user_prompt = WORLD_BIBLE_USER.format(hints=hints_text)
            user_prompt += (
                "\n\n【用户提供的详细大纲（请严格参考此大纲生成世界观）】\n"
                f"{outline_snippet}\n\n"
                "【重要提示】\n"
                "上述大纲是用户精心编写的详细设定，请在生成世界观圣经时严格参考：\n"
                "1. 世界名称、力量体系、势力设定等应与大纲保持一致\n"
                "2. 角色名称、关系、性格等应忠实于大纲描述\n"
                "3. 在 factions 中包含大纲中的主要势力\n"
                "4. 在 setting 中详细描述大纲中的世界背景\n"
                "5. power_system 应准确反映大纲中的修炼/力量体系\n"
            )
        else:
            user_prompt = WORLD_BIBLE_USER.format(hints=hints_text)

        try:
            content = await self._llm_json(
                system_prompt=WORLD_BIBLE_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.8,
            )
        except Exception as exc:
            logger.warning("项目 %s 世界观生成失败，使用默认结构: %s", self.project_id, exc)
            content = {}

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

        # 提取上传的大纲文本（如果有）
        outline_text = hints.pop("outline_text", None) if hints else None

        if outline_text and outline_text.strip():
            # 有上传的大纲 — 让 AI 从大纲中提取章节结构
            outline_snippet = outline_text[:15000]
            if len(outline_text) > 15000:
                outline_snippet += "\n\n（大纲后续部分已截断...）"

            user_prompt = OUTLINE_USER.format(
                volume_count=volume_count,
                chapters_per_volume=chapters_per_volume,
                world_bible=world_bible_text,
                hints=hints_text,
            )
            user_prompt += (
                "\n\n【用户提供的详细大纲（请严格按此大纲规划章节）】\n"
                f"{outline_snippet}\n\n"
                "【重要提示】\n"
                "上述大纲是用户精心编写的详细设定，请严格按照大纲中的内容规划章节：\n"
                "1. 从大纲中提取已有的卷划分和章节安排\n"
                "2. 章节标题、情节描述应忠实于大纲内容\n"
                "3. 如果大纲中有明确的卷/章结构，直接使用\n"
                "4. 如果大纲只有情节描述没有章节划分，请根据情节自然分割为章节\n"
                "5. volume_count 和 chapters_per_volume 可根据大纲实际内容调整\n"
            )
        else:
            # 没有上传大纲 — 正常生成
            user_prompt = OUTLINE_USER.format(
                volume_count=volume_count,
                chapters_per_volume=chapters_per_volume,
                world_bible=world_bible_text,
                hints=hints_text,
            )

        try:
            result = await self._llm_json(
                system_prompt=OUTLINE_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.8,
            )
        except Exception as exc:
            logger.warning("项目 %s 大纲 LLM 失败，使用默认结构: %s", self.project_id, exc)
            result = {}

        # 如果 LLM 返回空，生成默认大纲结构
        if not result.get("volumes"):
            logger.info("项目 %s LLM 返回空，生成默认大纲: %d 卷 × %d 章", self.project_id, volume_count, chapters_per_volume)
            default_volumes = []
            ch_no = 0
            for v in range(1, volume_count + 1):
                vol_chapters = []
                beats = []
                for c in range(1, chapters_per_volume + 1):
                    ch_no += 1
                    beats.append({
                        "beat_no": c,
                        "chapter_no": ch_no,
                        "title": f"第{ch_no}章",
                        "description": f"第{v}卷第{c}章，待展开的精彩情节",
                        "plot_threads": [],
                        "importance": "normal",
                    })
                    vol_chapters.append(ch_no)
                default_volumes.append({
                    "volume_no": v,
                    "title": f"第{v}卷",
                    "summary": f"第{v}卷的精彩故事",
                    "beats": beats,
                })
            result = {"volumes": default_volumes}

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
