"""StoryArchitect Agent — 世界观圣经与大纲生成。

职责：
- generate_world_bible: 根据用户提示生成世界观圣经
- generate_outline: 生成卷→节拍→章三级大纲
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from sqlalchemy import delete, func, select

from app.agents.base import BaseAgent
from app.db.models.chapter import Chapter
from app.db.models.storyline import StorylineBeat, StorylineVolume
from app.db.models.world import WorldBible
from app.domain.errors import EmptyResultError
from app.prompts.templates.outline import OUTLINE_SYSTEM, OUTLINE_USER
from app.prompts.templates.world_bible import WORLD_BIBLE_SYSTEM, WORLD_BIBLE_USER

logger = logging.getLogger("app.agents.story_architect")

_OUTLINE_HEADING_RE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s+.+|第[0-9一二三四五六七八九十百千万零〇两]+[卷部篇幕章节回集].*|"
    r"[卷部篇幕章节回集][0-9一二三四五六七八九十百千万零〇两]+.*|序章.*|楔子.*|终章.*|尾声.*|后记.*)$"
)


def _compact_outline(text: str, max_chars: int = 30_000) -> str:
    """Build a deterministic whole-document view without discarding the ending.

    Uploaded outlines are frequently much longer than one model prompt.  A plain
    prefix slice silently loses late-volume reveals and ending constraints, which
    is especially damaging for long-form fiction.  This view keeps exact head and
    tail regions and distributes the middle budget over headings across the whole
    document.  Documents without headings receive three evenly spaced windows.
    """
    source = str(text or "")
    if len(source) <= max_chars:
        return source
    if max_chars <= 0:
        return ""
    if max_chars < 800:
        head_size = max_chars // 2
        marker = "\n…\n"
        tail_size = max(0, max_chars - head_size - len(marker))
        return source[:head_size] + marker + source[-tail_size:]

    header = f"【大纲全篇压缩视图｜原文 {len(source)} 字符｜已保留开头、全篇结构采样与结尾】\n"
    middle_marker = "\n\n……【中段卷章与情节采样】……\n\n"
    tail_marker = "\n\n……【大纲结尾原文保留区】……\n\n"
    content_budget = max_chars - len(header) - len(middle_marker) - len(tail_marker)
    head_budget = content_budget // 4
    tail_budget = content_budget // 4
    middle_budget = content_budget - head_budget - tail_budget
    head = source[:head_budget]
    tail = source[-tail_budget:]

    middle_start = head_budget
    middle_end = len(source) - tail_budget
    heading_positions = [
        match.start()
        for match in _OUTLINE_HEADING_RE.finditer(source)
        if middle_start <= match.start() < middle_end
    ]

    if heading_positions:
        sample_count = min(
            len(heading_positions),
            max(1, min(32, middle_budget // 700)),
        )
        if sample_count == 1:
            selected = [heading_positions[len(heading_positions) // 2]]
        else:
            selected = [
                heading_positions[round(index * (len(heading_positions) - 1) / (sample_count - 1))]
                for index in range(sample_count)
            ]
    else:
        span = max(1, middle_end - middle_start)
        selected = [middle_start + round(span * fraction) for fraction in (0.2, 0.5, 0.8)]

    sample_separator = "\n\n…\n\n"
    excerpt_budget = max(
        1,
        (middle_budget - len(sample_separator) * max(0, len(selected) - 1))
        // len(selected),
    )
    excerpts: list[str] = []
    seen_starts: set[int] = set()
    for position in selected:
        line_start = source.rfind("\n", middle_start, position) + 1
        line_start = max(middle_start, line_start)
        if line_start in seen_starts:
            continue
        seen_starts.add(line_start)
        excerpt = source[line_start : min(middle_end, line_start + excerpt_budget)].strip()
        if excerpt:
            excerpts.append(excerpt)
    middle = sample_separator.join(excerpts)[:middle_budget]

    compacted = header + head + middle_marker + middle + tail_marker + tail
    # The budget arithmetic above is exact; this guard protects future label edits.
    if len(compacted) > max_chars:
        overflow = len(compacted) - max_chars
        middle = middle[: max(0, len(middle) - overflow)]
        compacted = header + head + middle_marker + middle + tail_marker + tail
    return compacted


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
        hints = dict(hints)
        # 提取上传的大纲文本（如果有）
        outline_text = hints.pop("outline_text", None)
        # 提取用户创作灵感（从零开始模式时用户填写的文本）
        creative_prompt = hints.pop("creative_prompt", None)

        hints_text = json.dumps(hints, ensure_ascii=False, indent=2)

        # 如果有上传的大纲，在 prompt 中加入专门的部分
        if outline_text and outline_text.strip():
            outline_snippet = _compact_outline(outline_text)

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

        # 如果有用户创作灵感，追加到 user_prompt 末尾
        if creative_prompt and creative_prompt.strip():
            user_prompt += (
                f"\n\n【用户创作灵感】\n{creative_prompt}\n\n"
                "请在生成世界观时充分参考用户的创作意图。"
            )

        content = await self._llm_json(
            system_prompt=WORLD_BIBLE_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.8,
        )
        if not content.get("world_name") or not content.get("setting"):
            raise EmptyResultError(
                "世界观结果缺少 world_name 或 setting，拒绝保存占位设定",
                agent_name=self.agent_name,
                project_id=str(self.project_id),
            )

        # 生成纯文本摘要
        summary_parts = []
        summary_parts.append(f"世界名称：{content.get('world_name', '未知')}")
        summary_parts.append(f"背景设定：{content.get('setting', '')}")
        summary_parts.append(f"力量体系：{content.get('power_system', '无')}")
        summary_parts.append(f"叙事基调：{content.get('tone', '')}")
        factions = content.get("factions", [])
        if factions:
            summary_parts.append("主要势力：" + "、".join(f.get("name", "") for f in factions))
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
        *,
        replace_existing: bool = False,
    ) -> list[StorylineVolume]:
        """生成卷→节拍→章三级大纲。

        Args:
            world_bible: 世界观圣经实例。
            volume_count: 卷数。
            chapters_per_volume: 每卷章节数。
            hints: 额外提示。
            replace_existing: 在新结构通过模型校验后，原子替换现有的未开写结构。

        Returns:
            创建的 StorylineVolume 列表。
        """
        hints = dict(hints or {})
        world_bible_text = world_bible.summary or json.dumps(
            world_bible.content, ensure_ascii=False
        )
        # 提取上传的大纲文本（如果有）
        outline_text = hints.pop("outline_text", None)
        hints_text = json.dumps(hints, ensure_ascii=False, indent=2)

        if outline_text and outline_text.strip():
            # 有上传的大纲 — 让 AI 从大纲中提取章节结构
            outline_snippet = _compact_outline(outline_text)

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

        result = await self._llm_json(
            system_prompt=OUTLINE_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.8,
        )
        if not isinstance(result.get("volumes"), list) or not result["volumes"]:
            raise EmptyResultError(
                "大纲结果没有有效 volumes，拒绝生成占位章节",
                agent_name=self.agent_name,
                project_id=str(self.project_id),
            )

        volumes_data = result.get("volumes", [])
        created_volumes: list[StorylineVolume] = []

        # 模型输出先完成并通过上面的结构校验，之后才进入数据库 savepoint。
        # 因此模型超时/空结果不会触碰旧大纲；删除或写入任一步失败时，
        # savepoint 会恢复整套旧结构，避免项目落入“半删半建”状态。
        structure_tx = await self.db.begin_nested()
        try:
            if replace_existing:
                await self.db.execute(
                    delete(StorylineBeat).where(StorylineBeat.project_id == self.project_id)
                )
                await self.db.execute(
                    delete(Chapter).where(Chapter.project_id == self.project_id)
                )
                await self.db.execute(
                    delete(StorylineVolume).where(
                        StorylineVolume.project_id == self.project_id
                    )
                )
                await self.db.flush()
                global_chapter_no = 0
                volume_offset = 0
            else:
                existing_chapter = await self.db.execute(
                    select(func.max(Chapter.chapter_no)).where(
                        Chapter.project_id == self.project_id
                    )
                )
                existing_volume = await self.db.execute(
                    select(func.max(StorylineVolume.volume_no)).where(
                        StorylineVolume.project_id == self.project_id
                    )
                )
                global_chapter_no = int(existing_chapter.scalar_one_or_none() or 0)
                volume_offset = int(existing_volume.scalar_one_or_none() or 0)

            for volume_index, vol_data in enumerate(volumes_data, start=1):
                volume_no = volume_offset + volume_index
                beats_data = vol_data.get("beats", [])
                volume = StorylineVolume(
                    project_id=self.project_id,
                    volume_no=volume_no,
                    title=vol_data.get("title", f"第{volume_no}卷"),
                    summary=vol_data.get("summary", ""),
                    target_chapters=len(beats_data) or chapters_per_volume,
                    status="planned",
                )
                self.db.add(volume)
                await self.db.flush()
                created_volumes.append(volume)

                # 创建 beat 与 chapter
                for beat_index, beat_data in enumerate(beats_data, start=1):
                    global_chapter_no += 1
                    beat = StorylineBeat(
                        project_id=self.project_id,
                        volume_id=volume.id,
                        beat_no=beat_index,
                        chapter_no=global_chapter_no,
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
            await structure_tx.commit()
        except BaseException:
            await structure_tx.rollback()
            raise

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
