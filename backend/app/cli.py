"""命令行入口 - ``nos`` 命令。

提供开发常用的便捷子命令，例如启动服务、初始化数据库等。
"""
from __future__ import annotations

import asyncio

import click

from app.core.config import settings
from app.core.logging import setup_logging


@click.group()
def cli() -> None:
    """Novel Agent OS 命令行工具。"""
    setup_logging()


@cli.command()
def initdb() -> None:
    """创建所有数据库表。"""
    from app.core.database import init_db

    click.echo("正在创建数据库表...")
    asyncio.run(init_db())
    click.echo("数据库表创建完成。")


@cli.command()
def info() -> None:
    """显示当前配置摘要。"""
    click.echo(f"APP_ENV        = {settings.APP_ENV}")
    click.echo(f"DATABASE_URL   = {settings.DATABASE_URL}")
    click.echo(f"REDIS_URL      = {settings.REDIS_URL}")
    click.echo(f"DEFAULT_PROVIDER = {settings.DEFAULT_PROVIDER}")
    click.echo(f"is_sqlite      = {settings.is_sqlite}")


def main() -> None:
    """pyproject.toml 中 nos 脚本的入口。"""
    cli()


if __name__ == "__main__":
    main()
