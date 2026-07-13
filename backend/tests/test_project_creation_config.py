"""Project creation must persist its complete public configuration atomically."""

from __future__ import annotations

import pytest
from app.api.routes_projects import ProjectCreate, create_project, get_project
from app.core.database import Base
from app.db.models.project import ProjectConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_project_config_roundtrip_and_transactional_custom_prompt():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        payload = ProjectCreate(
            title="余烬天门",
            genre="末世修仙",
            chapter_words=3200,
            custom_prompt="保持冷峻克制，不泄露后续谜底。",
            creative_conversation=[
                {"role": "user", "content": "主角使用能力会失去记忆"},
                {"role": "assistant", "content": "他最不能忘记的人是谁？"},
            ],
            creation_blueprint={"readiness": 85, "missing_fields": ["ending_preference"]},
            config={
                "themes": ["身份", "记忆"],
                "pov": "第三人称限制",
                "tense": "过去时",
                "volume_count": 4,
                "creative_prompt": "现代都市灵气复苏，科技与修仙并存。",
                "outline_text": "不能绕过上传接口写入的大文本",
            },
        )
        created = await create_project(payload, session)
        await session.flush()

        assert created.config["words_per_chapter"] == 3200
        assert "chapter_words" not in created.config
        assert created.config["themes"] == ["身份", "记忆"]
        assert created.config["pov"] == "第三人称限制"
        assert created.config["volume_count"] == 4
        assert created.config["creative_conversation"][0]["role"] == "user"
        assert "outline_text" not in created.config

        prompt = (
            await session.execute(
                select(ProjectConfig).where(
                    ProjectConfig.project_id == created.id,
                    ProjectConfig.key == "custom_system_prompt",
                )
            )
        ).scalar_one()
        assert prompt.value == {"text": "保持冷峻克制，不泄露后续谜底。"}

        fetched = await get_project(created.id, session)
        assert fetched.config == created.config

    await engine.dispose()
