"""自定义异常类层次结构。

所有业务异常继承自 ``AppError``，便于统一异常处理中间件捕获。
"""
from __future__ import annotations


class AppError(Exception):
    """所有应用层异常的基类。"""

    status_code: int = 500
    error_code: str = "app_error"

    def __init__(self, message: str = "", *, error_code: str | None = None, status_code: int | None = None):
        super().__init__(message or self.error_code)
        self.message = message or self.error_code
        if error_code is not None:
            self.error_code = error_code
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self) -> dict:
        return {
            "error": self.error_code,
            "message": self.message,
            "status_code": self.status_code,
        }


# ---------------------------------------------------------------------------
# Provider 相关
# ---------------------------------------------------------------------------
class ProviderError(AppError):
    """LLM Provider 调用异常。"""

    status_code = 502
    error_code = "provider_error"


class ProviderUnavailableError(ProviderError):
    """Provider 不可用（连接超时 / 服务端错误）。"""

    error_code = "provider_unavailable"


class ProviderRateLimitError(ProviderError):
    """Provider 触发限流。"""

    status_code = 429
    error_code = "provider_rate_limited"


# ---------------------------------------------------------------------------
# Pipeline 相关
# ---------------------------------------------------------------------------
class PipelineError(AppError):
    """Pipeline 执行异常。"""

    status_code = 500
    error_code = "pipeline_error"


class PipelineStateError(PipelineError):
    """Pipeline 状态非法（如重复执行 / 缺少前置步骤）。"""

    status_code = 409
    error_code = "pipeline_state_error"


# ---------------------------------------------------------------------------
# 锁与并发
# ---------------------------------------------------------------------------
class LockError(AppError):
    """分布式锁获取失败（资源被占用）。"""

    status_code = 409
    error_code = "lock_error"


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
class ValidationError(AppError):
    """业务数据校验异常。"""

    status_code = 422
    error_code = "validation_error"


# ---------------------------------------------------------------------------
# 资源未找到
# ---------------------------------------------------------------------------
class NotFoundError(AppError):
    """请求的资源不存在。"""

    status_code = 404
    error_code = "not_found"


# ---------------------------------------------------------------------------
# Canon（设定一致性）
# ---------------------------------------------------------------------------
class CanonError(AppError):
    """设定冲突 / 一致性校验异常。"""

    status_code = 409
    error_code = "canon_error"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
class ConfigError(AppError):
    """配置缺失或非法。"""

    status_code = 500
    error_code = "config_error"
