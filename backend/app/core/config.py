"""应用配置 - 基于 Pydantic Settings v2 读取 .env 环境变量。"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置，所有字段均可通过环境变量 / .env 文件覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 运行环境 ----
    APP_ENV: str = Field(
        default="development",
        description="运行环境: development/staging/production",
    )
    APP_HOST: str = Field(default="0.0.0.0")
    APP_PORT: int = Field(default=8000)

    # ---- 数据库 ----
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./data/novel_os.db",
        description="SQLAlchemy async 数据库连接串",
    )

    # ---- Redis ----
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # ---- 默认 LLM Provider ----
    DEFAULT_PROVIDER: str = Field(default="openai_compatible")
    DEFAULT_MODEL: str = Field(default="")
    DEFAULT_BASE_URL: str = Field(default="")
    DEFAULT_API_KEY: str = Field(default="")

    # ---- OpenAI 兼容 Provider ----
    OPENAI_COMPATIBLE_BASE_URL: str = Field(default="")
    OPENAI_COMPATIBLE_API_KEY: str = Field(default="")
    OPENAI_COMPATIBLE_MODEL: str = Field(default="")

    # ---- Anthropic Provider ----
    ANTHROPIC_BASE_URL: str = Field(default="")
    ANTHROPIC_API_KEY: str = Field(default="")
    ANTHROPIC_MODEL: str = Field(default="")

    # ---- 对象存储 ----
    OBJECT_STORAGE_DRIVER: str = Field(default="local", description="local/s3")
    OBJECT_STORAGE_LOCAL_DIR: str = Field(default="./data/blobstore")

    # ---- 在线学习 ----
    ENABLE_ONLINE_LEARNING: bool = Field(default=False)

    # ---- 真实 Provider 冒烟测试 ----
    ENABLE_REAL_PROVIDER_SMOKE: bool = Field(default=False)
    REAL_PROVIDER_SMOKE_MAX_CHAPTERS: int = Field(default=1)
    REAL_PROVIDER_SMOKE_MAX_WORDS: int = Field(default=800)

    # ---- CORS ----
    CORS_ORIGINS: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="允许的前端来源，逗号分隔",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """返回单例 Settings。"""
    return Settings()


# 默认导出单例，便于直接 import
settings: Settings = get_settings()
