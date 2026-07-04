"""学习报告实验室 - 学习与反思报告。

功能：
1. 生成学习报告（统计质量分数趋势、分析常见问题模式、提取经验教训、生成改进建议）
2. 创建规划反思记录（PlanningReflection）
3. 列出反思记录
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.memory import BookMemory, PlanningReflection
from app.db.models.session import AgentRun
from app.db.models.summary import ChapterSummary
from app.pipeline.llm_client import LLMClient, get_llm_client

logger = logging.getLogger("app.learning.learning_lab")


class LearningLab:
    """学习与反思报告。

    使用方式::

        lab = LearningLab(db, project_id)
        report = await lab.generate_report(chapter_range=(1, 10))
        reflection = await lab.create_reflection(
            reflection_type="post_chapter",
            chapter_no=5,
            content="第5章节奏偏快...",
            lessons_learned=["需要更多铺垫"],
        )
    """

    def __init__(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        llm_client: Optional[LLMClient] = None,
    ):
        self.db = db
        self.project_id = project_id
        self.llm = llm_client or get_llm_client()

    # ------------------------------------------------------------------
    # 生成学习报告
    # ------------------------------------------------------------------
    async def generate_report(
        self,
        project_id: Optional[uuid.UUID] = None,
        chapter_range: Optional[tuple[int, int]] = None,
    ) -> dict[str, Any]:
        """生成学习报告。

        Args:
            project_id: 项目 ID
            chapter_range: 章节范围 (start, end)，可选

        Returns:
            学习报告::

                {
                    "period": {"start": int, "end": int},
                    "avg_score_trend": [...],
                    "common_issues": [...],
                    "lessons_learned": [...],
                    "suggestions": [...],
                }
        """
        pid = project_id or self.project_id

        # 1. 统计最近章节的质量分数趋势
        score_trend = await self._collect_score_trend(pid, chapter_range)

        # 2. 分析常见问题模式
        common_issues = await self._collect_common_issues(pid, chapter_range)

        # 3. 提取经验教训（从 BookMemory 和 PlanningReflection）
        lessons_learned = await self._collect_lessons(pid)

        # 4. 生成改进建议
        suggestions = await self._generate_suggestions(
            pid, score_trend, common_issues, lessons_learned
        )

        period = {}
        if chapter_range:
            period = {"start": chapter_range[0], "end": chapter_range[1]}
        elif score_trend:
            chapters = [s["chapter_no"] for s in score_trend]
            period = {"start": min(chapters), "end": max(chapters)}

        report = {
            "period": period,
            "avg_score_trend": score_trend,
            "common_issues": common_issues,
            "lessons_learned": lessons_learned,
            "suggestions": suggestions,
        }

        logger.info(
            "学习报告已生成: %d 章分数趋势, %d 问题模式, %d 经验教训, %d 建议",
            len(score_trend), len(common_issues),
            len(lessons_learned), len(suggestions),
        )
        return report

    # ------------------------------------------------------------------
    # 创建规划反思
    # ------------------------------------------------------------------
    async def create_reflection(
        self,
        project_id: Optional[uuid.UUID],
        reflection_type: str,
        chapter_no: Optional[int] = None,
        content: str = "",
        decisions: Optional[list[Any]] = None,
        lessons_learned: Optional[list[Any]] = None,
        session_id: Optional[uuid.UUID] = None,
    ) -> PlanningReflection:
        """创建规划反思记录。

        Args:
            project_id: 项目 ID
            reflection_type: 反思类型 (pre_chapter/post_chapter/session_end/volume_end)
            chapter_no: 关联章节号
            content: 反思内容
            decisions: 决策列表
            lessons_learned: 经验教训列表
            session_id: 关联会话 ID

        Returns:
            创建的 PlanningReflection
        """
        pid = project_id or self.project_id

        reflection = PlanningReflection(
            project_id=pid,
            session_id=session_id,
            chapter_no=chapter_no,
            reflection_type=reflection_type,
            content=content,
            decisions=decisions or [],
            lessons_learned=lessons_learned or [],
        )
        self.db.add(reflection)
        await self.db.flush()

        logger.info(
            "创建反思记录: type=%s chapter_no=%s",
            reflection_type, chapter_no,
        )
        return reflection

    # ------------------------------------------------------------------
    # 列出反思记录
    # ------------------------------------------------------------------
    async def list_reflections(
        self,
        project_id: Optional[uuid.UUID] = None,
        reflection_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[PlanningReflection]:
        """列出反思记录。"""
        pid = project_id or self.project_id
        stmt = (
            select(PlanningReflection)
            .where(PlanningReflection.project_id == pid)
            .order_by(PlanningReflection.created_at.desc())
            .limit(limit)
        )
        if reflection_type:
            stmt = stmt.where(PlanningReflection.reflection_type == reflection_type)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    async def _collect_score_trend(
        self,
        project_id: uuid.UUID,
        chapter_range: Optional[tuple[int, int]],
    ) -> list[dict[str, Any]]:
        """收集章节质量分数趋势。

        从 AgentRun.result 中提取质量分数（agent_name 包含 "reviewer" 或 "quality"）。
        """
        stmt = (
            select(AgentRun)
            .where(
                AgentRun.project_id == project_id,
                AgentRun.agent_name.in_(["quality_reviewer", "reviewer", "editor"]),
                AgentRun.status == "success",
            )
            .order_by(AgentRun.created_at.asc())
        )
        result = await self.db.execute(stmt)
        runs = result.scalars().all()

        trend = []
        for run in runs:
            r = run.result or {}
            chapter_no = r.get("chapter_no")
            score = r.get("score") or r.get("quality_score")
            if chapter_no and score is not None:
                if chapter_range and not (chapter_range[0] <= chapter_no <= chapter_range[1]):
                    continue
                trend.append({
                    "chapter_no": chapter_no,
                    "score": float(score),
                    "issues": r.get("issues", []),
                })
        return trend

    async def _collect_common_issues(
        self,
        project_id: uuid.UUID,
        chapter_range: Optional[tuple[int, int]],
    ) -> list[dict[str, Any]]:
        """收集常见问题模式。

        从 AgentRun.result 中提取问题列表，统计频次。
        """
        stmt = (
            select(AgentRun)
            .where(
                AgentRun.project_id == project_id,
                AgentRun.agent_name.in_(["quality_reviewer", "reviewer", "editor", "continuity_checker"]),
                AgentRun.status == "success",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(100)
        )
        result = await self.db.execute(stmt)
        runs = result.scalars().all()

        issue_counter: dict[str, int] = {}
        for run in runs:
            r = run.result or {}
            chapter_no = r.get("chapter_no", 0)
            if chapter_range and not (chapter_range[0] <= chapter_no <= chapter_range[1]):
                continue
            issues = r.get("issues", [])
            if isinstance(issues, list):
                for issue in issues:
                    if isinstance(issue, dict):
                        key = issue.get("type", issue.get("category", "unknown"))
                    else:
                        key = str(issue)
                    issue_counter[key] = issue_counter.get(key, 0) + 1

        # 按频次排序
        return [
            {"issue_type": k, "count": v}
            for k, v in sorted(issue_counter.items(), key=lambda x: x[1], reverse=True)
        ]

    async def _collect_lessons(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        """提取经验教训（从 BookMemory 和 PlanningReflection）。"""
        lessons: list[dict[str, Any]] = []

        # 从 BookMemory 提取
        stmt = select(BookMemory).where(
            BookMemory.project_id == project_id,
            BookMemory.memory_type == "lesson",
        )
        result = await self.db.execute(stmt)
        for mem in result.scalars().all():
            lessons.append({
                "source": "book_memory",
                "key": mem.key,
                "value": mem.value,
                "confidence": mem.confidence,
            })

        # 从 PlanningReflection 提取
        stmt = (
            select(PlanningReflection)
            .where(PlanningReflection.project_id == project_id)
            .order_by(PlanningReflection.created_at.desc())
            .limit(20)
        )
        result = await self.db.execute(stmt)
        for ref in result.scalars().all():
            for lesson in ref.lessons_learned or []:
                if isinstance(lesson, str):
                    lessons.append({
                        "source": "reflection",
                        "reflection_type": ref.reflection_type,
                        "chapter_no": ref.chapter_no,
                        "value": lesson,
                    })
                elif isinstance(lesson, dict):
                    lessons.append({
                        "source": "reflection",
                        "reflection_type": ref.reflection_type,
                        "chapter_no": ref.chapter_no,
                        **lesson,
                    })

        return lessons

    async def _generate_suggestions(
        self,
        project_id: uuid.UUID,
        score_trend: list[dict[str, Any]],
        common_issues: list[dict[str, Any]],
        lessons_learned: list[dict[str, Any]],
    ) -> list[str]:
        """生成改进建议。

        如果 LLM 可用，调用 LLM 生成建议；否则使用规则生成。
        """
        # 基于规则的默认建议
        suggestions: list[str] = []

        # 分数趋势分析
        if len(score_trend) >= 2:
            recent_scores = [s["score"] for s in score_trend[-5:]]
            avg_recent = sum(recent_scores) / len(recent_scores)
            if avg_recent < 70:
                suggestions.append("近期章节质量分数偏低，建议加强对情节连贯性和角色一致性的审查")
            elif avg_recent >= 90:
                suggestions.append("近期章节质量优秀，可考虑适当提高目标字数或增加情节复杂度")

            # 下降趋势
            if len(recent_scores) >= 3:
                if recent_scores[-1] < recent_scores[0] - 5:
                    suggestions.append("质量分数呈下降趋势，建议暂停生成并回顾最近的章节")

        # 常见问题建议
        for issue in common_issues[:3]:
            issue_type = issue.get("issue_type", "")
            count = issue.get("count", 0)
            if count >= 3:
                suggestions.append(f"问题 '{issue_type}' 频繁出现（{count}次），建议针对性优化")

        # 经验教训
        if not lessons_learned:
            suggestions.append("尚无积累的经验教训，建议在每章完成后创建反思记录")

        # LLM 增强建议
        if self.llm.is_configured and (score_trend or common_issues):
            llm_suggestions = await self._llm_suggestions(
                score_trend, common_issues, lessons_learned
            )
            if llm_suggestions:
                suggestions.extend(llm_suggestions)

        return suggestions

    async def _llm_suggestions(
        self,
        score_trend: list[dict[str, Any]],
        common_issues: list[dict[str, Any]],
        lessons_learned: list[dict[str, Any]],
    ) -> list[str]:
        """使用 LLM 生成改进建议。"""
        system = (
            "你是一个小说创作系统的学习顾问。请基于以下数据生成 3-5 条具体的改进建议。\n"
            "每条建议一行，以 '- ' 开头。只输出建议，不要输出其他内容。"
        )

        prompt_parts = []
        if score_trend:
            scores_str = ", ".join(
                f"第{s['chapter_no']}章:{s['score']}" for s in score_trend[-10:]
            )
            prompt_parts.append(f"质量分数趋势: {scores_str}")
        if common_issues:
            issues_str = "; ".join(
                f"{i['issue_type']}({i['count']}次)" for i in common_issues[:5]
            )
            prompt_parts.append(f"常见问题: {issues_str}")
        if lessons_learned:
            lessons_str = "; ".join(
                str(l.get("value", l.get("key", "")))[:50]
                for l in lessons_learned[:5]
            )
            prompt_parts.append(f"已有经验教训: {lessons_str}")

        prompt = "\n".join(prompt_parts) + "\n\n请生成改进建议:"

        resp = await self.llm.complete(prompt, system=system)
        if not resp.ok or not resp.content:
            return []

        # 解析建议（每行一条）
        suggestions = []
        for line in resp.content.strip().split("\n"):
            line = line.strip()
            if line.startswith("- "):
                suggestions.append(line[2:].strip())
            elif line and not line.startswith("#"):
                suggestions.append(line)

        return suggestions[:5]
