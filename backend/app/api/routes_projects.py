"""项目路由 - GET/POST /api/projects, GET /api/projects/{id}。

返回结构对齐前端 ``Project`` TypeScript 类型：
- ``description`` = ``synopsis``（保留 synopsis 以兼容旧调用）
- ``target_chapters`` 从 ``extra`` JSON 读取
- ``current_chapter`` = ``current_chapter_no``
- ``config`` 由 ``extra`` dict 组装
"""
from __future__ import annotations

import io
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.repositories.project import ProjectRepository

logger = logging.getLogger("app.api.routes_projects")

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
    config: Optional[dict[str, Any]] = None  # 前端传入的完整 config dict


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
        "length_type",
        "length_label",
        "chapter_range",
        "estimated_words",
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

    # 前端传入的 config dict 中的所有字段也存入 extra
    # （包含 length_type / chapter_range / themes 等新字段）
    if payload.config:
        for k, v in payload.config.items():
            if v is not None:
                extra[k] = v

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


# ------------------------------------------------------------------
# 上传大纲文件
# ------------------------------------------------------------------

def _extract_text_from_docx(content: bytes) -> str:
    """从 docx 文件字节中提取纯文本。"""
    import docx

    doc = docx.Document(io.BytesIO(content))
    paragraphs = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _extract_text_from_file(filename: str, content: bytes) -> str:
    """根据文件扩展名提取文本内容。

    支持 .docx / .txt / .md
    """
    lower = filename.lower()
    if lower.endswith(".docx"):
        return _extract_text_from_docx(content)
    elif lower.endswith((".txt", ".md", ".markdown")):
        # 尝试 UTF-8，回退 GBK
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("gbk", errors="replace")
    else:
        raise HTTPException(
            status_code=400,
            detail="不支持的文件格式，请上传 .docx / .txt / .md 文件",
        )


@router.post("/{project_id}/upload-outline")
async def upload_outline(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """上传详细大纲文件（.docx / .txt / .md）。

    文件内容会被提取为纯文本，存储在 Project.extra["outline_text"] 中。
    生成世界观圣经时会自动读取并传给 StoryArchitect 作为参考。
    """
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:  # 5MB 限制
        raise HTTPException(status_code=413, detail="文件过大，请上传 5MB 以下的文件")

    text = _extract_text_from_file(file.filename or "outline.txt", content)

    if not text.strip():
        raise HTTPException(status_code=400, detail="文件内容为空")

    # 存入 extra dict
    # 注意：SQLAlchemy JSON 字段需要显式标记为已修改
    from sqlalchemy.orm.attributes import flag_modified

    extra = project.extra or {}
    extra["outline_text"] = text
    extra["outline_filename"] = file.filename
    project.extra = extra
    flag_modified(project, "extra")
    await db.commit()

    logger.info(
        "项目 %s 上传大纲: %s (%d 字符)",
        project_id, file.filename, len(text),
    )

    return {
        "ok": True,
        "project_id": str(project_id),
        "filename": file.filename,
        "char_count": len(text),
        "preview": text[:500],
    }


@router.get("/{project_id}/outline")
async def get_outline(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """获取项目已上传的大纲文本。"""
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    extra = project.extra or {}
    text = extra.get("outline_text", "")
    return {
        "project_id": str(project_id),
        "filename": extra.get("outline_filename"),
        "char_count": len(text),
        "text": text,
    }


@router.delete("/{project_id}/outline")
async def delete_outline(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """删除项目已上传的大纲。"""
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    extra = project.extra or {}
    extra.pop("outline_text", None)
    extra.pop("outline_filename", None)
    project.extra = extra
    flag_modified(project, "extra")
    await db.commit()

    return {"ok": True, "project_id": str(project_id)}


# ------------------------------------------------------------------
# 删除项目（级联删除所有关联数据）
# ------------------------------------------------------------------
@router.delete("/{project_id}")
async def delete_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """删除项目及其所有关联数据。

    通过 SQL 级联删除章节、设定、会话等关联表数据，
    最后删除项目本身。
    """
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # 级联删除关联数据
    from sqlalchemy import text

    pid = str(project_id)

    # 先查所有有 project_id 列的表
    try:
        result = await db.execute(text("""
            SELECT table_name FROM information_schema.columns
            WHERE column_name = 'project_id' AND table_schema = 'public'
        """))
        direct_tables = [row[0] for row in result.fetchall()]
    except Exception:
        direct_tables = []

    # 先删 manuscript_blocks 和 chapter_summaries（通过 chapter_id 关联）
    for sub_sql in [
        "DELETE FROM manuscript_blocks WHERE chapter_id IN (SELECT id FROM chapters WHERE project_id = :pid)",
        "DELETE FROM chapter_summaries WHERE chapter_id IN (SELECT id FROM chapters WHERE project_id = :pid)",
    ]:
        try:
            await db.execute(text(sub_sql), {"pid": pid})
        except Exception as e:
            logger.warning("删除关联数据失败: %s", e)
            await db.rollback()
            break

    # 再删有 project_id 列的表
    for table in direct_tables:
        if table == "projects":
            continue
        try:
            await db.execute(text(f"DELETE FROM {table} WHERE project_id = :pid"), {"pid": pid})
        except Exception as e:
            logger.warning("删除 %s 失败: %s", table, e)
            await db.rollback()

    # 最后用 ORM 删除项目本身
    try:
        await db.delete(project)
        await db.commit()
    except Exception as e:
        await db.rollback()
        # ORM 删除失败，用 SQL 直接删
        await db.execute(text("DELETE FROM projects WHERE id = :pid"), {"pid": pid})
        await db.commit()

    return {"ok": True, "project_id": pid}
