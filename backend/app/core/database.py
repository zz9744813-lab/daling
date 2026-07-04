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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
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
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        kwargs["pool_pre_ping"] = True
    else:
        # SQLite 需要允许跨线程共享连接（FastAPI 多线程）
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(url, **kwargs)


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
        try:
            await conn.execute(
                text("ALTER TABLE model_bindings ADD COLUMN agent_role VARCHAR(50)")
            )
        except Exception:
            pass  # 列已存在
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
