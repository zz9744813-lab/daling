"""用量统计路由 - GET /api/usage/{project_id}。

提供按天 / 按 Agent 分组的 token 用量与成本统计：
- GET /{project_id}          — 今日 token 用量、成本、按 agent 分组、近 7 天趋势
- GET /{project_id}/daily    — 按天统计明细
- GET /{project_id}/by-agent — 按 Agent 分组统计
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.models.session import AgentRun
from app.db.models.usage import UsageDailyStat

logger = logging.getLogger("app.api.usage")

router = APIRouter(prefix="/api/usage", tags=["usage"])


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------
class DailyStat(BaseModel):
    stat_date: str
    total_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


class AgentStat(BaseModel):
    agent_name: str
    total_runs: int = 0
    success_count: int = 0
    failed_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    avg_duration_ms: Optional[float] = None


class UsageView(BaseModel):
    project_id: str
    # 今日汇总
    today_input_tokens: int = 0
    today_output_tokens: int = 0
    today_cost: float = 0.0
    today_requests: int = 0
    # 总计
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    total_requests: int = 0
    # 按 Agent 分组
    by_agent: list[AgentStat] = []
    # 近 7 天趋势
    daily: list[DailyStat] = []


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("/{project_id}", response_model=UsageView)
async def get_usage(
    project_id: uuid.UUID,
    start_date: Optional[date] = Query(None, description="起始日期"),
    end_date: Optional[date] = Query(None, description="结束日期"),
    db: AsyncSession = Depends(get_db),
):
    """用量统计：今日 token 用量、成本、按 agent 分组、近 7 天趋势。

    如果不指定日期范围，默认返回近 7 天数据。
    """
    today = date.today()
    if end_date is None:
        end_date = today
    if start_date is None:
        start_date = today - timedelta(days=6)

    # 1. 按天统计（UsageDailyStat）
    daily_stats = await _query_daily_stats(db, project_id, start_date, end_date)

    # 2. 按 Agent 分组统计（AgentRun）
    agent_stats = await _query_agent_stats(db, project_id, start_date, end_date)

    # 3. 今日汇总
    today_input = 0
    today_output = 0
    today_cost = 0.0
    today_requests = 0
    for d in daily_stats:
        if d.stat_date == today.isoformat():
            today_input = d.input_tokens
            today_output = d.output_tokens
            today_cost = d.cost
            today_requests = d.total_requests
            break

    # 4. 总计（范围内）
    total_input = sum(d.input_tokens for d in daily_stats)
    total_output = sum(d.output_tokens for d in daily_stats)
    total_cost = sum(d.cost for d in daily_stats)
    total_requests = sum(d.total_requests for d in daily_stats)

    return UsageView(
        project_id=str(project_id),
        today_input_tokens=today_input,
        today_output_tokens=today_output,
        today_cost=round(today_cost, 4),
        today_requests=today_requests,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cost=round(total_cost, 4),
        total_requests=total_requests,
        by_agent=agent_stats,
        daily=daily_stats,
    )


@router.get("/{project_id}/daily", response_model=list[DailyStat])
async def get_daily_usage(
    project_id: uuid.UUID,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """按天统计明细。"""
    today = date.today()
    if end_date is None:
        end_date = today
    if start_date is None:
        start_date = today - timedelta(days=30)
    return await _query_daily_stats(db, project_id, start_date, end_date)


@router.get("/{project_id}/by-agent", response_model=list[AgentStat])
async def get_usage_by_agent(
    project_id: uuid.UUID,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """按 Agent 分组统计。"""
    today = date.today()
    if end_date is None:
        end_date = today
    if start_date is None:
        start_date = today - timedelta(days=30)
    return await _query_agent_stats(db, project_id, start_date, end_date)


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------
async def _query_daily_stats(
    db: AsyncSession,
    project_id: uuid.UUID,
    start_date: date,
    end_date: date,
) -> list[DailyStat]:
    """查询按天的用量统计。

    优先使用 UsageDailyStat 表；如果该表无数据，则从 AgentRun 聚合。
    """
    # 先查 UsageDailyStat
    stmt = (
        select(UsageDailyStat)
        .where(
            UsageDailyStat.project_id == project_id,
            UsageDailyStat.stat_date >= start_date,
            UsageDailyStat.stat_date <= end_date,
        )
        .order_by(UsageDailyStat.stat_date.asc())
    )
    result = await db.execute(stmt)
    stats = result.scalars().all()

    if stats:
        return [
            DailyStat(
                stat_date=s.stat_date.isoformat(),
                total_requests=s.total_requests,
                input_tokens=s.input_tokens,
                output_tokens=s.output_tokens,
                cost=round(s.cost, 4),
            )
            for s in stats
        ]

    # UsageDailyStat 无数据，从 AgentRun 聚合
    return await _aggregate_from_agent_runs(db, project_id, start_date, end_date)


async def _aggregate_from_agent_runs(
    db: AsyncSession,
    project_id: uuid.UUID,
    start_date: date,
    end_date: date,
) -> list[DailyStat]:
    """从 AgentRun 按天聚合用量统计。"""
    from datetime import datetime
    from datetime import timezone as tz

    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=tz.utc)
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=tz.utc)

    stmt = (
        select(AgentRun)
        .where(
            AgentRun.project_id == project_id,
            AgentRun.created_at >= start_dt,
            AgentRun.created_at < end_dt,
        )
        .order_by(AgentRun.created_at.asc())
    )
    result = await db.execute(stmt)
    runs = result.scalars().all()

    # 按天分组
    daily_map: dict[str, dict[str, Any]] = {}
    for run in runs:
        if run.created_at is None:
            continue
        day_key = run.created_at.date().isoformat()
        if day_key not in daily_map:
            daily_map[day_key] = {
                "total_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
            }
        daily_map[day_key]["total_requests"] += 1
        daily_map[day_key]["input_tokens"] += run.input_tokens
        daily_map[day_key]["output_tokens"] += run.output_tokens
        daily_map[day_key]["cost"] += run.cost

    # 填充缺失的日期
    stats = []
    current = start_date
    while current <= end_date:
        day_key = current.isoformat()
        data = daily_map.get(
            day_key,
            {
                "total_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
            },
        )
        stats.append(
            DailyStat(
                stat_date=day_key,
                total_requests=data["total_requests"],
                input_tokens=data["input_tokens"],
                output_tokens=data["output_tokens"],
                cost=round(data["cost"], 4),
            )
        )
        current += timedelta(days=1)

    return stats


async def _query_agent_stats(
    db: AsyncSession,
    project_id: uuid.UUID,
    start_date: date,
    end_date: date,
) -> list[AgentStat]:
    """按 Agent 分组统计用量。"""
    from datetime import datetime
    from datetime import timezone as tz

    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=tz.utc)
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=tz.utc)

    stmt = select(AgentRun).where(
        AgentRun.project_id == project_id,
        AgentRun.created_at >= start_dt,
        AgentRun.created_at < end_dt,
    )
    result = await db.execute(stmt)
    runs = result.scalars().all()

    # 按 agent_name 分组
    agent_map: dict[str, dict[str, Any]] = {}
    for run in runs:
        name = run.agent_name
        if name not in agent_map:
            agent_map[name] = {
                "total_runs": 0,
                "success_count": 0,
                "failed_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
                "durations": [],
            }
        agent_map[name]["total_runs"] += 1
        if run.status == "success":
            agent_map[name]["success_count"] += 1
        elif run.status == "failed":
            agent_map[name]["failed_count"] += 1
        agent_map[name]["input_tokens"] += run.input_tokens
        agent_map[name]["output_tokens"] += run.output_tokens
        agent_map[name]["cost"] += run.cost
        if run.duration_ms:
            agent_map[name]["durations"].append(run.duration_ms)

    stats = []
    for name, data in sorted(agent_map.items(), key=lambda x: x[1]["cost"], reverse=True):
        durations = data["durations"]
        avg_duration = sum(durations) / len(durations) if durations else None
        stats.append(
            AgentStat(
                agent_name=name,
                total_runs=data["total_runs"],
                success_count=data["success_count"],
                failed_count=data["failed_count"],
                input_tokens=data["input_tokens"],
                output_tokens=data["output_tokens"],
                cost=round(data["cost"], 4),
                avg_duration_ms=round(avg_duration, 1) if avg_duration else None,
            )
        )

    return stats
