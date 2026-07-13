"""日志配置 - 优先使用 rich 的 RichHandler，不可用时回退到标准 logging。"""

from __future__ import annotations

import logging

try:
    from rich.logging import RichHandler

    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False
    RichHandler = None  # type: ignore


def setup_logging(level: str = "INFO") -> None:
    """配置全局日志。rich 可用时使用 RichHandler，否则使用标准 StreamHandler。"""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    if _RICH_AVAILABLE:
        logging.basicConfig(
            level=numeric_level,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[
                RichHandler(
                    rich_tracebacks=True,
                    show_path=False,
                    markup=True,
                    log_time_format="[%X]",
                )
            ],
            force=True,
        )
    else:
        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
            handlers=[logging.StreamHandler()],
            force=True,
        )

    # 降低第三方库噪音
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger。"""
    return logging.getLogger(name)
