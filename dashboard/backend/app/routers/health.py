"""Overall network-health history.

Not auth-gated: unlike connections/usage this carries no visited-host data, just
whether the link was up and how fast. Always on (a conservative background probe),
so the dashboard can show outages and quality degradation over time.
"""

import asyncio
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services import health as hs
from app.services import store
from app.services.system_proxy import Capability, registry

router = APIRouter(prefix="/health", tags=["health"])


async def _connection_tracking() -> bool:
    """Whether live/usage connection tracking is active: opted in at deploy time
    AND the current controller can report connections."""
    if not settings.connection_tracking:
        return False
    caps = await asyncio.to_thread(registry.active_capabilities)
    return Capability.CONNECTIONS in caps

# window -> (duration_s, bucket_size_s). One storage path serves every window now:
# the RLE segment timeline. store.health_segments includes the segment straddling
# `since`, so the state in force when the window opened seeds the first bucket.
_WINDOWS: dict[str, tuple[int, int]] = {
    "24h": (86400, 3600),
    "7d": (7 * 86400, 4 * 3600),
    "30d": (30 * 86400, 86400),
    "90d": (90 * 86400, 86400),
}


async def _load_segments(since: int, until: int) -> tuple[list, list]:
    """Stored rows (carry regime, for incidents) plus clipped (start, end, status)
    segments with the live tail resolved (for buckets/summary)."""
    rows = await asyncio.to_thread(store.health_segments, since, until)
    segments = hs.clip_segments(rows, until, hs.monitor.alive_ts())
    return rows, segments


def _within_window(incidents: list[dict], since: int, until: int) -> list[dict]:
    """Drop incidents that ended before the window opened (the straddling segment can
    surface one whose recovery predates ``since``); ongoing ones use ``until`` as end."""
    return [i for i in incidents if (i["end"] or until) > since]


def _today_bounds() -> tuple[int, int]:
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()), int(now.timestamp()) + 1


# After a restart the monitor's in-memory status is empty until its first probe;
# fall back to the last stored sample, but only while it's still fresh.
_CURRENT_STALE_S = int(hs.MAX_GAP_S * 2)


async def _current_status() -> dict | None:
    live = hs.monitor.latest()
    if live is not None:
        return live
    seg = await asyncio.to_thread(store.latest_segment)
    if seg is None:
        return None
    _start, end_ts, status, regime, detail = seg
    if int(time.time()) - int(end_ts) > _CURRENT_STALE_S:
        return None
    return hs.snapshot_from_segment(end_ts, status, regime, detail)


@router.get("/current")
async def health_current():
    since, until = _today_bounds()
    rows, segments = await _load_segments(since, until)
    incidents = hs.find_incidents(rows, until, hs.monitor.alive_ts())
    incidents = _within_window(incidents, since, until)
    summary = hs.summarize(segments, since, until)
    current, tracking = await asyncio.gather(_current_status(), _connection_tracking())
    return {
        "now": int(time.time()),
        "current": current,
        "connectionTracking": tracking,
        "today": {
            "since": since,
            "until": until,
            "uptimePct": summary["uptimePct"],
            "secs": summary["secs"],
            "incidentCount": len(incidents),
            "downtimeS": summary["secs"]["outage"],
            "degradedS": summary["secs"]["degraded"],
        },
    }


@router.post("/check")
async def health_check():
    """Force an immediate probe (header tap-to-recheck) and return fresh status."""
    await hs.monitor.probe_now()
    return await health_current()


@router.get("/timeline")
async def health_timeline(window: str = "24h"):
    spec = _WINDOWS.get(window)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"unknown window: {window}")
    duration, size = spec
    until = int(time.time())
    since = until - duration
    _rows, segments = await _load_segments(since, until)
    buckets = hs.bucketize(segments, since, until, size)
    summary = hs.summarize(segments, since, until)
    return {
        "window": window,
        "since": since,
        "until": until,
        "bucketSize": size,
        "buckets": buckets,
        "summary": summary,
    }


@router.get("/incidents")
async def health_incidents(window: str = "24h", min_duration: int = 60):
    spec = _WINDOWS.get(window)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"unknown window: {window}")
    duration = spec[0]
    until = int(time.time())
    since = until - duration
    rows = await asyncio.to_thread(store.health_segments, since, until)
    incidents = hs.find_incidents(rows, until, hs.monitor.alive_ts(), min_duration_s=min_duration)
    incidents = _within_window(incidents, since, until)
    incidents.sort(key=lambda i: i["start"], reverse=True)
    return {"window": window, "since": since, "until": until, "incidents": incidents, "count": len(incidents)}


@router.delete("")
async def clear_health():
    await asyncio.to_thread(hs.monitor.purge_all)
    return {"cleared": True}
