"""Shared API dependency for project-scoped production mutations."""

from __future__ import annotations

import uuid
from typing import AsyncIterator

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.services.continuous_production import (
    ManualPipelineConflictError,
    continuous_production_service,
)


async def manual_production_guard(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AsyncIterator[None]:
    """Reserve a project or return a stable 409 while autopilot owns it."""
    try:
        async with continuous_production_service.manual_pipeline_guard(project_id, db):
            yield
    except ManualPipelineConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "continuous_run_active",
                "message": str(exc),
                "continuous_status": exc.status,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["manual_production_guard"]
