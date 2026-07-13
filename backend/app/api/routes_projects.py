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
import re
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.production_guard import manual_production_guard
from app.db import get_db
from app.db.models.chapter import Chapter
from app.db.models.project import ProjectConfig
from app.db.models.storyline import StorylineVolume
from app.db.models.world import WorldBible
from app.db.repositories.project import ProjectRepository
from app.services.preparation_state import (
    artifact_stale_state,
    outline_source,
    record_outline_change,
)

logger = logging.getLogger("app.api.routes_projects")

router = APIRouter(prefix="/api/projects", tags=["projects"])


class CreativeConversationMessage(BaseModel):
    """A persisted message from the project-design conversation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class ProjectCreate(BaseModel):
    """创建项目请求体。

    前端可能传 ``description`` / ``target_chapters`` / ``autonomy_level`` 等扩展字段，
    这些字段会写入 Project.extra JSON dict（不新增数据库列）。
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=255)
    genre: Optional[str] = None
    synopsis: Optional[str] = None
    description: Optional[str] = None  # 与 synopsis 同义，前端用 description
    target_words: int = 0
    target_chapters: Optional[int] = None
    autonomy_level: Optional[str] = None  # L1 / L2 / L3 / L4
    words_per_chapter: Optional[int] = None
    chapter_words: Optional[int] = None  # 旧前端字段，写入时归一为 words_per_chapter
    language: Optional[str] = None
    tone: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    type: Optional[str] = None  # 前端 Project.type
    config: Optional[dict[str, Any]] = None  # 前端传入的完整 config dict
    custom_prompt: Optional[str] = Field(default=None, max_length=20_000)
    creative_conversation: Optional[list[CreativeConversationMessage]] = Field(
        default=None,
        max_length=80,
        validation_alias=AliasChoices("creative_conversation", "conversation"),
    )
    creation_blueprint: Optional[dict[str, Any]] = Field(
        default=None,
        validation_alias=AliasChoices("creation_blueprint", "blueprint"),
    )


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


_INTERNAL_EXTRA_KEYS = {
    "outline_text",
    "outline_filename",
    "raw_prompt",
    "raw_response",
    "provider_config",
    "api_key",
    "api_key_enc",
    "secret",
    "token",
}


def _build_config(extra: dict[str, Any]) -> dict[str, Any]:
    """Return all public project config while excluding internal/large values."""
    if not extra:
        return {}
    return {
        key: value
        for key, value in extra.items()
        if value is not None and key not in _INTERNAL_EXTRA_KEYS and not key.startswith("_")
    }


def _normalise_conversation(value: Any) -> Optional[list[dict[str, str]]]:
    if value is None:
        return None
    if not isinstance(value, list):
        raise HTTPException(status_code=422, detail="creative_conversation 必须是消息数组")
    if len(value) > 80:
        raise HTTPException(status_code=422, detail="creative_conversation 最多 80 条消息")
    messages: list[dict[str, str]] = []
    for item in value:
        try:
            message = (
                item
                if isinstance(item, CreativeConversationMessage)
                else CreativeConversationMessage.model_validate(item)
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"creative_conversation 消息格式无效: {exc}",
            ) from exc
        messages.append(message.model_dump())
    return messages


