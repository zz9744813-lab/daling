"""PipelineOrchestrator — 单章/多章生成流程编排器。

编排单章生成的完整 Pipeline：
1. ChapterPlanner.plan_chapter()   — 生成写作计划
2. Drafter.draft_chapter()         — 起草正文
3. Critic.review()                 — 质量审查
4. ContinuityGuard.check()         — 一致性校验
5. if issues: Rewriter.rewrite() → goto 3（最多重试 2 次）
6. ChiefEditor.finalize()          — 最终审定
7. MemoryKeeper.update_state()     — 状态更新
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chapter_planner import ChapterPlanner
from app.agents.chief_editor import ChiefEditor
from app.agents.continuity_guard import ContinuityGuard
from app.agents.critic import Critic
from app.agents.drafter import Drafter
from app.agents.memory_keeper import MemoryKeeper
from app.agents.rewriter import Rewriter
from app.agents.story_architect import StoryArchitect
from app.context.compiler import ContextCompiler
from app.db.models.chapter import Chapter, ChapterVersion, ManuscriptBlock
from app.db.models.project import Project
from app.db.models.quality import QualityAssessment, QualityIssue
from app.db.models.session import ReviewQueueItem, WorkSession
from app.domain.errors import AgentExecutionError
from app.model_gateway import Gateway
from app.services.autonomous_learning import AutonomousLearningService
from app.services.prompt_evolution import PromptEvolutionService
from app.services.quality_ledger import QualityLedger

logger = logging.getLogger("app.pipeline.orchestrator")

# 单章重写最大次数
MAX_REWRITE_ROUNDS = 2


class PipelineOrchestrator:
    """Pipeline 编排器，协调各 Agent 完成章节生成。"""

    def __init__(
        self,
        gateway: Gateway,
        db: AsyncSession,
        project_id: uuid.UUID,
        session_id: Optional[uuid.UUID] = None,
        quality_threshold: Optional[int] = None,
        max_rewrite_rounds: int = MAX_REWRITE_ROUNDS,
        learning_interval_chapters: int = 1,
        agent_run_db_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """初始化编排器。

        Args:
            gateway: LLM Gateway 实例。
            db: 异步数据库会话。
            project_id: 项目 ID。
            session_id: 关联的 WorkSession ID。
            quality_threshold: 可选的确定性终审门槛。
            max_rewrite_rounds: 自动重写轮数上限。
        """
        self.gateway = gateway
        self.db = db
        self.project_id = project_id
        self.session_id = session_id
        self.quality_threshold = (
            max(0, min(100, quality_threshold)) if quality_threshold is not None else None
        )
        self.max_rewrite_rounds = max(0, min(5, max_rewrite_rounds))
        self.learning_interval_chapters = max(1, min(50, learning_interval_chapters))
        self.agent_run_db_factory = agent_run_db_factory
        # ``custom_system_prompt`` 保留给旧调用；正式生产按 Agent role 解析
        # “项目自定义提示词 + 当前 champion 版本链”，并保留来源审计。
        self.custom_system_prompt: str = ""
        self.production_prompts: dict[str, str] = {}
        self.production_prompt_audit: dict[str, dict[str, Any]] = {}

    async def _load_custom_prompt(self) -> None:
        """Resolve the exact, versioned production prompt for every agent role."""
        service = PromptEvolutionService(self.db, self.project_id)
        roles = (
            "StoryArchitect",
            "ChapterPlanner",
            "Drafter",
            "Critic",
            "Rewriter",
            "ContinuityGuard",
            "ChiefEditor",
            "MemoryKeeper",
        )
        self.production_prompts = {}
        self.production_prompt_audit = {}
        for role in roles:
            bundle = await service.resolve_production_prompt(role)
            self.production_prompts[role] = bundle.text
            self.production_prompt_audit[role] = bundle.audit_payload()
        self.custom_system_prompt = self.production_prompts.get("Drafter", "")

    def _prompt_for(self, agent_role: str) -> str:
        return self.production_prompts.get(agent_role, self.custom_system_prompt)

    def _prompt_audit_for(self, agent_role: str) -> dict[str, Any]:
        return self.production_prompt_audit.get(agent_role, {})

    def _configure_agent_prompt(self, agent: Any, agent_role: str) -> None:
        """Inject the role champion and persist its provenance on every AgentRun."""
        agent.custom_system_prompt = self._prompt_for(agent_role)
        agent.prompt_provenance = self._prompt_audit_for(agent_role)
        agent.agent_run_db_factory = self.agent_run_db_factory

    # ------------------------------------------------------------------
    # Phase: 生成世界观
    # ------------------------------------------------------------------
    async def generate_bible(self, hints: dict[str, Any]) -> dict[str, Any]:
        """生成世界观圣经。

        Args:
            hints: 创作提示字典。

        Returns:
            结果 dict，包含 world_bible 信息。
        """
        # 加载项目专属自定义系统提示词
        await self._load_custom_prompt()
        hints = await self._merge_project_generation_hints(hints)

        agent = StoryArchitect(
            gateway=self.gateway,
            db=self.db,
            project_id=self.project_id,
            session_id=self.session_id,
        )
        # 注入自定义系统提示词到 Agent
        self._configure_agent_prompt(agent, "StoryArchitect")
        world_bible = await agent.generate_world_bible(hints)
        return {
            "job": "generate_bible",
            "status": "completed",
            "world_bible_id": str(world_bible.id),
            "world_name": world_bible.content.get("world_name", ""),
            "summary": world_bible.summary or "",
        }

    # ------------------------------------------------------------------
    # Phase: 生成大纲
    # ------------------------------------------------------------------
    async def generate_outline(
        self,
        volume_count: int = 1,
        chapters_per_volume: int = 10,
        hints: Optional[dict[str, Any]] = None,
        *,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        """生成故事大纲。

        Args:
            volume_count: 卷数（如未指定，则从项目配置自动推算）。
            chapters_per_volume: 每卷章节数（如未指定，则自动推算）。
            hints: 额外提示。
            replace_existing: 原子替换现有的未开写结构，而不是追加卷章。

        Returns:
            结果 dict，包含大纲信息。
        """
        # 加载项目专属自定义系统提示词
        await self._load_custom_prompt()

        agent = StoryArchitect(
            gateway=self.gateway,
            db=self.db,
            project_id=self.project_id,
            session_id=self.session_id,
        )
        # 注入自定义系统提示词到 Agent
        self._configure_agent_prompt(agent, "StoryArchitect")
        world_bible = await agent.get_latest_world_bible()
        if not world_bible:
            return {
                "job": "generate_outline",
                "status": "failed",
                "error": "请先生成世界观圣经",
            }

        # 从项目 extra 读取篇幅配置，自动推算卷数与每卷章数
        proj_stmt = select(Project).where(Project.id == self.project_id)
        proj_result = await self.db.execute(proj_stmt)
        project = proj_result.scalar_one_or_none()
        extra = (project.extra or {}) if project else {}

        chapter_range = extra.get("chapter_range")
        length_label = extra.get("length_label", "")

        if chapter_range:
            rng_min = chapter_range.get("min", 10)
            rng_max = chapter_range.get("max", 50)
            target_total = (rng_min + rng_max) // 2
            # 每卷 30-50 章较合理，据此推算卷数
            if target_total <= 30:
                volume_count = 1
                chapters_per_volume = max(target_total, 5)
            elif target_total <= 100:
                volume_count = max(target_total // 30, 1)
                chapters_per_volume = target_total // volume_count
            else:
                chapters_per_volume = 40  # 每卷约 40 章
                volume_count = max(target_total // chapters_per_volume, 1)
            logger.info(
                "项目 %s 篇幅=%s, 目标 %d 章 → %d 卷 × %d 章/卷",
                self.project_id,
                length_label,
                target_total,
                volume_count,
                chapters_per_volume,
            )

        # 将完整项目蓝图与篇幅信息传给 StoryArchitect
        hints = await self._merge_project_generation_hints(hints)
        if chapter_range:
            hints["chapter_range"] = chapter_range
            hints["length_label"] = length_label
            minimum = chapter_range.get("min", 10)
            maximum = chapter_range.get("max", 50)
            hints["target_total_chapters"] = (minimum + maximum) // 2

        # 如果用户上传了详细大纲，直接传给 StoryArchitect 解析
        outline_text = extra.get("outline_text")
        if outline_text and outline_text.strip():
            hints["outline_text"] = outline_text
            logger.info(
                "项目 %s 使用上传的大纲生成章节结构 (%d 字符)",
                self.project_id,
                len(outline_text),
            )

        volumes = await agent.generate_outline(
            world_bible=world_bible,
            volume_count=volume_count,
            chapters_per_volume=chapters_per_volume,
            hints=hints,
            replace_existing=replace_existing,
        )

        return {
            "job": "generate_outline",
            "status": "completed",
            "replaced": replace_existing,
            "volume_count": len(volumes),
            "total_chapters": sum(v.target_chapters for v in volumes),
            "volumes": [
                {
                    "volume_no": v.volume_no,
                    "title": v.title,
                    "summary": v.summary or "",
                    "target_chapters": v.target_chapters,
                }
                for v in volumes
            ],
        }

    async def _merge_project_generation_hints(
        self,
        hints: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        """Merge durable onboarding decisions into every preparation call.

        Continuous production can start before the user manually opens the
        preparation screen.  In that path an empty ``hints`` dict must not
        discard the title, blueprint, conversation, or uploaded outline that
        the user already confirmed during project creation.
        """
        merged = dict(hints or {})
        project = await self.db.get(Project, self.project_id)
        if project is None:
            return merged
        extra = dict(project.extra or {})
        blueprint = extra.get("creation_blueprint")
        blueprint = dict(blueprint) if isinstance(blueprint, dict) else {}

        base_values = {
            "title": project.title,
            "genre": project.genre,
            "synopsis": project.synopsis,
        }
        for key, value in base_values.items():
            if value not in (None, "", [], {}):
                merged.setdefault(key, value)

        for key in (
            "logline",
            "premise",
            "protagonist",
            "protagonist_name",
            "protagonist_desire",
            "protagonist_goal",
            "protagonist_flaw",
            "protagonist_fear",
            "flaw",
            "fear",
            "core_conflict",
            "story_question",
            "antagonist",
            "ability",
            "ability_cost",
            "setting",
            "world_setting",
            "world_rules",
            "themes",
            "tone",
            "pacing",
            "audience",
            "audience_experience",
            "platform",
            "language",
            "pov",
            "tense",
            "ending_preference",
            "content_boundaries",
            "target_words",
            "target_chapters",
            "words_per_chapter",
            "chapter_words",
            "volume_count",
            "chapter_range",
            "length_label",
        ):
            value = blueprint.get(key, extra.get(key))
            if value not in (None, "", [], {}):
                merged.setdefault(key, value)

        outline_text = str(extra.get("outline_text") or "").strip()
        if outline_text:
            merged.setdefault("outline_text", outline_text)
            merged.setdefault("outline_filename", extra.get("outline_filename"))

        creative_prompt = str(extra.get("creative_prompt") or "").strip()
        if not creative_prompt:
            conversation = extra.get("creative_conversation")
            if isinstance(conversation, list):
                user_messages = [
                    str(item.get("content") or "").strip()
                    for item in conversation
                    if isinstance(item, dict)
                    and item.get("role") == "user"
                    and str(item.get("content") or "").strip()
                ]
                creative_prompt = "\n".join(user_messages[-12:])[:8000]
        if creative_prompt:
            merged.setdefault("creative_prompt", creative_prompt)
        return merged

    # ------------------------------------------------------------------
    # 单章生成 Pipeline
    # ------------------------------------------------------------------
    async def run_chapter(self, chapter_no: int, mode: str = "L2") -> dict[str, Any]:
        """Compute a chapter without holding SQLite's writer slot across LLM calls.

        AgentRun rows are durable audit evidence and may commit independently.
        Every canonical artifact (working blocks, immutable candidates, quality
        ledger, selected version, Canon and learned memory) is still published
        in one final savepoint which the continuous supervisor fences before its
        outer commit.
        """
        if self.agent_run_db_factory is None:
            # Request-scoped/manual pipelines retain their caller-owned atomic
            # transaction.  The staged boundary is enabled only when a durable
            # supervisor supplies the independent AgentRun session factory.
            return await self._run_chapter_legacy(chapter_no, mode)
        logger.info(
            "项目 %s 开始生成第 %d 章 (mode=%s)",
            self.project_id,
            chapter_no,
            mode,
        )
        chapter_tx = None
        chapter_id: Optional[uuid.UUID] = None

        def snapshot(blks: list[ManuscriptBlock]) -> list[dict[str, Any]]:
            return [
                {
                    "content": block.content,
                    "block_type": block.block_type,
                    "block_no": block.block_no,
                }
                for block in blks
            ]

        try:
            await self._load_custom_prompt()
            await self.db.rollback()

            existing = await self.db.scalar(
                select(Chapter)
                .where(
                    Chapter.project_id == self.project_id,
                    Chapter.chapter_no == chapter_no,
                )
                .order_by(Chapter.created_at.desc())
                .limit(1)
            )
            chapter_id = existing.id if existing is not None else uuid.uuid4()
            repair_context = (
                await self._quality_repair_context(existing) if existing is not None else ""
            )
            latest_version_no = await self.db.scalar(
                select(ChapterVersion.version_no)
                .where(ChapterVersion.chapter_id == chapter_id)
                .order_by(ChapterVersion.version_no.desc())
                .limit(1)
            )
            next_version_no = int(latest_version_no or 0) + 1
            quality_threshold = self.quality_threshold
            if quality_threshold is None:
                quality_threshold = 80 if mode in {"L2", "L3", "L4", "auto"} else 70
            persisted_incumbent = await self._best_persisted_candidate(
                chapter_id,
                quality_threshold,
            )
            await self.db.rollback()

            planner = ChapterPlanner(
                gateway=self.gateway,
                db=self.db,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            self._configure_agent_prompt(planner, "ChapterPlanner")
            planner.quality_repair_context = repair_context
            plan = await planner.plan_chapter(chapter_no)
            if repair_context:
                plan["_quality_repair_context"] = repair_context
            await self.db.rollback()

            compiled_context = await ContextCompiler(
                self.db,
                self.project_id,
                gateway=self.gateway,
            ).compile(
                chapter_no,
                plan,
                prompt_provenance=self._prompt_audit_for("Drafter"),
            )
            plan["_compiled_context"] = {
                "system_prompt": compiled_context.system_prompt,
                "context_text": compiled_context.context_text,
                "total_tokens": compiled_context.total_tokens,
                "budget_breakdown": compiled_context.budget_breakdown,
                "provenance": compiled_context.provenance,
                "prompt_provenance": compiled_context.prompt_provenance,
            }
            await self.db.rollback()

            drafter = Drafter(
                gateway=self.gateway,
                db=self.db,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            self._configure_agent_prompt(drafter, "Drafter")
            draft_blocks = await drafter.draft_chapter(plan, chapter_id=chapter_id)
            block_texts = snapshot(draft_blocks)
            await self.db.rollback()

            candidates: list[dict[str, Any]] = []

            def new_candidate(
                texts: list[dict[str, Any]],
                *,
                created_by: str,
                status: str,
            ) -> dict[str, Any]:
                nonlocal next_version_no
                content = "\n\n".join(str(item.get("content", "")) for item in texts)
                candidate = {
                    "id": uuid.uuid4(),
                    "version_no": next_version_no,
                    "content": content,
                    "word_count": len(content.replace("\n", "").replace(" ", "")),
                    "created_by": created_by,
                    "status": status,
                    "block_texts": [dict(item) for item in texts],
                }
                next_version_no += 1
                candidates.append(candidate)
                return candidate

            current_candidate = new_candidate(
                block_texts,
                created_by="Drafter",
                status="draft",
            )
            critic = Critic(
                gateway=self.gateway,
                db=self.db,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            self._configure_agent_prompt(critic, "Critic")
            rewriter = Rewriter(
                gateway=self.gateway,
                db=self.db,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            self._configure_agent_prompt(rewriter, "Rewriter")

            evaluated_candidates: list[dict[str, Any]] = []
            revision_records: list[dict[str, Any]] = []
            all_issues: list[dict[str, Any]] = []

            for round_no in range(self.max_rewrite_rounds + 1):
                critic_result = await critic.review_texts(block_texts, chapter_plan=plan)
                await self.db.rollback()
                all_issues.extend(critic_result.get("issues", []))

                guard = ContinuityGuard(
                    gateway=self.gateway,
                    db=self.db,
                    project_id=self.project_id,
                    session_id=self.session_id,
                )
                self._configure_agent_prompt(guard, "ContinuityGuard")
                continuity_result = await guard.check_texts(block_texts, chapter_no)
                await self.db.rollback()

                current_candidate["critic_result"] = dict(critic_result)
                current_candidate["continuity_result"] = dict(continuity_result)
                current_candidate["round_no"] = round_no
                evaluated_candidates.append(current_candidate)

                verdict = critic_result.get("verdict", "revise")
                score = critic_result.get("overall_score", 0)
                needs_rewrite = (
                    verdict == "rewrite"
                    or (verdict == "revise" and round_no < self.max_rewrite_rounds)
                    or not continuity_result.get("passed", True)
                )
                if not needs_rewrite:
                    break
                if round_no >= self.max_rewrite_rounds:
                    logger.warning(
                        "项目 %s 第 %d 章已达最大重写次数 %d，继续审定",
                        self.project_id,
                        chapter_no,
                        self.max_rewrite_rounds,
                    )
                    break

                continuity_issues: list[dict[str, Any]] = []
                for conflict in continuity_result.get("conflicts", []):
                    if isinstance(conflict, dict):
                        continuity_issues.append(
                            {
                                **conflict,
                                "issue_type": conflict.get("conflict_type", "continuity"),
                                "severity": conflict.get("severity", "high"),
                                "description": conflict.get("description")
                                or conflict.get("message")
                                or "正文与既有设定冲突",
                                "source": "ContinuityGuard",
                            }
                        )
                for warning in continuity_result.get("warnings", []):
                    if isinstance(warning, dict):
                        continuity_issues.append(
                            {
                                **warning,
                                "issue_type": warning.get(
                                    "warning_type", "continuity_warning"
                                ),
                                "severity": warning.get("severity", "medium"),
                                "description": warning.get("description")
                                or warning.get("message")
                                or "正文存在连续性风险",
                                "source": "ContinuityGuard",
                            }
                        )
                    elif warning:
                        continuity_issues.append(
                            {
                                "issue_type": "continuity_warning",
                                "severity": "medium",
                                "description": str(warning),
                                "source": "ContinuityGuard",
                            }
                        )
                rewrite_issues = [*critic_result.get("issues", []), *continuity_issues]
                all_issues.extend(continuity_issues)
                revised_blocks = await rewriter.rewrite_texts(
                    block_texts=block_texts,
                    issues=rewrite_issues,
                    plan=plan,
                    chapter_id=chapter_id,
                )
                await self.db.rollback()
                if not revised_blocks:
                    raise AgentExecutionError(
                        "Rewriter 返回空结果，已阻止删除旧版本",
                        agent_name="Rewriter",
                        project_id=str(self.project_id),
                        chapter_no=chapter_no,
                    )
                previous_candidate = current_candidate
                block_texts = snapshot(revised_blocks)
                current_candidate = new_candidate(
                    block_texts,
                    created_by=f"Rewriter:round-{round_no + 1}",
                    status="revision",
                )
                revision_records.append(
                    {
                        "round_no": round_no + 1,
                        "input_candidate_id": previous_candidate["id"],
                        "output_candidate_id": current_candidate["id"],
                        "instruction": self._revision_instruction(rewrite_issues),
                        "score_before": score,
                        "diff_summary": (
                            f"版本 {previous_candidate['version_no']} → "
                            f"{current_candidate['version_no']}; "
                            f"处理 {len(rewrite_issues)} 项问题"
                        ),
                    }
                )

            selection_pool = list(evaluated_candidates)
            if persisted_incumbent is not None:
                selection_pool.append(persisted_incumbent)
            selected = max(
                selection_pool,
                key=lambda candidate: self._quality_candidate_rank(
                    candidate,
                    quality_threshold,
                ),
            )
            block_texts = [dict(item) for item in selected["block_texts"]]
            critic_result = dict(selected["critic_result"])
            continuity_result = dict(selected["continuity_result"])

            gate_preview = ChiefEditor.assess_results(
                critic_result,
                continuity_result,
                quality_threshold,
            )
            memory_prepared: Optional[dict[str, Any]] = None
            keeper: Optional[MemoryKeeper] = None
            if gate_preview["approved"]:
                keeper = MemoryKeeper(
                    gateway=self.gateway,
                    db=self.db,
                    project_id=self.project_id,
                    session_id=self.session_id,
                )
                self._configure_agent_prompt(keeper, "MemoryKeeper")
                memory_prepared = await keeper.prepare_state_update(chapter_no, block_texts)
                await self.db.rollback()

            # No model call is allowed below this point.  This is the only
            # canonical publication transaction for the chapter.
            chapter_tx = await self.db.begin_nested()
            chapter = await self.db.get(Chapter, chapter_id)
            if chapter is None:
                chapter = Chapter(
                    id=chapter_id,
                    project_id=self.project_id,
                    chapter_no=chapter_no,
                    title=str(plan.get("chapter_title") or f"第{chapter_no}章"),
                    status="draft",
                    word_count=0,
                    target_words=3000,
                )
                self.db.add(chapter)
                await self.db.flush()

            for candidate in candidates:
                version = ChapterVersion(
                    id=candidate["id"],
                    chapter_id=chapter.id,
                    version_no=candidate["version_no"],
                    content=candidate["content"],
                    word_count=candidate["word_count"],
                    status=candidate["status"],
                    created_by_agent=candidate["created_by"],
                )
                self.db.add(version)
            await self.db.flush()

            await self._delete_blocks(chapter.id)
            published_blocks: list[ManuscriptBlock] = []
            for index, raw in enumerate(block_texts, start=1):
                block = ManuscriptBlock(
                    chapter_id=chapter.id,
                    version_id=selected["id"],
                    block_no=int(raw.get("block_no") or index),
                    block_type=str(raw.get("block_type") or "paragraph"),
                    content=str(raw.get("content") or ""),
                )
                self.db.add(block)
                published_blocks.append(block)
            chapter.current_version_id = selected["id"]
            chapter.word_count = int(selected["word_count"])
            await self.db.flush()

            ledger = QualityLedger(self.db, self.project_id)
            issue_ids_by_candidate: dict[uuid.UUID, list[uuid.UUID]] = {}
            for candidate in evaluated_candidates:
                candidate_id = candidate["id"]
                round_no = int(candidate["round_no"])
                critic_assessment = await ledger.record_critic_assessment(
                    idempotency_key=(
                        f"chapter:{chapter.id}:version:{candidate_id}:round:{round_no}:critic"
                    ),
                    result=candidate["critic_result"],
                    chapter_id=chapter.id,
                    version_id=candidate_id,
                    session_id=self.session_id,
                    round_no=round_no,
                )
                continuity_assessment = await ledger.record_continuity_assessment(
                    idempotency_key=(
                        f"chapter:{chapter.id}:version:{candidate_id}:"
                        f"round:{round_no}:continuity"
                    ),
                    result=candidate["continuity_result"],
                    chapter_id=chapter.id,
                    version_id=candidate_id,
                    session_id=self.session_id,
                    round_no=round_no,
                )
                issue_rows = await self.db.execute(
                    select(QualityIssue.id).where(
                        QualityIssue.assessment_id.in_(
                            [critic_assessment.id, continuity_assessment.id]
                        ),
                        QualityIssue.status == "open",
                    )
                )
                issue_ids_by_candidate[candidate_id] = list(issue_rows.scalars().all())

            score_by_candidate = {
                candidate["id"]: float(
                    candidate["critic_result"].get("overall_score") or 0
                )
                for candidate in evaluated_candidates
            }
            for revision in revision_records:
                output_id = revision["output_candidate_id"]
                await ledger.record_revision_attempt(
                    idempotency_key=(
                        f"chapter:{chapter.id}:input:{revision['input_candidate_id']}:"
                        f"output:{output_id}:rewrite"
                    ),
                    round_no=revision["round_no"],
                    chapter_id=chapter.id,
                    session_id=self.session_id,
                    input_version_id=revision["input_candidate_id"],
                    output_version_id=output_id,
                    trigger_issue_ids=issue_ids_by_candidate.get(
                        revision["input_candidate_id"], []
                    ),
                    instruction_source="critic+continuity",
                    instruction=revision["instruction"],
                    score_before=revision["score_before"],
                    score_after=score_by_candidate.get(output_id),
                    status="completed",
                    diff_summary=revision["diff_summary"],
                )

            latest_candidate = evaluated_candidates[-1]
            if selected["id"] != latest_candidate["id"]:
                await ledger.record_revision_attempt(
                    idempotency_key=(
                        f"chapter:{chapter.id}:input:{latest_candidate['id']}:"
                        f"output:{selected['id']}:quality-selector"
                    ),
                    status="selected",
                    round_no=int(selected["round_no"]),
                    chapter_id=chapter.id,
                    session_id=self.session_id,
                    input_version_id=latest_candidate["id"],
                    output_version_id=selected["id"],
                    instruction_source="quality_selector",
                    instruction="自动回选本轮所有已评估版本中的最佳候选，防止重写质量倒退",
                    score_before=float(
                        latest_candidate["critic_result"].get("overall_score") or 0
                    ),
                    score_after=float(critic_result.get("overall_score") or 0),
                    diff_summary=(
                        f"放弃版本 {latest_candidate['version_no']}；"
                        f"回选版本 {selected['version_no']}"
                    ),
                )

            editor = ChiefEditor(
                gateway=self.gateway,
                db=self.db,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            self._configure_agent_prompt(editor, "ChiefEditor")
            finalize_result = await editor.finalize(
                chapter_id=chapter.id,
                critic_result=critic_result,
                continuity_result=continuity_result,
                quality_threshold=quality_threshold,
            )
            final_score = int(finalize_result.get("final_score") or 0)
            approved = bool(finalize_result.get("approved"))
            final_gate_issues = (
                []
                if approved
                else [
                    {
                        "source": "ChiefEditor",
                        "category": "quality_gate",
                        "severity": "high",
                        "description": finalize_result.get("notes")
                        or "章节未通过最终质量闸门",
                    }
                ]
            )
            await ledger.record_assessment(
                idempotency_key=(
                    f"chapter:{chapter.id}:version:{selected['id']}:final-gate"
                ),
                assessor="ChiefEditor",
                assessment_type="deterministic_gate",
                dimension_scores={"final": final_score},
                overall_score=final_score,
                verdict="approved" if approved else "review",
                passed=approved,
                issues=final_gate_issues,
                raw_result={
                    **finalize_result,
                    "production_prompt": self._prompt_audit_for("ChiefEditor"),
                    "prompt_provenance": self._prompt_audit_for("ChiefEditor"),
                },
                chapter_id=chapter.id,
                version_id=selected["id"],
                session_id=self.session_id,
                round_no=self.max_rewrite_rounds + 1,
                rubric_version=f"threshold-{quality_threshold}",
            )

            learning_result: Optional[dict[str, Any]] = None
            if approved:
                await self._resolve_pending_review_items(chapter)
                if keeper is None or memory_prepared is None:
                    raise RuntimeError("MemoryKeeper 发布载荷缺失")
                await keeper.apply_prepared_state(chapter.id, memory_prepared)
                learning_result = await AutonomousLearningService(
                    self.db,
                    self.project_id,
                ).run_post_chapter_cycle(
                    chapter_no=chapter_no,
                    session_id=self.session_id,
                    prompt_evaluation_interval=self.learning_interval_chapters,
                )
            else:
                await self._ensure_review_item(
                    chapter=chapter,
                    score=final_score,
                    quality_threshold=quality_threshold,
                    critic_result=critic_result,
                    continuity_result=continuity_result,
                    notes=finalize_result.get("notes", ""),
                )

            result = {
                "chapter_no": chapter_no,
                "chapter_id": str(chapter.id),
                "status": "approved" if approved else "review",
                "score": final_score,
                "verdict": critic_result.get("verdict", ""),
                "block_count": len(published_blocks),
                "word_count": finalize_result.get("word_count", 0),
                "issues_count": len(all_issues),
                "continuity_passed": bool(continuity_result.get("passed", True)),
                "version_no": finalize_result.get("version_no", 1),
                "notes": finalize_result.get("notes", ""),
                "learning": learning_result,
                "production_prompt": self._prompt_audit_for("Drafter"),
            }
            await chapter_tx.commit()
            return result
        except Exception as exc:
            if chapter_tx is not None and chapter_tx.is_active:
                await chapter_tx.rollback()
            else:
                await self.db.rollback()
            logger.exception(
                "项目 %s 第 %d 章生成失败: %s",
                self.project_id,
                chapter_no,
                exc,
            )
            try:
                chapter = await self._get_or_create_chapter(chapter_no)
                chapter.status = "failed"
                await self.db.flush()
            except Exception as rollback_exc:
                logger.error(
                    "项目 %s 第 %d 章状态回滚失败: %s",
                    self.project_id,
                    chapter_no,
                    rollback_exc,
                )
            return {
                "chapter_no": chapter_no,
                "status": "failed",
                "error": str(exc),
                "score": 0,
            }

    async def _run_chapter_legacy(self, chapter_no: int, mode: str = "L2") -> dict[str, Any]:
        """执行单章生成 Pipeline。

        流程：计划 → 起草 → 审查 → 一致性 → (重写) → 审定 → 状态更新

        Args:
            chapter_no: 章节编号。
            mode: 自治等级（L0/L1/L2）。

        Returns:
            结果 dict，包含 chapter_no, status, score, blocks, issues。
        """
        logger.info(
            "项目 %s 开始生成第 %d 章 (mode=%s)",
            self.project_id,
            chapter_no,
            mode,
        )

        # 加载项目专属自定义系统提示词（影响所有 Agent）
        await self._load_custom_prompt()

        # 每一章都在独立 savepoint 内完成。任一 Agent 失败时，正文、版本、
        # 记忆与质量记录会一起回滚，避免连续任务提交半章残留。
        chapter_tx = await self.db.begin_nested()
        try:
            # 获取或创建 Chapter
            chapter = await self._get_or_create_chapter(chapter_no)
            repair_context = await self._quality_repair_context(chapter)
            chapter.status = "generating"
            # 重试 review/failed 章节前清理当前工作块。历史稿已经保存在
            # ChapterVersion 中，不会随工作块替换而丢失。
            await self._delete_blocks(chapter.id)
            await self.db.flush()

            # 1. ChapterPlanner — 生成写作计划
            planner = ChapterPlanner(
                gateway=self.gateway,
                db=self.db,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            # 注入自定义系统提示词
            self._configure_agent_prompt(planner, "ChapterPlanner")
            planner.quality_repair_context = repair_context
            plan = await planner.plan_chapter(chapter_no)
            if repair_context:
                plan["_quality_repair_context"] = repair_context

            # 将 Canon、近章全文、弧线摘要、角色卡、伏笔和长期文风记忆
            # 按固定 token 预算编译后交给 Drafter。这个上下文是长篇一致性
            # 的正式输入，而不是只读取前章最后 500 字。
            compiled_context = await ContextCompiler(
                self.db,
                self.project_id,
                gateway=self.gateway,
            ).compile(
                chapter_no,
                plan,
                prompt_provenance=self._prompt_audit_for("Drafter"),
            )
            plan["_compiled_context"] = {
                "system_prompt": compiled_context.system_prompt,
                "context_text": compiled_context.context_text,
                "total_tokens": compiled_context.total_tokens,
                "budget_breakdown": compiled_context.budget_breakdown,
                "provenance": compiled_context.provenance,
                "prompt_provenance": compiled_context.prompt_provenance,
            }

            # 2. Drafter — 起草正文
            drafter = Drafter(
                gateway=self.gateway,
                db=self.db,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            # 注入自定义系统提示词
            self._configure_agent_prompt(drafter, "Drafter")
            blocks = await drafter.draft_chapter(plan, chapter_id=chapter.id)

            # 持久化初始 blocks，并创建内容快照
            for block in blocks:
                self.db.add(block)
            await self.db.flush()

            # 创建 blocks 的内容快照（避免 ORM session 状态问题）
            def _snapshot(blks):
                """从 ORM blocks 创建简单内容列表。"""
                return [
                    {"content": b.content, "block_type": b.block_type, "block_no": b.block_no}
                    for b in blks
                ]

            block_texts = _snapshot(blocks)
            current_version = await self._create_version_snapshot(
                chapter,
                block_texts,
                created_by="Drafter",
                status="draft",
            )

            # 3-5. Critic → (Rewriter → Critic) 循环
            critic = Critic(
                gateway=self.gateway,
                db=self.db,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            # 注入自定义系统提示词
            self._configure_agent_prompt(critic, "Critic")
            rewriter = Rewriter(
                gateway=self.gateway,
                db=self.db,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            # 注入自定义系统提示词
            self._configure_agent_prompt(rewriter, "Rewriter")

            critic_result: dict[str, Any] = {}
            continuity_result: dict[str, Any] = {}
            all_issues: list[dict[str, Any]] = []
            ledger = QualityLedger(self.db, self.project_id)
            pending_revision: Optional[dict[str, Any]] = None
            quality_threshold = self.quality_threshold
            if quality_threshold is None:
                quality_threshold = 80 if mode in {"L2", "L3", "L4", "auto"} else 70
            evaluated_candidates: list[dict[str, Any]] = []

            for round_no in range(self.max_rewrite_rounds + 1):
                # 3. Critic — 质量审查（传内容快照，避免 ORM 状态问题）
                critic_result = await critic.review_texts(block_texts, chapter_plan=plan)
                all_issues.extend(critic_result.get("issues", []))

                verdict = critic_result.get("verdict", "revise")
                score = critic_result.get("overall_score", 0)

                # 4. ContinuityGuard — 一致性校验
                guard = ContinuityGuard(
                    gateway=self.gateway,
                    db=self.db,
                    project_id=self.project_id,
                    session_id=self.session_id,
                )
                # 注入自定义系统提示词
                self._configure_agent_prompt(guard, "ContinuityGuard")
                continuity_result = await guard.check_texts(block_texts, chapter_no)
                evaluated_candidates.append(
                    {
                        "version": current_version,
                        "block_texts": [dict(block) for block in block_texts],
                        "critic_result": dict(critic_result),
                        "continuity_result": dict(continuity_result),
                        "round_no": round_no,
                    }
                )

                critic_assessment = await ledger.record_critic_assessment(
                    idempotency_key=(
                        f"chapter:{chapter.id}:version:{current_version.id}:round:{round_no}:critic"
                    ),
                    result=critic_result,
                    chapter_id=chapter.id,
                    version_id=current_version.id,
                    session_id=self.session_id,
                    round_no=round_no,
                )
                continuity_assessment = await ledger.record_continuity_assessment(
                    idempotency_key=(
                        f"chapter:{chapter.id}:version:{current_version.id}:"
                        f"round:{round_no}:continuity"
                    ),
                    result=continuity_result,
                    chapter_id=chapter.id,
                    version_id=current_version.id,
                    session_id=self.session_id,
                    round_no=round_no,
                )

                assessment_ids = [critic_assessment.id, continuity_assessment.id]
                issue_rows = await self.db.execute(
                    select(QualityIssue.id).where(
                        QualityIssue.assessment_id.in_(assessment_ids),
                        QualityIssue.status == "open",
                    )
                )
                round_issue_ids = list(issue_rows.scalars().all())

                if pending_revision:
                    await ledger.record_revision_attempt(
                        **pending_revision,
                        score_after=score,
                        status="completed",
                    )
                    pending_revision = None

                # 判断是否需要重写
                needs_rewrite = (
                    verdict == "rewrite"
                    or (verdict == "revise" and round_no < self.max_rewrite_rounds)
                    or not continuity_result.get("passed", True)
                )

                if not needs_rewrite:
                    break

                if round_no >= self.max_rewrite_rounds:
                    logger.warning(
                        "项目 %s 第 %d 章已达最大重写次数 %d，继续审定",
                        self.project_id,
                        chapter_no,
                        self.max_rewrite_rounds,
                    )
                    break

                # 5. Rewriter — 重写
                logger.info(
                    "项目 %s 第 %d 章第 %d 轮重写 (score=%d, verdict=%s)",
                    self.project_id,
                    chapter_no,
                    round_no + 1,
                    score,
                    verdict,
                )

                continuity_issues: list[dict[str, Any]] = []
                for conflict in continuity_result.get("conflicts", []):
                    if isinstance(conflict, dict):
                        continuity_issues.append(
                            {
                                **conflict,
                                "issue_type": conflict.get("conflict_type", "continuity"),
                                "severity": conflict.get("severity", "high"),
                                "description": conflict.get("description")
                                or conflict.get("message")
                                or "正文与既有设定冲突",
                                "source": "ContinuityGuard",
                            }
                        )
                for warning in continuity_result.get("warnings", []):
                    if isinstance(warning, dict):
                        continuity_issues.append(
                            {
                                **warning,
                                "issue_type": warning.get("warning_type", "continuity_warning"),
                                "severity": warning.get("severity", "medium"),
                                "description": warning.get("description")
                                or warning.get("message")
                                or "正文存在连续性风险",
                                "source": "ContinuityGuard",
                            }
                        )
                    elif warning:
                        continuity_issues.append(
                            {
                                "issue_type": "continuity_warning",
                                "severity": "medium",
                                "description": str(warning),
                                "source": "ContinuityGuard",
                            }
                        )
                rewrite_issues = [
                    *critic_result.get("issues", []),
                    *continuity_issues,
                ]
                all_issues.extend(continuity_issues)

                # 传内容快照给 Rewriter（而非 ORM 引用）
                blocks = await rewriter.rewrite_texts(
                    block_texts=block_texts,
                    issues=rewrite_issues,
                    plan=plan,
                    chapter_id=chapter.id,
                )
                # 数据保护：Rewriter 返回空列表时不删除旧 blocks
                if not blocks:
                    logger.error(
                        "项目 %s 第 %d 章 Rewriter 返回空结果，保留旧版本不删除",
                        self.project_id,
                        chapter_no,
                    )
                    # 不执行 _delete_blocks，保留上一轮的正文
                    raise AgentExecutionError(
                        "Rewriter 返回空结果，已阻止删除旧版本",
                        agent_name="Rewriter",
                        project_id=str(self.project_id),
                        chapter_no=chapter_no,
                    )
                # 有新 blocks 才删除旧 blocks 并添加新的
                await self._delete_blocks(chapter.id)
                for block in blocks:
                    self.db.add(block)
                await self.db.flush()
                block_texts = _snapshot(blocks)
                previous_version = current_version
                current_version = await self._create_version_snapshot(
                    chapter,
                    block_texts,
                    created_by=f"Rewriter:round-{round_no + 1}",
                    status="revision",
                )
                pending_revision = {
                    "idempotency_key": (
                        f"chapter:{chapter.id}:input:{previous_version.id}:"
                        f"output:{current_version.id}:rewrite"
                    ),
                    "round_no": round_no + 1,
                    "chapter_id": chapter.id,
                    "session_id": self.session_id,
                    "input_version_id": previous_version.id,
                    "output_version_id": current_version.id,
                    "trigger_issue_ids": round_issue_ids,
                    "instruction_source": "critic+continuity",
                    "instruction": self._revision_instruction(rewrite_issues),
                    "score_before": score,
                    "diff_summary": (
                        f"版本 {previous_version.version_no} → {current_version.version_no}; "
                        f"处理 {len(rewrite_issues)} 项问题"
                    ),
                }

            # Rewriter is stochastic and can introduce a new defect while
            # repairing another one.  Never submit a worse final candidate
            # merely because it happened to be generated last.  Every version
            # remains immutable and auditable; the deterministic selector
            # restores the strongest evaluated snapshot for final gating.
            if evaluated_candidates:
                latest_candidate = evaluated_candidates[-1]
                best_candidate = max(
                    evaluated_candidates,
                    key=lambda candidate: self._quality_candidate_rank(
                        candidate,
                        quality_threshold,
                    ),
                )
                best_version = best_candidate["version"]
                if best_version.id != current_version.id:
                    blocks = await self._restore_working_snapshot(
                        chapter,
                        best_candidate["block_texts"],
                        version_id=best_version.id,
                    )
                    block_texts = [dict(block) for block in best_candidate["block_texts"]]
                    current_version = best_version
                    critic_result = dict(best_candidate["critic_result"])
                    continuity_result = dict(best_candidate["continuity_result"])
                    chapter.current_version_id = best_version.id
                    chapter.word_count = best_version.word_count
                    await ledger.record_revision_attempt(
                        idempotency_key=(
                            f"chapter:{chapter.id}:input:{latest_candidate['version'].id}:"
                            f"output:{best_version.id}:quality-selector"
                        ),
                        status="selected",
                        round_no=int(best_candidate["round_no"]),
                        chapter_id=chapter.id,
                        session_id=self.session_id,
                        input_version_id=latest_candidate["version"].id,
                        output_version_id=best_version.id,
                        instruction_source="quality_selector",
                        instruction="自动回选本轮所有已评估版本中的最佳候选，防止重写质量倒退",
                        score_before=float(
                            latest_candidate["critic_result"].get("overall_score") or 0
                        ),
                        score_after=float(critic_result.get("overall_score") or 0),
                        diff_summary=(
                            f"放弃版本 {latest_candidate['version'].version_no}；"
                            f"回选版本 {best_version.version_no}"
                        ),
                    )
                    logger.warning(
                        "项目 %s 第 %d 章末轮质量倒退，已从版本 %d 回选版本 %d",
                        self.project_id,
                        chapter_no,
                        latest_candidate["version"].version_no,
                        best_version.version_no,
                    )

            # 6. ChiefEditor — 最终审定
            editor = ChiefEditor(
                gateway=self.gateway,
                db=self.db,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            # 注入自定义系统提示词
            self._configure_agent_prompt(editor, "ChiefEditor")
            finalize_result = await editor.finalize(
                chapter_id=chapter.id,
                critic_result=critic_result,
                continuity_result=continuity_result,
                quality_threshold=quality_threshold,
            )

            final_score = finalize_result.get("final_score", 0)
            approved = finalize_result.get("approved", False)
            final_version_id = chapter.current_version_id
            final_gate_issues: list[dict[str, Any]] = []
            if not approved:
                final_gate_issues.append(
                    {
                        "source": "ChiefEditor",
                        "category": "quality_gate",
                        "severity": "high",
                        "description": finalize_result.get("notes") or "章节未通过最终质量闸门",
                    }
                )
            await ledger.record_assessment(
                idempotency_key=(f"chapter:{chapter.id}:version:{final_version_id}:final-gate"),
                assessor="ChiefEditor",
                assessment_type="deterministic_gate",
                dimension_scores={"final": final_score},
                overall_score=final_score,
                verdict="approved" if approved else "review",
                passed=approved,
                issues=final_gate_issues,
                raw_result={
                    **finalize_result,
                    "production_prompt": self._prompt_audit_for("ChiefEditor"),
                    "prompt_provenance": self._prompt_audit_for("ChiefEditor"),
                },
                chapter_id=chapter.id,
                version_id=final_version_id,
                session_id=self.session_id,
                round_no=self.max_rewrite_rounds + 1,
                rubric_version=f"threshold-{quality_threshold}",
            )

            learning_result: Optional[dict[str, Any]] = None

            # 7. MemoryKeeper — 只有批准的章节才执行记忆更新
            if approved:
                await self._resolve_pending_review_items(chapter)
                keeper = MemoryKeeper(
                    gateway=self.gateway,
                    db=self.db,
                    project_id=self.project_id,
                    session_id=self.session_id,
                )
                # 注入自定义系统提示词
                self._configure_agent_prompt(keeper, "MemoryKeeper")
                await keeper.update_state(chapter.id, blocks)
                # 8. AutonomousLearning — 将本章质量证据与人工反馈沉淀成
                # 下一章 ContextCompiler 会实际读取的长期规则；提示词变更只
                # 生成候选版本，未经 holdout 不自动晋升。
                learning_result = await AutonomousLearningService(
                    self.db,
                    self.project_id,
                ).run_post_chapter_cycle(
                    chapter_no=chapter_no,
                    session_id=self.session_id,
                    prompt_evaluation_interval=self.learning_interval_chapters,
                )
            else:
                logger.warning(
                    "项目 %s 第 %d 章未获批准 (score=%d)，跳过记忆更新",
                    self.project_id,
                    chapter_no,
                    final_score,
                )
                await self._ensure_review_item(
                    chapter=chapter,
                    score=final_score,
                    quality_threshold=quality_threshold,
                    critic_result=critic_result,
                    continuity_result=continuity_result,
                    notes=finalize_result.get("notes", ""),
                )

            logger.info(
                "项目 %s 第 %d 章生成完成: approved=%s, score=%d",
                self.project_id,
                chapter_no,
                approved,
                final_score,
            )

            result = {
                "chapter_no": chapter_no,
                "chapter_id": str(chapter.id),
                "status": "approved" if approved else "review",
                "score": final_score,
                "verdict": critic_result.get("verdict", ""),
                "block_count": len(blocks),
                "word_count": finalize_result.get("word_count", 0),
                "issues_count": len(all_issues),
                "continuity_passed": continuity_result.get("passed", True),
                "version_no": finalize_result.get("version_no", 1),
                "notes": finalize_result.get("notes", ""),
                "learning": learning_result,
                "production_prompt": self._prompt_audit_for("Drafter"),
            }
            await chapter_tx.commit()
            return result

        except Exception as exc:
            if chapter_tx.is_active:
                await chapter_tx.rollback()
            logger.exception(
                "项目 %s 第 %d 章生成失败: %s",
                self.project_id,
                chapter_no,
                exc,
            )
            # savepoint 已经清除了本轮所有半成品。仅在外层事务记录一个
            # 可恢复的失败状态，连续运行器会据此退避并重试同一章。
            try:
                chapter = await self._get_or_create_chapter(chapter_no)
                chapter.status = "failed"
                await self.db.flush()
            except Exception as rollback_exc:
                logger.error(
                    "项目 %s 第 %d 章状态回滚失败: %s",
                    self.project_id,
                    chapter_no,
                    rollback_exc,
                )

            return {
                "chapter_no": chapter_no,
                "status": "failed",
                "error": str(exc),
                "score": 0,
            }

    # ------------------------------------------------------------------
    # 多章连续生成
    # ------------------------------------------------------------------
    async def run_pipeline(
        self,
        target_chapters: int,
        mode: str = "L2",
        start_chapter: Optional[int] = None,
    ) -> dict[str, Any]:
        """多章连续生成。

        Args:
            target_chapters: 目标生成章节数。
            mode: 自治等级。
            start_chapter: 起始章节号（为 None 则从当前进度继续）。

        Returns:
            汇总结果 dict。
        """
        # 确定起始章节
        if start_chapter is None:
            start_chapter = await self._get_next_chapter_no()

        results: list[dict[str, Any]] = []
        success_count = 0
        failed_count = 0
        review_count = 0

        for i in range(target_chapters):
            chapter_no = start_chapter + i
            result = await self.run_chapter(chapter_no, mode=mode)
            results.append(result)

            result_status = result.get("status")
            if result_status == "failed":
                failed_count += 1
                break  # 失败则停止
            if result_status == "review":
                review_count += 1
                break  # 未审定章节绝不允许越章
            if result_status == "approved":
                success_count += 1

        # 更新 WorkSession 进度
        session_status = "waiting_review" if review_count else None
        await self._update_session_progress(
            success_count,
            target_chapters,
            status=session_status,
        )

        if review_count:
            pipeline_status = "waiting_review"
        elif failed_count:
            pipeline_status = "partial"
        else:
            pipeline_status = "completed"

        return {
            "job": "run_pipeline",
            "status": pipeline_status,
            "mode": mode,
            "start_chapter": start_chapter,
            "target_chapters": target_chapters,
            "success_count": success_count,
            "failed_count": failed_count,
            "review_count": review_count,
            "chapters": results,
        }

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    async def _get_or_create_chapter(self, chapter_no: int) -> Chapter:
        """获取或创建章节记录。"""
        stmt = (
            select(Chapter)
            .where(
                Chapter.project_id == self.project_id,
                Chapter.chapter_no == chapter_no,
            )
            .order_by(Chapter.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        chapter = result.scalar_one_or_none()
        if not chapter:
            chapter = Chapter(
                project_id=self.project_id,
                chapter_no=chapter_no,
                title=f"第{chapter_no}章",
                status="draft",
                word_count=0,
                target_words=3000,
            )
            self.db.add(chapter)
            await self.db.flush()
        return chapter

    async def _delete_blocks(self, chapter_id: uuid.UUID) -> None:
        """删除章节的所有 ManuscriptBlock。"""
        stmt = select(ManuscriptBlock).where(
            ManuscriptBlock.chapter_id == chapter_id,
        )
        result = await self.db.execute(stmt)
        blocks = result.scalars().all()
        for block in blocks:
            await self.db.delete(block)
        await self.db.flush()

    async def _create_version_snapshot(
        self,
        chapter: Chapter,
        block_texts: list[dict[str, Any]],
        *,
        created_by: str,
        status: str,
    ) -> ChapterVersion:
        """保存不可变正文快照，供重写 diff、审计与人工回退使用。"""
        stmt = (
            select(ChapterVersion.version_no)
            .where(ChapterVersion.chapter_id == chapter.id)
            .order_by(ChapterVersion.version_no.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        next_version_no = (result.scalar_one_or_none() or 0) + 1
        content = "\n\n".join(str(block.get("content", "")) for block in block_texts)
        word_count = len(content.replace("\n", "").replace(" ", ""))
        version = ChapterVersion(
            chapter_id=chapter.id,
            version_no=next_version_no,
            content=content,
            word_count=word_count,
            status=status,
            created_by_agent=created_by,
        )
        self.db.add(version)
        await self.db.flush()
        chapter.current_version_id = version.id
        chapter.word_count = word_count
        await self.db.flush()
        return version

    async def _restore_working_snapshot(
        self,
        chapter: Chapter,
        block_texts: list[dict[str, Any]],
        *,
        version_id: uuid.UUID,
    ) -> list[ManuscriptBlock]:
        """Restore one immutable candidate as the current editable blocks."""
        await self._delete_blocks(chapter.id)
        restored: list[ManuscriptBlock] = []
        for index, raw in enumerate(block_texts, start=1):
            block = ManuscriptBlock(
                chapter_id=chapter.id,
                version_id=version_id,
                block_no=int(raw.get("block_no") or index),
                block_type=str(raw.get("block_type") or "paragraph"),
                content=str(raw.get("content") or ""),
            )
            self.db.add(block)
            restored.append(block)
        await self.db.flush()
        return restored

    @staticmethod
    def _quality_candidate_rank(
        candidate: dict[str, Any],
        quality_threshold: int,
    ) -> tuple[int, int, int, int, float, int]:
        """Rank evaluated snapshots without trusting generation order."""
        critic = candidate.get("critic_result") or {}
        continuity = candidate.get("continuity_result") or {}
        verdict = str(critic.get("verdict") or "rewrite").lower()
        score = float(critic.get("overall_score") or 0)
        issues = critic.get("issues") if isinstance(critic.get("issues"), list) else []
        blocking = sum(
            str(issue.get("severity") or "medium").lower() in {"high", "critical"}
            for issue in issues
            if isinstance(issue, dict)
        )
        continuity_passed = bool(continuity.get("passed", False))
        gate_passed = continuity_passed and verdict != "rewrite" and score >= quality_threshold
        verdict_rank = {"pass": 2, "revise": 1, "rewrite": 0}.get(verdict, 0)
        return (
            int(gate_passed),
            int(continuity_passed),
            verdict_rank,
            -blocking,
            score,
            -len(issues),
        )

    async def _best_persisted_candidate(
        self,
        chapter_id: uuid.UUID,
        quality_threshold: int,
    ) -> Optional[dict[str, Any]]:
        """Return the strongest immutable candidate from earlier retry cycles.

        A continuous quality retry creates a fresh plan, but it must never lose
        a stronger version produced by a previous plan.  Critic and continuity
        evidence is immutable and version-scoped, so it can be ranked with the
        exact same deterministic selector used inside one rewrite cycle.
        """
        versions = list(
            (
                await self.db.scalars(
                    select(ChapterVersion)
                    .where(ChapterVersion.chapter_id == chapter_id)
                    .order_by(ChapterVersion.version_no)
                )
            ).all()
        )
        if not versions:
            return None
        version_ids = [version.id for version in versions]
        assessments = list(
            (
                await self.db.scalars(
                    select(QualityAssessment)
                    .where(
                        QualityAssessment.version_id.in_(version_ids),
                        QualityAssessment.assessment_type.in_(["critic", "continuity"]),
                    )
                    .order_by(QualityAssessment.created_at, QualityAssessment.id)
                )
            ).all()
        )
        latest: dict[tuple[uuid.UUID, str], QualityAssessment] = {}
        for assessment in assessments:
            if assessment.version_id is not None:
                latest[(assessment.version_id, assessment.assessment_type)] = assessment

        candidates: list[dict[str, Any]] = []
        for version in versions:
            critic_assessment = latest.get((version.id, "critic"))
            continuity_assessment = latest.get((version.id, "continuity"))
            if critic_assessment is None or continuity_assessment is None:
                continue
            content = str(version.content or "").strip()
            if not content:
                continue
            critic = dict(critic_assessment.raw_result or {})
            critic.setdefault("overall_score", critic_assessment.overall_score or 0)
            critic.setdefault("verdict", critic_assessment.verdict or "rewrite")
            if not isinstance(critic.get("issues"), list):
                critic["issues"] = []
            continuity = dict(continuity_assessment.raw_result or {})
            continuity.setdefault("passed", bool(continuity_assessment.passed))
            if not isinstance(continuity.get("conflicts"), list):
                continuity["conflicts"] = []
            if not isinstance(continuity.get("warnings"), list):
                continuity["warnings"] = []
            paragraphs = [part.strip() for part in content.split("\n\n") if part.strip()]
            candidates.append(
                {
                    "id": version.id,
                    "version_no": version.version_no,
                    "content": content,
                    "word_count": version.word_count,
                    "created_by": version.created_by_agent,
                    "status": version.status,
                    "block_texts": [
                        {
                            "content": paragraph,
                            "block_type": "paragraph",
                            "block_no": index,
                        }
                        for index, paragraph in enumerate(paragraphs, start=1)
                    ],
                    "critic_result": critic,
                    "continuity_result": continuity,
                    "round_no": max(
                        int(critic_assessment.round_no or 0),
                        int(continuity_assessment.round_no or 0),
                    ),
                    "persisted": True,
                }
            )
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda candidate: self._quality_candidate_rank(
                candidate,
                quality_threshold,
            ),
        )

    async def _quality_repair_context(self, chapter: Chapter) -> str:
        """Build a bounded, deduplicated brief from prior failed versions."""
        rows = list(
            (
                await self.db.scalars(
                    select(QualityIssue)
                    .where(
                        QualityIssue.chapter_id == chapter.id,
                        QualityIssue.status == "open",
                    )
                    .order_by(QualityIssue.created_at.desc())
                    .limit(24)
                )
            ).all()
        )
        if not rows:
            return ""
        lines: list[str] = []
        seen: set[str] = set()
        for issue in rows:
            description = " ".join(str(issue.description or "").split()).strip()
            if not description or description in seen:
                continue
            seen.add(description)
            suggestion = " ".join(str(issue.suggestion or "").split()).strip()
            line = f"- [{issue.severity}/{issue.category}] {description}"
            if suggestion:
                line += f"；修复要求：{suggestion}"
            lines.append(line)
            if len(lines) >= 12:
                break
        return "\n".join(lines)[:6000]

    @staticmethod
    def _revision_instruction(issues: list[dict[str, Any]]) -> str:
        """Build a bounded, human-readable audit summary for one rewrite."""
        parts = []
        for issue in issues[:20]:
            severity = str(issue.get("severity", "medium"))
            description = str(issue.get("description") or issue.get("message") or "待修正问题")
            parts.append(f"[{severity}] {description}")
        return "\n".join(parts)[:8000]

    async def _ensure_review_item(
        self,
        *,
        chapter: Chapter,
        score: int,
        quality_threshold: int,
        critic_result: dict[str, Any],
        continuity_result: dict[str, Any],
        notes: str,
    ) -> ReviewQueueItem:
        """为质量闸门失败创建幂等的人工审阅条目。"""
        stmt = select(ReviewQueueItem).where(
            ReviewQueueItem.project_id == self.project_id,
            ReviewQueueItem.artifact_type == "chapter",
            ReviewQueueItem.artifact_id == chapter.id,
            ReviewQueueItem.status == "pending",
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()
        conflicts = continuity_result.get("conflicts", [])
        issues = critic_result.get("issues", [])
        description = (
            f"得分 {score}/{quality_threshold}；质量问题 {len(issues)} 项；"
            f"连续性冲突 {len(conflicts)} 项。{notes}"
        )
        risk_level = "high" if conflicts or score < quality_threshold - 10 else "medium"
        if existing:
            existing.session_id = self.session_id
            existing.title = f"第{chapter.chapter_no}章未通过质量闸门"
            existing.description = description
            existing.risk_level = risk_level
            existing.chapter_no = chapter.chapter_no
            await self.db.flush()
            return existing

        item = ReviewQueueItem(
            project_id=self.project_id,
            session_id=self.session_id,
            item_type="quality_gate",
            artifact_type="chapter",
            artifact_id=chapter.id,
            title=f"第{chapter.chapter_no}章未通过质量闸门",
            description=description,
            risk_level=risk_level,
            status="pending",
            chapter_no=chapter.chapter_no,
        )
        self.db.add(item)
        await self.db.flush()
        return item

    async def _resolve_pending_review_items(self, chapter: Chapter) -> int:
        """Archive obsolete human gates once a newer chapter version is approved."""
        pending = list(
            (
                await self.db.scalars(
                    select(ReviewQueueItem).where(
                        ReviewQueueItem.project_id == self.project_id,
                        ReviewQueueItem.artifact_type == "chapter",
                        ReviewQueueItem.artifact_id == chapter.id,
                        ReviewQueueItem.status == "pending",
                    )
                )
            ).all()
        )
        decided_at = datetime.now(timezone.utc)
        for item in pending:
            item.status = "approved"
            item.decided_by = "system"
            item.decided_at = decided_at
            item.decision_notes = (
                "A newer version passed the deterministic final gate; "
                "this older pending review was archived automatically."
            )
        if pending:
            await self.db.flush()
        return len(pending)

    async def _get_next_chapter_no(self) -> int:
        """获取下一章编号。

        优先返回第一个 word_count=0 的章节（未生成的已有章节），
        如果所有已有章节都已生成，则返回最大章节号 + 1。
        """
        # review/failed/generating 章节都属于未完成，必须从最早的一章恢复；
        # 只有 approved/published 才能作为后续章节的正式事实基础。
        stmt = (
            select(Chapter.chapter_no)
            .where(Chapter.project_id == self.project_id)
            .where(Chapter.status.notin_(["approved", "published"]))
            .order_by(Chapter.chapter_no.asc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        first_unfinished = result.scalar_one_or_none()
        if first_unfinished is not None:
            return first_unfinished

        # 所有已有章节都已完成，返回下一章
        stmt = (
            select(Chapter.chapter_no)
            .where(Chapter.project_id == self.project_id)
            .order_by(Chapter.chapter_no.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        max_no = result.scalar_one_or_none()
        return (max_no or 0) + 1

    async def _update_session_progress(
        self,
        completed: int,
        total: int,
        *,
        status: Optional[str] = None,
    ) -> None:
        """更新 WorkSession 进度。"""
        if not self.session_id:
            return
        session = await self.db.get(WorkSession, self.session_id)
        if session:
            session.progress_percent = (completed / total * 100) if total > 0 else 0
            if status:
                session.status = status
                if status == "waiting_review":
                    session.paused_reason = "章节未通过质量闸门，等待处理"
            elif completed >= total:
                session.status = "completed"
            elif completed > 0:
                session.status = "running"
            await self.db.flush()
