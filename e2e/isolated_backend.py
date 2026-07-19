"""Expose the real FastAPI app while suppressing autonomous worker dispatch.

The mutating browser suite verifies durable Continuous API transitions and must
never contact an external model.  Start/pause/resume/stop still execute the real
service and database code; only the asynchronous chapter worker is not spawned.
"""

from __future__ import annotations

from typing import Any

from app.main import app
from app.services.continuous_production import continuous_production_service


def _do_not_spawn_model_worker(*_args: Any, **_kwargs: Any) -> None:
    return None


continuous_production_service._spawn = _do_not_spawn_model_worker  # type: ignore[method-assign]

__all__ = ["app"]
