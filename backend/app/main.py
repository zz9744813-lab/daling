"""FastAPI 应用入口。

- 创建 FastAPI app 并配置 CORS（允许 localhost:5173）
- 注册全部 API 路由
- lifespan: 启动时初始化日志 / Redis / 数据库建表；关闭时释放连接
- /health 健康检查端点
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    book_memory_router,
    brain_router,
    canon_router,
    cockpit_router,
    continuous_router,
    evolution_router,
    pipeline_router,
    planning_router,
    projects_router,
    provider_router,
    review_queue_router,
    storyline_router,
    usage_router,
)
from app.core.config import settings
from app.core.database import dispose_db, init_db
from app.core.logging import setup_logging
from app.core.redis import close_redis, init_redis, is_redis_available

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动与关闭钩子。"""
    setup_logging("DEBUG" if not settings.is_production else "INFO")
    logger.info("启动 Novel Agent OS 后端 (env=%s)", settings.APP_ENV)

    # Redis（可选，失败不阻断）
    await init_redis()

    # 数据库建表（开发环境便捷用）
    try:
        await init_db()
        logger.info("数据库表已就绪")
    except Exception as exc:  # noqa: BLE001
        logger.error("数据库初始化失败: %s", exc)

    yield

    # 关闭
    logger.info("关闭 Novel Agent OS 后端...")
    await close_redis()
    err = await dispose_db()
    if err is not None:
        logger.warning("关闭数据库引擎时出错: %s", err)


def create_app() -> FastAPI:
    """构造 FastAPI 应用实例。"""
    app = FastAPI(
        title="Novel Agent OS",
        description="自主小说创作智能体后端",
        version="0.5.0",
        lifespan=lifespan,
    )

    # ---- CORS ----
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 注册路由 ----
    app.include_router(projects_router)
    app.include_router(pipeline_router)
    app.include_router(cockpit_router)
    app.include_router(storyline_router)
    app.include_router(brain_router)
    app.include_router(review_queue_router)
    app.include_router(evolution_router)
    app.include_router(usage_router)
    app.include_router(provider_router)
    app.include_router(canon_router)
    app.include_router(book_memory_router)
    app.include_router(planning_router)
    app.include_router(continuous_router)

    # ---- 健康检查 ----
    @app.get("/health", tags=["system"])
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "app": "novel-agent-os",
                "version": "0.5.0",
                "env": settings.APP_ENV,
                "database": "sqlite" if settings.is_sqlite else "postgresql",
                "redis": is_redis_available(),
            }
        )

    @app.get("/", tags=["system"])
    async def root() -> dict:
        return {"name": "Novel Agent OS", "docs": "/docs", "health": "/health"}

    return app


app = create_app()
