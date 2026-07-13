"""通用异步 CRUD Repository 基类。

提供基于 SQLAlchemy 2.0 AsyncSession 的通用增删改查能力，
子类通过指定 ``model`` 即可复用。
"""

from __future__ import annotations

import uuid
from typing import Any, Generic, Optional, TypeVar, overload

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """通用异步 CRUD 基类。"""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id_: uuid.UUID | str) -> Optional[ModelT]:
        """按主键获取单条记录。"""
        return await self.session.get(self.model, id_)

    async def get_by_id(self, id_: uuid.UUID | str) -> Optional[ModelT]:
        """get 的语义别名。"""
        return await self.get(id_)

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[ModelT]:
        """分页列表查询，filters 为 {字段名: 值} 的等值过滤。"""
        stmt = select(self.model).offset(offset).limit(limit)
        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key):
                    stmt = stmt.where(getattr(self.model, key) == value)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, filters: Optional[dict[str, Any]] = None) -> int:
        """计数。"""
        stmt = select(func.count()).select_from(self.model)
        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key):
                    stmt = stmt.where(getattr(self.model, key) == value)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def create(self, **kwargs: Any) -> ModelT:
        """创建记录。"""
        obj = self.model(**kwargs)
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    @overload
    async def update(self, obj: ModelT, **kwargs: Any) -> ModelT: ...

    async def update(self, obj: ModelT, **kwargs: Any) -> ModelT:
        """更新记录的指定字段。"""
        for key, value in kwargs.items():
            if hasattr(obj, key):
                setattr(obj, key, value)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        """删除单条记录。"""
        await self.session.delete(obj)
        await self.session.flush()

    async def delete_by_id(self, id_: uuid.UUID | str) -> bool:
        """按主键删除，返回是否删除了记录。"""
        stmt = sa_delete(self.model).where(self.model.id == id_)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return (result.rowcount or 0) > 0
