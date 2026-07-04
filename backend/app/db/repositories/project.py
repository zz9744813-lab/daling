"""Project 仓储 - 项目相关查询。"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.project import Project, ProjectConfig
from app.db.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[Project]):
    """Project 表的仓储实现。"""

    model = Project

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_id(self, id_: uuid.UUID | str) -> Optional[Project]:
        """按 ID 获取项目。"""
        pk = uuid.UUID(str(id_)) if isinstance(id_, str) else id_
        stmt = select(Project).where(Project.id == pk)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_projects(
        self, *, offset: int = 0, limit: int = 50, status: Optional[str] = None
    ) -> list[Project]:
        """列出项目，可按 status 过滤。"""
        stmt = select(Project).offset(offset).limit(limit).order_by(Project.created_at.desc())
        if status:
            stmt = stmt.where(Project.status == status)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_project(self, **kwargs) -> Project:
        """创建项目。"""
        return await self.create(**kwargs)

    async def get_config(self, project_id: uuid.UUID, key: str) -> Optional[ProjectConfig]:
        """获取项目配置项。"""
        stmt = select(ProjectConfig).where(
            ProjectConfig.project_id == project_id,
            ProjectConfig.key == key,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_config(self, project_id: uuid.UUID, key: str, value) -> ProjectConfig:
        """设置 / 更新项目配置项（upsert 语义）。"""
        existing = await self.get_config(project_id, key)
        if existing:
            existing.value = value
            await self.session.flush()
            await self.session.refresh(existing)
            return existing
        cfg = ProjectConfig(project_id=project_id, key=key, value=value)
        self.session.add(cfg)
        await self.session.flush()
        await self.session.refresh(cfg)
        return cfg

    async def list_configs(self, project_id: uuid.UUID) -> list[ProjectConfig]:
        """列出项目所有配置项。"""
        stmt = select(ProjectConfig).where(ProjectConfig.project_id == project_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
