"""The always-on monitor: probe on a self-tuning cadence, fold each sample into the
RLE segment timeline (one row per state change), and keep a live snapshot in memory.

This is composite system health, not per-proxy: the dashboard runs in the host netns
where the default route is the Clash TUN, so a plain probe egresses through the active
system proxy (exactly the path a user experiences). See ``probe`` for cause attribution.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

from app.services import store
from app.services.health import probe
from app.services.health.cause import cause_for, describe_detail, detail_for
from app.services.health.settings import (
    HEARTBEAT_S,
    INTERVAL_BAD_S,
    INTERVAL_OK_S,
    MAX_GAP_S,
    PRUNE_EVERY,
    RETENTION_DAYS,
    Status,
)

logger = logging.getLogger(__name__)


@dataclass
class OpenSegment:
    """The in-progress segment, mirrored from disk. ``end`` tracks the live edge every
    tick; ``persisted_end`` is the last value actually written (only every HEARTBEAT_S)."""

    start: int | None = None
    end: int = 0
    status: Status = "unknown"
    regime: str = "normal"
    persisted_end: int = 0


class HealthMonitor:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._latest: dict | None = None
        self._samples = 0
        self._last_prune = 0
        self._loaded = False
        self._open = OpenSegment()
        # Serializes the loop tick and any forced (tap-to-recheck) probe.
        self._lock = asyncio.Lock()

    def latest(self) -> dict | None:
        return self._latest

    def alive_ts(self) -> int | None:
        """The live edge for read-time tail handling: when the monitor last measured,
        or None if it hasn't probed since (re)starting (reads then fall back to disk)."""
        return int(self._latest["ts"]) if self._latest is not None else None

    def _ensure_loaded(self) -> None:
        """Recover the open segment from disk once, so a restart continues the same
        run-length timeline instead of orphaning the previously open segment."""
        if self._loaded:
            return
        seg = store.latest_segment()
        if seg is not None:
            start, end, status, regime, _detail = seg
            self._open = OpenSegment(start, end, status, regime, persisted_end=end)
        self._loaded = True

    def _persist(self, now: int, status: str, regime: str, detail: str | None) -> None:
        """Fold one probe into the segment timeline (runs in a worker thread).

        Same state as the open segment -> just extend its end (a heartbeat, written
        only every HEARTBEAT_S). A real change -> close the open segment and open a
        new one, stamping its onset ``detail`` (the granular "why"). A gap wider than
        MAX_GAP_S (monitor was down) -> insert an ``unknown`` span for the time we
        couldn't measure before opening the new state.
        """
        open_ = self._open
        if open_.start is None:
            store.add_segment(now, now, status, regime, detail)
        elif now - open_.end > MAX_GAP_S:
            store.set_segment_end(open_.start, open_.end)
            store.add_segment(open_.end, now, "unknown", open_.regime)
            store.add_segment(now, now, status, regime, detail)
        elif status != open_.status or regime != open_.regime:
            store.set_segment_end(open_.start, now)
            store.add_segment(now, now, status, regime, detail)
        else:
            open_.end = now
            if now - open_.persisted_end >= HEARTBEAT_S:
                store.set_segment_end(open_.start, now)
                open_.persisted_end = now
            self._maybe_prune(now)
            return
        open_.start = open_.end = open_.persisted_end = now
        open_.status, open_.regime = status, regime
        self._maybe_prune(now)

    def _maybe_prune(self, now: int) -> None:
        if self._samples - self._last_prune >= PRUNE_EVERY:
            self._last_prune = self._samples
            store.prune_health(now - RETENTION_DAYS * 86400)

    async def _probe_and_record(self) -> float:
        """Run one combined probe (connectivity + DNS), fold it into the timeline,
        refresh the live snapshot, and return how long to wait before the next tick."""
        result = await probe.classify()
        now = int(time.time())
        self._samples += 1
        detail = detail_for(result.status, result.latency_ms, result.dns_ok)
        await asyncio.to_thread(self._persist, now, result.status, result.regime, detail)
        kind, label = cause_for(result.status, result.regime)
        self._latest = {
            "ts": now,
            "status": result.status,
            "regime": result.regime,
            "latencyMs": result.latency_ms,
            "reachable": result.reachable,
            "cause": kind,
            "causeLabel": label,
            "causeDetail": describe_detail(result.status, result.regime, detail),
            # Derived from the same probe (resolution succeeded), not a separate call.
            "dns": {"success": result.dns_ok},
        }
        return INTERVAL_OK_S if result.status == "good" else INTERVAL_BAD_S

    async def _tick(self) -> float:
        async with self._lock:
            return await self._probe_and_record()

    async def probe_now(self) -> dict | None:
        """Force an immediate probe (the header's tap-to-recheck), independent of
        the loop's cadence. Returns the fresh live snapshot."""
        store.init_health()
        async with self._lock:
            self._ensure_loaded()
            await self._probe_and_record()
        return self._latest

    async def _run(self) -> None:
        while True:
            try:
                delay = await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("health monitor tick failed; retrying", exc_info=True)
                delay = INTERVAL_BAD_S
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    def start(self) -> None:
        store.init_health()
        self._ensure_loaded()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Persist the live edge so post-shutdown reads know exactly when we last measured.
        async with self._lock:
            if self._open.start is not None and self._open.end != self._open.persisted_end:
                await asyncio.to_thread(store.set_segment_end, self._open.start, self._open.end)
                self._open.persisted_end = self._open.end

    def purge_all(self) -> None:
        self._latest = None
        self._open = OpenSegment()
        self._loaded = True  # nothing on disk now; no need to reload
        store.wipe_health()
        store.init_health()


monitor = HealthMonitor()
