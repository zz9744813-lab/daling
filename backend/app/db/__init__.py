"""数据库包：声明式基类、引擎、会话与全部 ORM 模型。"""

from app.core.database import (
    GUID,
    Base,
    async_session_factory,
    dispose_db,
    engine,
    get_db,
    init_db,
)
from app.db.models import *  # noqa: F401, F403  确保所有模型注册到 metadata

__all__ = [
    "Base",
    "GUID",
    "engine",
    "async_session_factory",
    "get_db",
    "init_db",
    "dispose_db",
]
