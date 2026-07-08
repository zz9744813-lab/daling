"""连续生产服务 - 24 小时不间断后台写作。

用 asyncio 后台任务实现项目的连续章节生成：
- 维护一个 dict[uuid.UUID, asyncio.Task] 记录每个项目的后台任务
- start(project_id, db_factory): 启动后台循环任务，每完成一章自动继续下一章
- stop(project_id): 取消后台任务
- get_status(project_id): 返回运行状态、当前章节、已完成章数、错误信息

后台循环逻辑：
    while not cancelled:
        1. 创建新的 db session
        2. 检查是否还有未完成章节（word_count=0 的 chapter）
        3. 如果有，调用 orchestrator.run_pipeline(target_chapters=1)
        4. 如果没有未完成章节，自动生成新大纲（generate_outline）
        5. 等待完成，记录结果
        6. 短暂休息 5 秒后继续下一章
        7. 出错时休息 30 秒重试
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.db.models.chapter import Chapter
from app.db.models.session import WorkSession
from app.db.models.world import WorldBible
from app.model_gateway import gateway
from app.pipeline.orchestrator import PipelineOrchestrator

logger = logging.getLogger("app.services.continuous_production")


class ContinuousProductionService:
    """连续生产服务 - 用 asyncio 后台任务管理项目的 24 小时不间断写作。

    维护两个映射：
    - ``_tasks``: project_id -> asyncio.Task，记录每个项目的后台循环任务
    - ``_status``: project_id -> 运行状态 dict
    """

    def __init__(self) -> None:
        # 每个项目的后台任务：project_id -> asyncio.Task
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        # 每个项目的运行状态
        self._status: dict[uuid.UUID, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    async def start(
        self,
        project_id: uuid.UUID,
        db_factory: Optional[Callable[[], Any]] = None,
        target_chapters: Optional[int] = None,
    ) -> dict[str, Any]:
        """启动项目的连续写作后台任务。

        Args:
            project_id: 项目 ID。
            db_factory: 可选的会话工厂（无参数调用返回 AsyncSession 上下文管理器），
                        为 None 则使用全局 ``async_session_factory``。
            target_chapters: 目标章数（None 表示无限制，持续写作）。

        Returns:
            启动结果 dict，包含 running / project_id / started_at。
        """
        # 若已有任务在运行，直接返回
        existing = self._tasks.get(project_id)
        if existing is not None and not existing.done():
            return {
                "running": True,
                "project_id": str(project_id),
                "message": "连续写作任务已在运行",
            }

        # 确定会话工厂：优先用传入的，否则用全局 async_session_factory
        factory: Callable[[], Any] = db_factory or async_session_factory

        # 初始化状态
        started_at = datetime.now(timezone.utc).isoformat()
        self._status[project_id] = {
            "running": True,
            "current_chapter": None,
            "completed_chapters": 0,
            "errors": [],
            "started_at": started_at,
            "target_chapters": target_chapters,
        }

        # 创建并启动后台循环任务
        task = asyncio.create_task(
            self._run_loop(project_id, factory, target_chapters)
        )
        self._tasks[project_id] = task
        logger.info(
            "项目 %s 启动连续写作 (target_chapters=%s)", project_id, target_chapters
        )
        return {
            "running": True,
            "project_id": str(project_id),
            "started_at": started_at,
        }

    async def stop(self, project_id: uuid.UUID) -> dict[str, Any]:
        """停止项目的连续写作后台任务。

        取消后台任务并清理状态。
        """
        task = self._tasks.get(project_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.pop(project_id, None)
        if project_id in self._status:
            self._status[project_id]["running"] = False
            self._status[project_id]["current_chapter"] = None

        logger.info("项目 %s 停止连续写作", project_id)
        return {"running": False, "project_id": str(project_id)}

    def get_status(self, project_id: uuid.UUID) -> dict[str, Any]:
        """返回项目的连续写作状态。

        Returns:
            包含 running / current_chapter / completed_chapters / errors / started_at 的 dict。
        """
        status = self._status.get(project_id)
        if status is None:
            return {
                "running": False,
                "current_chapter": None,
                "completed_chapters": 0,
                "errors": [],
                "started_at": None,
            }
        # 若任务已结束（非取消），标记为未运行
        task = self._tasks.get(project_id)
        if task is not None and task.done() and not task.cancelled():
            status["running"] = False
        return {
            "running": status["running"],
            "current_chapter": status["current_chapter"],
            "completed_chapters": status["completed_chapters"],
            "errors": status["errors"][-10:],  # 最近 10 条错误
            "started_at": status["started_at"],
        }

    # ------------------------------------------------------------------
    # 后台循环逻辑
    # ------------------------------------------------------------------
    async def _run_loop(
        self,
        project_id: uuid.UUID,
        db_factory: Callable[[], Any],
        target_chapters: Optional[int],
    ) -> None:
        """后台循环：持续生成章节，直到取消或达到目标章数。

        每轮循环：
        1. 创建新的 db session
        2. 检查是否还有未完成章节（word_count=0 的 chapter）
        3. 如果有，调用 orchestrator.run_pipeline(target_chapters=1)
        4. 如果没有未完成章节，自动生成新大纲（generate_outline）
        5. 等待完成，记录结果
        6. 短暂休息 5 秒后继续下一章
        7. 出错时休息 30 秒重试
        """
        completed = 0
        try:
            while True:
                # 检查是否达到目标章数（None 表示无限制）
                if target_chapters is not None and completed >= target_chapters:
                    logger.info(
                        "项目 %s 已完成目标 %d 章，连续写作结束",
                        project_id, target_chapters,
                    )
                    break

                try:
                    # 1. 创建新的 db session
                    async with db_factory() as db:
                        # 2. 检查是否还有未完成章节（word_count=0 的 chapter）
                        unfinished_no = await self._find_unfinished_chapter(
                            db, project_id
                        )

                        # 创建 WorkSession 用于追踪本次写作
                        session = WorkSession(
                            project_id=project_id,
                            title="连续写作",
                            goal="连续生成章节正文",
                            mode="L2",
                            status="running",
                            session_type="continuous_production",
                            quality_threshold=80,
                        )
                        db.add(session)
                        await db.flush()

                        # 创建编排器实例
                        orchestrator = PipelineOrchestrator(
                            gateway=gateway,
                            db=db,
                            project_id=project_id,
                            session_id=session.id,
                        )

                        if unfinished_no is not None:
                            # 3. 有未完成章节，调用 run_pipeline 生成正文
                            self._status[project_id]["current_chapter"] = (
                                unfinished_no
                            )
                            logger.info(
                                "项目 %s 连续写作：生成第 %d 章",
                                project_id, unfinished_no,
                            )
                            run_result = await orchestrator.run_pipeline(
                                target_chapters=1, mode="L2"
                            )
                            await db.commit()

                            # 5. 记录结果
                            if run_result.get("success_count", 0) > 0:
                                completed += 1
                                self._status[project_id]["completed_chapters"] = (
                                    completed
                                )
                            else:
                                # 章节生成失败，记录错误信息
                                chapters = run_result.get("chapters", [])
                                err = (
                                    chapters[0].get("error", "章节生成失败")
                                    if chapters
                                    else "章节生成失败"
                                )
                                self._status[project_id]["errors"].append(
                                    f"第{unfinished_no}章: {err}"
                                )
                        else:
                            # 4. 没有未完成章节
                            # 先确保项目已有世界观圣经，否则先生成
                            has_bible = await self._has_world_bible(
                                db, project_id
                            )
                            if not has_bible:
                                logger.info(
                                    "项目 %s 无世界观圣经，先生成世界观",
                                    project_id,
                                )
                                bible_result = await orchestrator.generate_bible(
                                    hints={}
                                )
                                await db.commit()
                                logger.info(
                                    "项目 %s 世界观已生成: %s",
                                    project_id,
                                    bible_result.get("world_name", ""),
                                )

                            # 自动生成新大纲（产生新的未完成章节）
                            logger.info(
                                "项目 %s 无未完成章节，生成新大纲", project_id
                            )
                            outline_result = await orchestrator.generate_outline(
                                volume_count=1, chapters_per_volume=10
                            )
                            await db.commit()
                            if outline_result.get("status") == "failed":
                                err = outline_result.get(
                                    "error", "大纲生成失败"
                                )
                                self._status[project_id]["errors"].append(err)

                except Exception as exc:
                    # 7. 出错时休息 30 秒重试
                    err_msg = f"{type(exc).__name__}: {exc}"
                    logger.error(
                        "项目 %s 连续写作出错: %s",
                        project_id, err_msg, exc_info=True,
                    )
                    self._status[project_id]["errors"].append(err_msg)
                    await asyncio.sleep(30)
                    continue

                # 6. 短暂休息 5 秒后继续下一章
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            # 任务被取消（stop 调用）
            logger.info("项目 %s 连续写作任务被取消", project_id)
            raise
        finally:
            # 清理运行状态
            if project_id in self._status:
                self._status[project_id]["running"] = False
                self._status[project_id]["current_chapter"] = None

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    async def _find_unfinished_chapter(
        self, db: Any, project_id: uuid.UUID
    ) -> Optional[int]:
        """查找第一个未完成的章节（word_count=0），返回其章节号。

        Args:
            db: 异步数据库会话。
            project_id: 项目 ID。

        Returns:
            第一个未完成章节的章节号，若无则返回 None。
        """
        stmt = (
            select(Chapter.chapter_no)
            .where(Chapter.project_id == project_id, Chapter.word_count == 0)
            .order_by(Chapter.chapter_no.asc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _has_world_bible(
        self, db: Any, project_id: uuid.UUID
    ) -> bool:
        """检查项目是否已有世界观圣经。

        Args:
            db: 异步数据库会话。
            project_id: 项目 ID。

        Returns:
            True 如果项目至少有一个 WorldBible 记录。
        """
        stmt = select(func.count(WorldBible.id)).where(
            WorldBible.project_id == project_id
        )
        result = await db.execute(stmt)
        count = result.scalar_one()
        return count > 0


# 全局单例：供 API 路由直接使用
continuous_production_service = ContinuousProductionService()
