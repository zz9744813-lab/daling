"""SQLAlchemy 2.0 异步数据库引擎、会话工厂与声明式基类。

兼容 PostgreSQL 与 SQLite：
- PostgreSQL: 使用 asyncpg 驱动 + 连接池参数
- SQLite:     使用 aiosqlite 驱动，不传 pool_size/max_overflow

包含平台无关的 GUID 类型（PostgreSQL 用原生 UUID，其它方言用 String(36)）。
"""

from __future__ import annotations

import uuid
from typing import AsyncGenerator, Optional

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from app.core.config import settings


# ---------------------------------------------------------------------------
# 平台无关 GUID 类型
# ---------------------------------------------------------------------------
class GUID(TypeDecorator):
    """平台无关 GUID 类型。

    - PostgreSQL: 使用原生 ``UUID(as_uuid=True)``
    - 其它方言(SQLite 等): 使用 ``String(36)`` 存储

    在 Python 侧统一返回 ``uuid.UUID`` 对象。
    """

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value if dialect.name == "postgresql" else str(value)
        # 字符串 / 其它
        parsed = uuid.UUID(str(value))
        return parsed if dialect.name == "postgresql" else str(parsed)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


# ---------------------------------------------------------------------------
# 声明式基类（SQLAlchemy 2.0 风格）
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    """所有 ORM 模型的统一声明式基类。"""

    pass


# ---------------------------------------------------------------------------
# 引擎与会话工厂
# ---------------------------------------------------------------------------
def _build_engine():
    """根据数据库 URL 构建异步引擎，SQLite 不传连接池参数。"""
    url = settings.DATABASE_URL
    kwargs: dict = {"echo": False, "future": True}
    if not settings.is_sqlite:
        # PostgreSQL / MySQL: 标准连接池
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        kwargs["pool_pre_ping"] = True
    else:
        # SQLite: 允许跨线程 + 开启 WAL 模式解决并发锁问题
        #
        # WAL（Write-Ahead Logging）模式：
        #   - 读操作不再阻塞写操作，写操作也不再阻塞读操作
        #   - 多个连接可以同时读，写操作串行但不再锁住整个文件
        #   - 适合"一写多读"场景（我们的 Pipeline 就是这种模式）
        #
        # busy_timeout=5000:
        #   - 遇到锁时自动等待最多 5 秒再重试，而非立即报错
        #   - 足以让正在执行的写操作完成
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": 30,  # 连接超时 30 秒
        }
    engine = create_async_engine(url, **kwargs)

    # SQLite 专属：每次连接时设置 PRAGMA
    if settings.is_sqlite:
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            """每个新连接建立时设置 SQLite PRAGMA。

            - journal_mode=WAL:  写前日志模式，允许并发读写
            - busy_timeout=5000: 锁等待 5 秒（而非立即失败）
            - synchronous=NORMAL: WAL 模式下的推荐同步级别
            - foreign_keys=ON:   启用外键约束
            """
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _build_engine()

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：提供异步数据库会话并在请求结束后关闭。"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """启动时创建所有表（开发环境便捷用，生产环境应使用 alembic 迁移）。"""
    # 确保所有模型已导入，以便 metadata 注册
    import app.db.models  # noqa: F401

    # SQLite: 确保数据库文件所在目录存在
    if settings.is_sqlite:
        import os
        from urllib.parse import urlparse

        parsed = urlparse(settings.DATABASE_URL)
        db_path = parsed.path.lstrip("/")
        if db_path:
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite ALTER TABLE 添加新列（如果不存在），幂等执行。
        # create_all 不会为已存在的表添加列，故手动迁移。
        # 注意：仅在 SQLite 下执行，PostgreSQL 的 create_all 已包含完整列定义
        if settings.is_sqlite:
            # Columns introduced after the first durable-run prototype.  The
            # desktop edition may already have a continuous_runs table, while
            # create_all only creates missing tables and never adds columns.
            for statement in (
                "ALTER TABLE continuous_runs ADD COLUMN generation INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE continuous_runs ADD COLUMN fencing_token INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    await conn.execute(text(statement))
                except Exception:
                    pass  # column already exists
            try:
                await conn.execute(
                    text("ALTER TABLE model_bindings ADD COLUMN agent_role VARCHAR(50)")
                )
            except Exception:
                pass  # 列已存在

            # Autonomous production must never schedule the same chapter or
            # version twice. Existing duplicates intentionally fail startup so
            # they can be repaired instead of being silently preserved.
            unique_indexes = (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_chapter_project_no "
                "ON chapters(project_id, chapter_no)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_chapter_version_no "
                "ON chapter_versions(chapter_id, version_no)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_chapter_block_no "
                "ON manuscript_blocks(chapter_id, block_no)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_storyline_volume_project_no "
                "ON storyline_volumes(project_id, volume_no)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_storyline_beat_chapter "
                "ON storyline_beats(project_id, chapter_no)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_chapter_summary_project_no "
                "ON chapter_summaries(project_id, chapter_no)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_story_state_project_no "
                "ON current_story_states(project_id, chapter_no)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_book_memory_key "
                "ON book_memory(project_id, memory_type, key)",
            )
            for statement in unique_indexes:
                await conn.execute(text(statement))
            try:
                await conn.execute(
                    text(
                        "ALTER TABLE model_bindings ADD COLUMN project_id CHAR(36) "
                        "REFERENCES projects(id) ON DELETE CASCADE"
                    )
                )
            except Exception:
                pass  # 列已存在


async def dispose_db() -> Optional[Exception]:
    """关闭数据库引擎，返回异常（如有）但不抛出。"""
    try:
        await engine.dispose()
        return None
    except Exception as exc:  # noqa: BLE001
        return exc
