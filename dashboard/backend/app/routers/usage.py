"""Persistent per-domain data-usage history.

Auth-gated (reveals visited domains) and gated again by ``connection_tracking``
so the whole feature is absent unless the operator opted in at deploy time.
"""

import asyncio
import calendar
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from app.auth import RequireAuth
from app.config import settings
from app.services import store, usage_service

router = APIRouter(prefix="/usage", tags=["usage"])


def _require_enabled() -> None:
    if not settings.connection_tracking:
        raise HTTPException(status_code=404)


def _period_range(period: str) -> tuple[int, int]:
    """Resolve a period to [since, until) unix bounds in the host's local time."""
    now = datetime.now().astimezone()
    until = int(now.timestamp()) + 1
    if period == "all":
        return 0, until
    if period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    else:  # "today" (default)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()), until


def _build_series(period: str, since: int, rows: list[tuple[int, int]]) -> list[dict]:
    """Bucket per-hour totals into the chart's grain: hourly for today, daily else.
    Spans the whole period (empty buckets included) so the axis is the full window."""
    if period == "today":
        size, count = 3600, 24
    elif period == "week":
        size, count = 86400, 7
    elif period == "month":
        d = datetime.fromtimestamp(since)
        size, count = 86400, calendar.monthrange(d.year, d.month)[1]
    else:
        return []
    values = [0] * count
    for hour_ts, total in rows:
        idx = (hour_ts - since) // size
        if 0 <= idx < count:
            values[idx] += int(total or 0)
    return [{"ts": since + i * size, "value": values[i]} for i in range(count)]


@router.get("/top", dependencies=[RequireAuth])
async def usage_top(period: str = "today", limit: int = 20):
    _require_enabled()
    since, until = _period_range(period)
    # Fold in the unflushed in-memory buffer so "today" is current to the second.
    await usage_service.recorder.flush_now()
    top, (total_down, total_up), series_rows = await asyncio.gather(
        asyncio.to_thread(store.query_usage_top, since, until, limit),
        asyncio.to_thread(store.query_usage_total, since, until),
        asyncio.to_thread(store.query_usage_series, since, until),
    )
    grand = total_down + total_up
    sites = [
        {"domain": d, "down": down, "up": up, "share": (down + up) / grand if grand else 0.0}
        for d, down, up in top
    ]
    return {
        "period": period,
        "since": since,
        "until": until,
        "updatedAt": int(time.time()),
        "totalDown": total_down,
        "totalUp": total_up,
        "sites": sites,
        "series": _build_series(period, since, series_rows),
    }


@router.delete("", dependencies=[RequireAuth])
async def clear_usage():
    _require_enabled()
    await asyncio.to_thread(usage_service.recorder.purge_all)
    return {"cleared": True}
