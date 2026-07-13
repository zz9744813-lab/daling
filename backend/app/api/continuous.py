"""连续写作路由 - 启动 / 停止 / 查询 24 小时连续写作状态。

路由前缀: /api/pipeline
- POST /{project_id}/continuous/start   启动连续写作
- POST /{project_id}/continuous/stop    停止连续写作
- GET  /{project_id}/continuous/status  查询连续写作状态
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.continuous_production import (
    ManualPipelineConflictError,
    continuous_production_service,
)

logger = logging.getLogger("app.api.continuous")

router = APIRouter(prefix="/api/pipeline", tags=["continuous"])


class ContinuousStartRequest(BaseModel):
    """Durable autopilot contract supplied by the operator."""

    target_chapters: Optional[int] = Field(
        default=None, ge=1, description="目标章数，None 表示无限制"
    )
    autonomy_level: Literal["L2", "L3", "L4"] = "L3"
    quality_threshold: int = Field(default=85, ge=50, le=100)
    max_rewrite_rounds: int = Field(default=2, ge=0, le=5)
    chapter_delay_seconds: int = Field(default=5, ge=0, le=86400)
    error_backoff_seconds: int = Field(default=30, ge=1, le=3600)
    max_consecutive_failures: int = Field(default=3, ge=1, le=20)
    circuit_cooldown_seconds: int = Field(default=300, ge=1, le=3600)
    quality_failure_action: Literal["retry", "pause"] = "retry"
    max_quality_retry_cycles: int = Field(default=2, ge=0, le=10)
    quality_retry_backoff_seconds: int = Field(default=30, ge=0, le=3600)
    learning_interval_chapters: int = Field(default=1, ge=1, le=50)
    daily_cost_limit: Optional[float] = Field(default=None, ge=0)
    daily_token_limit: Optional[int] = Field(default=None, ge=1000)

    def policy(self) -> dict:
        return {
            "quality_threshold": self.quality_threshold,
            "max_rewrite_rounds": self.max_rewrite_rounds,
            "chapter_delay_seconds": self.chapter_delay_seconds,
            "error_backoff_seconds": self.error_backoff_seconds,
            "max_consecutive_failures": self.max_consecutive_failures,
            "circuit_cooldown_seconds": self.circuit_cooldown_seconds,
            "quality_failure_action": self.quality_failure_action,
            "max_quality_retry_cycles": self.max_quality_retry_cycles,
            "quality_retry_backoff_seconds": self.quality_retry_backoff_seconds,
            "learning_interval_chapters": self.learning_interval_chapters,
            "daily_cost_limit": self.daily_cost_limit,
            "daily_token_limit": self.daily_token_limit,
        }


class ContinuousPauseRequest(BaseModel):
    reason: str = Field(default="用户暂停", max_length=1000)


@router.post("/{project_id}/continuous/start")
async def start_continuous(
    project_id: uuid.UUID,
    payload: Optional[ContinuousStartRequest] = None,
):
    """启动连续写作。

    参数 target_chapters 可选（默认无限制），
    启动后后台任务会持续生成章节，每完成一章自动继续下一章。
    """
    contract = payload or ContinuousStartRequest()
    try:
        result = await continuous_production_service.start(
            project_id=project_id,
            target_chapters=contract.target_chapters,
            autonomy_level=contract.autonomy_level,
            policy=contract.policy(),
        )
    except ManualPipelineConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info("启动连续写作: project_id=%s, result=%s", project_id, result)
    return result


@router.post("/{project_id}/continuous/stop")
async def stop_continuous(project_id: uuid.UUID):
    """停止连续写作。

    取消后台循环任务，清理运行状态。
    """
    result = await continuous_production_service.stop(project_id)
    logger.info("停止连续写作: project_id=%s", project_id)
    return result


@router.post("/{project_id}/continuous/pause")
async def pause_continuous(
    project_id: uuid.UUID,
    payload: Optional[ContinuousPauseRequest] = None,
):
    reason = payload.reason if payload else "用户暂停"
    return await continuous_production_service.pause(project_id, reason=reason)


@router.post("/{project_id}/continuous/resume")
async def resume_continuous(project_id: uuid.UUID):
    try:
        return await continuous_production_service.resume(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}/continuous/status")
async def continuous_status(project_id: uuid.UUID):
    """返回连续写作状态。

    Returns:
        { running, current_chapter, completed_chapters, errors, started_at }
    """
    status = await continuous_production_service.get_status(project_id)
    return status


@router.get("/{project_id}/continuous/events")
async def continuous_events(
    project_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
):
    return await continuous_production_service.list_events(project_id, limit=limit)
