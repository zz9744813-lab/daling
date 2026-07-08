"""连续写作路由 - 启动 / 停止 / 查询 24 小时连续写作状态。

路由前缀: /api/pipeline
- POST /{project_id}/continuous/start   启动连续写作
- POST /{project_id}/continuous/stop    停止连续写作
- GET  /{project_id}/continuous/status  查询连续写作状态
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.continuous_production import continuous_production_service

logger = logging.getLogger("app.api.continuous")

router = APIRouter(prefix="/api/pipeline", tags=["continuous"])


class ContinuousStartRequest(BaseModel):
    """启动连续写作请求体。

    target_chapters 为 None 表示无限制（持续写作）。
    """

    target_chapters: Optional[int] = Field(
        default=None, ge=1, description="目标章数，None 表示无限制"
    )


@router.post("/{project_id}/continuous/start")
async def start_continuous(
    project_id: uuid.UUID,
    payload: Optional[ContinuousStartRequest] = None,
):
    """启动连续写作。

    参数 target_chapters 可选（默认无限制），
    启动后后台任务会持续生成章节，每完成一章自动继续下一章。
    """
    # 兼容无请求体的情况
    target_chapters = payload.target_chapters if payload else None
    result = await continuous_production_service.start(
        project_id=project_id,
        target_chapters=target_chapters,
    )
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


@router.get("/{project_id}/continuous/status")
async def continuous_status(project_id: uuid.UUID):
    """返回连续写作状态。

    Returns:
        { running, current_chapter, completed_chapters, errors, started_at }
    """
    status = continuous_production_service.get_status(project_id)
    return status
