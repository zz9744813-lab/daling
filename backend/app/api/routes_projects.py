"""项目路由 - GET/POST /api/projects, GET /api/projects/{id}。

返回结构对齐前端 ``Project`` TypeScript 类型：
- ``description`` = ``synopsis``（保留 synopsis 以兼容旧调用）
- ``target_chapters`` 从 ``extra`` JSON 读取
- ``current_chapter`` = ``current_chapter_no``
- ``config`` 由 ``extra`` dict 组装
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.repositories.project import ProjectRepository

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    """创建项目请求体。

    前端可能传 ``description`` / ``target_chapters`` / ``autonomy_level`` 等扩展字段，
    这些字段会写入 Project.extra JSON dict（不新增数据库列）。
    """

    title: str = Field(..., min_length=1, max_length=255)
    genre: Optional[str] = None
    synopsis: Optional[str] = None
    description: Optional[str] = None  # 与 synopsis 同义，前端用 description
    target_words: int = 0
    target_chapters: Optional[int] = None
    autonomy_level: Optional[str] = None  # L1 / L2 / L3 / L4
    words_per_chapter: Optional[int] = None
    language: Optional[str] = None
    tone: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    type: Optional[str] = None  # 前端 Project.type


class ProjectOut(BaseModel):
    """返回给前端的项目结构（对齐前端 Project 类型）。"""

    id: str
    title: str
    type: Optional[str] = None
    genre: Optional[str] = None
    description: Optional[str] = None
    synopsis: Optional[str] = None  # 兼容旧字段
    target_words: int = 0
    target_chapters: Optional[int] = None
    current_chapter: Optional[int] = None
    current_chapter_no: Optional[int] = None  # 兼容旧字段
    config: Optional[dict[str, Any]] = None
    status: str = "draft"
    progress: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = {"from_attributes": True}


def _build_config(extra: dict[str, Any]) -> dict[str, Any]:
    """从 Project.extra 组装前端 ProjectConfig 结构。"""
    if not extra:
        return {}
    config: dict[str, Any] = {}
    for key in (
        "target_chapters",
        "words_per_chapter",
        "autonomy_level",
        "provider",
        "model",
        "language",
        "genre",
        "tone",
    ):
        if key in extra and extra[key] is not None:
            config[key] = extra[key]
    return config


def _to_out(obj: Any) -> ProjectOut:
    """将 Project ORM 对象序列化为前端期望的 Project 结构。"""
    extra: dict[str, Any] = obj.extra or {}
    synopsis = obj.synopsis
    return ProjectOut(
        id=str(obj.id),
        title=obj.title,
        type=extra.get("type"),
        genre=obj.genre,
        description=synopsis,
        synopsis=synopsis,
        target_words=obj.target_words,
        target_chapters=extra.get("target_chapters"),
        current_chapter=obj.current_chapter_no,
        current_chapter_no=obj.current_chapter_no,
        config=_build_config(extra) or None,
        status=obj.status,
        progress=extra.get("progress"),
        created_at=obj.created_at.isoformat() if obj.created_at else None,
        updated_at=obj.updated_at.isoformat() if obj.updated_at else None,
    )


@router.get("", response_model=list[ProjectOut])
@router.get("/", response_model=list[ProjectOut], include_in_schema=False)
async def list_projects(
    status: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    repo = ProjectRepository(db)
    items = await repo.list_projects(offset=offset, limit=limit, status=status)
    return [_to_out(p) for p in items]


@router.post("", response_model=ProjectOut, status_code=201)
@router.post("/", response_model=ProjectOut, status_code=201, include_in_schema=False)
async def create_project(payload: ProjectCreate, db: AsyncSession = Depends(get_db)):
    # 将扩展字段写入 extra dict（不新增数据库列）
    extra: dict[str, Any] = {}
    if payload.target_chapters is not None:
        extra["target_chapters"] = payload.target_chapters
    if payload.autonomy_level is not None:
        extra["autonomy_level"] = payload.autonomy_level
    if payload.words_per_chapter is not None:
        extra["words_per_chapter"] = payload.words_per_chapter
    if payload.language is not None:
        extra["language"] = payload.language
    if payload.tone is not None:
        extra["tone"] = payload.tone
    if payload.provider is not None:
        extra["provider"] = payload.provider
    if payload.model is not None:
        extra["model"] = payload.model
    if payload.type is not None:
        extra["type"] = payload.type

    # description 与 synopsis 同义，优先用 synopsis
    synopsis = payload.synopsis if payload.synopsis is not None else payload.description

    repo = ProjectRepository(db)
    obj = await repo.create_project(
        title=payload.title,
        genre=payload.genre,
        synopsis=synopsis,
        target_words=payload.target_words,
        status="draft",
        extra=extra,
    )
    return _to_out(obj)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    repo = ProjectRepository(db)
    obj = await repo.get_by_id(project_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _to_out(obj)
