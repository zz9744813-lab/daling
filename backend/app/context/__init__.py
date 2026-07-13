"""Continuity Engine (Phase 5) - 上下文编译、Canon 管理与连续性校验。

导出:
    ContextCompiler     - 按预算比例组装上下文
    CompiledContext     - 编译后的上下文数据结构
    CanonManager        - canon_facts 表 CRUD 与冲突检测
    CanonConflictError  - immutable 事实冲突异常
    ContinuityGuard     - 四步连续性校验
    ContinuityResult    - 校验结果数据结构
    BookMemoryManager   - 作品记忆管理
"""

from app.context.book_memory_manager import BookMemoryManager
from app.context.canon_manager import CanonConflictError, CanonManager
from app.context.compiler import CompiledContext, ContextCompiler
from app.context.continuity_guard import ContinuityGuard, ContinuityResult

__all__ = [
    "ContextCompiler",
    "CompiledContext",
    "CanonManager",
    "CanonConflictError",
    "ContinuityGuard",
    "ContinuityResult",
    "BookMemoryManager",
]
