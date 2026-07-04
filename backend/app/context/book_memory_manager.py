"""BookMemoryManager - 作品记忆管理。

管理 book_memory 表，提供：
- 记忆查询（get_memory）— 按 memory_type 过滤
- 记忆添加（add_memory）— 新增记忆条目
- 文风提取（extract_style_from_chapters）— 从已完成章节分析文风并存入 book_memory
- 文风提示词（get_style_prompt）— 将文风记忆组装为提示词
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context._llm import call_llm, is_llm_configured, parse_json_response
from app.db.models.chapter import Chapter, ChapterVersion, ManuscriptBlock
from app.db.models.memory import BookMemory

logger = logging.getLogger("app.context.book_memory")


class BookMemoryManager:
    """管理 book_memory 表。"""

    def __init__(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        self.db = db
        self.project_id = project_id

    # ------------------------------------------------------------------
    # 记忆查询
    # ------------------------------------------------------------------

    async def get_memory(
        self, memory_type: Optional[str] = None
    ) -> list[BookMemory]:
        """查询作品记忆，可按 memory_type 过滤。"""
        stmt = select(BookMemory).where(
            BookMemory.project_id == self.project_id,
        )
        if memory_type:
            stmt = stmt.where(BookMemory.memory_type == memory_type)
        stmt = stmt.order_by(BookMemory.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # 记忆添加
    # ------------------------------------------------------------------

    async def add_memory(
        self,
        memory_type: str,
        key: str,
        value: dict[str, Any] | str,
        source: Optional[str] = None,
    ) -> BookMemory:
        """添加记忆条目。

        Args:
            memory_type: 记忆类型（style / tone / convention / preference / lesson）
            key: 记忆键名
            value: 记忆值（dict 或 str，str 会被包装为 {"text": value}）
            source: 来源标识
        """
        if isinstance(value, str):
            value = {"text": value}

        memory = BookMemory(
            project_id=self.project_id,
            memory_type=memory_type,
            key=key,
            value=value,
            source=source,
            confidence=1.0,
        )
        self.db.add(memory)
        await self.db.flush()
        await self.db.refresh(memory)
        return memory

    # ------------------------------------------------------------------
    # 文风提取
    # ------------------------------------------------------------------

    async def extract_style_from_chapters(
        self, chapter_count: int = 5
    ) -> dict[str, Any]:
        """从已完成章节提取文风特征。

        分析已完成章节的文风，存入 book_memory。
        LLM 未配置时返回空字典。
        """
        if not is_llm_configured():
            logger.debug("LLM 未配置，跳过文风提取")
            return {}

        # 获取最近完成的章节
        stmt = (
            select(Chapter)
            .where(
                Chapter.project_id == self.project_id,
                Chapter.status.in_(["approved", "published"]),
            )
            .order_by(Chapter.chapter_no.desc())
            .limit(chapter_count)
        )
        result = await self.db.execute(stmt)
        chapters = list(result.scalars().all())
        if not chapters:
            logger.info("没有已完成的章节可供文风分析")
            return {}

        # 获取正文
        text_parts: list[str] = []
        for ch in chapters:
            content = await self._get_chapter_content(ch)
            if content:
                text_parts.append(
                    f"--- 第{ch.chapter_no}章 {ch.title} ---\n{content[:2000]}"
                )

        if not text_parts:
            return {}

        sample_text = "\n\n".join(text_parts)

        system_prompt = (
            "你是一个文学文风分析专家。请分析给定小说正文的文风特征，"
            "从以下维度提取：叙事人称、叙事时态、句式特征、用词偏好、"
            "对话风格、描写密度、节奏感、情感基调、修辞手法。"
        )

        user_prompt = f"""请分析以下小说正文的文风特征。

正文样本：
---
{sample_text[:8000]}
---

请以 JSON 格式返回文风分析结果：

```json
{{
  "narrative_perspective": "第三人称全知/第三人称限制/第一人称",
  "sentence_style": "句式特征描述",
  "vocabulary": "用词偏好描述",
  "dialogue_style": "对话风格描述",
  "description_density": "描写密度描述",
  "pacing": "节奏感描述",
  "emotional_tone": "情感基调描述",
  "rhetoric": "修辞手法描述",
  "overall_summary": "整体文风概括"
}}
```"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        raw = await call_llm(messages, temperature=0.3, max_tokens=2048)
        if raw is None:
            return {}

        style_data = parse_json_response(raw)
        if not isinstance(style_data, dict):
            logger.warning("文风分析返回非 dict: %s", type(style_data))
            return {}

        # 存入 book_memory
        chapter_nos = ",".join(str(ch.chapter_no) for ch in chapters)
        await self.add_memory(
            memory_type="style",
            key="overall_style",
            value=style_data,
            source=f"chapters:{chapter_nos}",
        )

        logger.info("文风提取完成，已存入 book_memory (来源: 第%s章)", chapter_nos)
        return style_data

    # ------------------------------------------------------------------
    # 文风提示词
    # ------------------------------------------------------------------

    async def get_style_prompt(self) -> str:
        """获取文风提示词。

        查询 book_memory 中的 style/tone/convention 记忆，组装为提示词文本。
        """
        memories = await self.get_memory(memory_type="style")
        if not memories:
            return ""

        # 字段中文标签映射
        field_labels = {
            "narrative_perspective": "叙事人称",
            "sentence_style": "句式",
            "vocabulary": "用词",
            "dialogue_style": "对话",
            "description_density": "描写密度",
            "pacing": "节奏",
            "emotional_tone": "情感基调",
            "rhetoric": "修辞",
            "overall_summary": "总体",
        }

        lines: list[str] = []
        for m in memories:
            if isinstance(m.value, dict):
                parts: list[str] = []
                for k, v in m.value.items():
                    if v:
                        label = field_labels.get(k, k)
                        parts.append(f"{label}：{v}")
                if parts:
                    lines.append(f"[{m.key}] " + "；".join(parts))
            elif isinstance(m.value, str):
                lines.append(f"[{m.key}] {m.value}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    async def _get_chapter_content(self, chapter: Chapter) -> str:
        """获取章节正文内容（优先 current_version，其次 manuscript_blocks）。"""
        if chapter.current_version_id:
            version = await self.db.get(ChapterVersion, chapter.current_version_id)
            if version and version.content:
                return version.content

        blk_stmt = (
            select(ManuscriptBlock)
            .where(ManuscriptBlock.chapter_id == chapter.id)
            .order_by(ManuscriptBlock.block_no.asc())
        )
        result = await self.db.execute(blk_stmt)
        blocks = list(result.scalars().all())
        return "\n".join(b.content for b in blocks if b.content)