def _prepare_project_extra(payload: ProjectCreate) -> tuple[dict[str, Any], Optional[str]]:
    """Build canonical extra/config and detach the transactional custom prompt."""
    extra = dict(payload.config or {})
    for key in _INTERNAL_EXTRA_KEYS:
        extra.pop(key, None)

    # Backward-compatible aliases are accepted but never persisted twice.
    if "chapter_words" in extra and "words_per_chapter" not in extra:
        extra["words_per_chapter"] = extra["chapter_words"]
    extra.pop("chapter_words", None)

    conversation_value: Any = payload.creative_conversation
    if conversation_value is None:
        conversation_value = extra.pop("creative_conversation", None)
    if conversation_value is None:
        conversation_value = extra.pop("conversation", None)
    conversation = _normalise_conversation(conversation_value)
    if conversation is not None:
        extra["creative_conversation"] = conversation

    blueprint_value: Any = payload.creation_blueprint
    if blueprint_value is None:
        blueprint_value = extra.pop("creation_blueprint", None)
    if blueprint_value is None:
        blueprint_value = extra.pop("blueprint", None)
    if blueprint_value is not None:
        if not isinstance(blueprint_value, dict):
            raise HTTPException(status_code=422, detail="creation_blueprint 必须是对象")
        extra["creation_blueprint"] = blueprint_value

    config_prompt = extra.pop("custom_prompt", None)
    custom_prompt = payload.custom_prompt if payload.custom_prompt is not None else config_prompt
    if custom_prompt is not None:
        custom_prompt = str(custom_prompt).strip()
        if len(custom_prompt) > 20_000:
            raise HTTPException(status_code=422, detail="custom_prompt 最多 20000 字符")

    canonical_values = {
        "target_chapters": payload.target_chapters,
        "autonomy_level": payload.autonomy_level,
        "language": payload.language,
        "tone": payload.tone,
        "provider": payload.provider,
        "model": payload.model,
        "type": payload.type,
    }
    words_per_chapter = (
        payload.words_per_chapter
        if payload.words_per_chapter is not None
        else payload.chapter_words
    )
    if words_per_chapter is not None:
        canonical_values["words_per_chapter"] = words_per_chapter
    for key, value in canonical_values.items():
        if value is not None:
            extra[key] = value
    return extra, custom_prompt


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
    # Project、完整公开 config、创作对话和 custom prompt 使用同一个
    # AsyncSession/事务写入；任一步失败都会由 get_db 统一回滚。
    extra, custom_prompt = _prepare_project_extra(payload)

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

    if custom_prompt is not None:
        db.add(
            ProjectConfig(
                project_id=obj.id,
                key="custom_system_prompt",
                value={"text": custom_prompt},
            )
        )
        await db.flush()
    return _to_out(obj)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    repo = ProjectRepository(db)
    obj = await repo.get_by_id(project_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _to_out(obj)


# ------------------------------------------------------------------
# 自定义系统提示词（类似 Gemini Gems / Custom GPTs）
# ------------------------------------------------------------------


@router.get("/{project_id}/custom-prompt")
async def get_custom_prompt(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """获取项目的自定义系统提示词。

    从 project_configs 表读取 key='custom_system_prompt' 的记录，
    返回 {"text": "..."} 格式。如不存在则返回空文本。
    """
    from sqlalchemy import select as sa_select

    from app.db.models.project import ProjectConfig

    result = await db.execute(
        sa_select(ProjectConfig).where(
            ProjectConfig.project_id == project_id,
            ProjectConfig.key == "custom_system_prompt",
        )
    )
    config = result.scalar_one_or_none()
    if config and config.value:
        text = config.value.get("text", "") if isinstance(config.value, dict) else str(config.value)
    else:
        text = ""
    return {"project_id": str(project_id), "text": text}


@router.put("/{project_id}/custom-prompt")
async def update_custom_prompt(
    project_id: uuid.UUID,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """更新项目的自定义系统提示词。

    payload: {"text": "..."}
    存入 project_configs 表，key='custom_system_prompt'，value={"text": "..."}。
    这些指令会注入到所有 Agent 的 system prompt 中。
    """
    from sqlalchemy import select as sa_select

    text = payload.get("text", "")

    result = await db.execute(
        sa_select(ProjectConfig).where(
            ProjectConfig.project_id == project_id,
            ProjectConfig.key == "custom_system_prompt",
        )
    )
    config = result.scalar_one_or_none()

    if config:
        # 更新已有记录
        config.value = {"text": text}
        flag_modified(config, "value")
    else:
        # 创建新记录
        config = ProjectConfig(
            project_id=project_id,
            key="custom_system_prompt",
            value={"text": text},
        )
        db.add(config)

    await db.commit()

    logger.info(
        "项目 %s 自定义系统提示词已更新 (%d 字符)",
        project_id,
        len(text),
    )

    return {"ok": True, "project_id": str(project_id), "text": text}


# ------------------------------------------------------------------
# 上传大纲文件
# ------------------------------------------------------------------

MAX_OUTLINE_BYTES = 5 * 1024 * 1024
ALLOWED_OUTLINE_EXTENSIONS = {".docx", ".txt", ".md", ".markdown"}


def _outline_extension(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_OUTLINE_EXTENSIONS:
        allowed = " / ".join(sorted(ALLOWED_OUTLINE_EXTENSIONS))
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件格式 {extension or '(无扩展名)'}；仅支持 {allowed}",
        )
    return extension


def _extract_text_from_docx(content: bytes) -> str:
    """从 docx 文件字节中提取纯文本。"""
    import docx
    from docx.opc.exceptions import PackageNotFoundError

    try:
        doc = docx.Document(io.BytesIO(content))
    except (PackageNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail="DOCX 文件已损坏或不是有效文档") from exc
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
    extension = _outline_extension(filename)
    if extension == ".docx":
        return _extract_text_from_docx(content)
    if extension in {".txt", ".md", ".markdown"}:
        # 尝试 UTF-8，回退 GBK
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("gbk", errors="replace")
    raise HTTPException(status_code=415, detail="不支持的文件格式")


async def _read_outline_upload(file: UploadFile) -> tuple[str, str, bytes, str]:
    """Validate and extract an outline upload without mutating project state."""
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="上传文件缺少文件名")
    extension = _outline_extension(filename)
    content = await file.read(MAX_OUTLINE_BYTES + 1)
    if len(content) > MAX_OUTLINE_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 5MB 限制")
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    text = _extract_text_from_file(filename, content).strip()
    if not text:
        raise HTTPException(status_code=400, detail="文件内容为空")
    return filename, extension, content, text


async def _derived_artifact_presence(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> tuple[bool, bool]:
    bible_count = int(
        await db.scalar(
            select(func.count(WorldBible.id)).where(WorldBible.project_id == project_id)
        )
        or 0
    )
    volume_count = int(
        await db.scalar(
            select(func.count(StorylineVolume.id)).where(
                StorylineVolume.project_id == project_id
            )
        )
        or 0
    )
    chapter_count = int(
        await db.scalar(select(func.count(Chapter.id)).where(Chapter.project_id == project_id))
        or 0
    )
    return bible_count > 0, volume_count > 0 or chapter_count > 0


@router.post("/outline/inspect")
async def inspect_outline(file: UploadFile = File(...)):
    """创建项目前解析大纲，让创作对话真正看到文件内容。"""
    filename, extension, content, text = await _read_outline_upload(file)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chapter_headings = [
        line
        for line in lines
        if re.match(r"^(第[一二三四五六七八九十百千万零〇两\d]+[章节回]|chapter\s+\d+)", line, re.I)
    ]
    volume_headings = [
        line
        for line in lines
        if re.match(r"^(第[一二三四五六七八九十百千万零〇两\d]+卷|volume\s+\d+)", line, re.I)
    ]
    return {
        "ok": True,
        "filename": filename,
        "extension": extension,
        "size_bytes": len(content),
        "char_count": len(text),
        "line_count": len(lines),
        "chapter_heading_count": len(chapter_headings),
        "volume_heading_count": len(volume_headings),
        "chapter_headings": chapter_headings[:30],
        "volume_headings": volume_headings[:20],
        "preview": text[:1200],
        "text": text,
    }


@router.post("/{project_id}/upload-outline")
async def upload_outline(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _manual_guard: None = Depends(manual_production_guard),
):
    """上传详细大纲文件（.docx / .txt / .md）。

    文件内容会被提取为纯文本，存储在 Project.extra["outline_text"] 中。
    生成世界观圣经时会自动读取并传给 StoryArchitect 作为参考。
    """
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    filename, extension, content, text = await _read_outline_upload(file)

    world_bible_exists, outline_exists = await _derived_artifact_presence(db, project_id)
    previous_source = outline_source(dict(project.extra or {}))
    reason = "outline_replaced" if previous_source["present"] else "outline_added"
    extra, changed = record_outline_change(
        dict(project.extra or {}),
        text=text,
        filename=filename,
        world_bible_exists=world_bible_exists,
        outline_exists=outline_exists,
        reason=reason,
    )
    project.extra = extra
    flag_modified(project, "extra")
    await db.commit()

    source = outline_source(extra)
    stale_artifacts = [
        artifact
        for artifact, state in (
            (
                "world_bible",
                artifact_stale_state(extra, "world_bible", exists=world_bible_exists),
            ),
            ("outline", artifact_stale_state(extra, "outline", exists=outline_exists)),
        )
        if state["stale"]
    ]

    logger.info(
        "项目 %s 上传大纲: %s (%d 字符)",
        project_id,
        filename,
        len(text),
    )

    return {
        "ok": True,
        "project_id": str(project_id),
        "filename": filename,
        "extension": extension,
        "size_bytes": len(content),
        "char_count": len(text),
        "preview": text[:500],
        "outline_changed": changed,
        "outline_source_revision": source["revision"],
        "outline_source_sha256": source["sha256"],
        "preparation_stale": bool(stale_artifacts),
        "stale_artifacts": stale_artifacts,
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
    source = outline_source(dict(extra))
    return {
        "project_id": str(project_id),
        "filename": extra.get("outline_filename"),
        "char_count": len(text),
        "text": text,
        "source_revision": source["revision"],
        "source_sha256": source["sha256"],
    }


@router.delete("/{project_id}/outline")
async def delete_outline(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _manual_guard: None = Depends(manual_production_guard),
):
    """删除项目已上传的大纲。"""
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    world_bible_exists, outline_exists = await _derived_artifact_presence(db, project_id)
    extra, changed = record_outline_change(
        dict(project.extra or {}),
        text="",
        filename=None,
        world_bible_exists=world_bible_exists,
        outline_exists=outline_exists,
        reason="outline_removed",
    )
    project.extra = extra
    flag_modified(project, "extra")
    await db.commit()

    return {
        "ok": True,
        "project_id": str(project_id),
        "outline_changed": changed,
        "outline_source_revision": outline_source(extra)["revision"],
        "preparation_stale": changed and (world_bible_exists or outline_exists),
    }


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
        result = await db.execute(
            text("""
            SELECT table_name FROM information_schema.columns
            WHERE column_name = 'project_id' AND table_schema = 'public'
        """)
        )
        direct_tables = [row[0] for row in result.fetchall()]
    except Exception:
        direct_tables = []

    # 先删 manuscript_blocks 和 chapter_summaries（通过 chapter_id 关联）
    for sub_sql in [
        "DELETE FROM manuscript_blocks WHERE chapter_id IN "
        "(SELECT id FROM chapters WHERE project_id = :pid)",
        "DELETE FROM chapter_summaries WHERE chapter_id IN "
        "(SELECT id FROM chapters WHERE project_id = :pid)",
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
    except Exception:
        await db.rollback()
        # ORM 删除失败，用 SQL 直接删
        await db.execute(text("DELETE FROM projects WHERE id = :pid"), {"pid": pid})
        await db.commit()

    return {"ok": True, "project_id": pid}
