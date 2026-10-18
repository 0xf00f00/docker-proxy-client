"""Once a minute, drain every collector into one raw sample row; record restarts and clock sync as events."""

import asyncio
import contextlib
import json
import logging
import time

from app.config import settings
from app.services import docker_service, store
from app.services.health.collectors import host, paths
from app.services.health.collectors.containers import RestartWatch
from app.services.health.collectors.edge_logs import EdgeLogFollower
from app.services.health.collectors.feed import FeedAggregator
from app.services.health.settings import (
    CORE_CONTAINERS,
    EDGE_CONTAINERS,
    EVENT_RETENTION_DAYS,
    MONITOR_GAP_S,
    SAMPLE_RETENTION_DAYS,
)

logger = logging.getLogger(__name__)

_LOG_READ_TIMEOUT_S = 600
_PRUNE_TICKS = 1440


def start_event(
    last: tuple[str, int, int, int] | None,
    last_event: tuple[int, str] | None,
    boot_id: str,
    uptime_s: float,
) -> tuple[str, dict, int] | None:
    """(kind, data, event uptime) for what happened since the last sample; a new
    boot without a stop marker after the last sample was abrupt (power loss)."""
    if last is None:
        return None
    prev_boot, prev_up, prev_ts, prev_synced = last
    graceful = last_event is not None and last_event[1] == "stop" and last_event[0] >= prev_up
    if prev_boot != boot_id:
        data = {"prev_boot": prev_boot, "last_ts": prev_ts + 60, "last_synced": bool(prev_synced), "graceful": graceful}
        return "boot", data, 0
    gap = uptime_s - (prev_up + 60)
    if gap > MONITOR_GAP_S:
        return "monitor_gap", {"gap_s": int(gap), "graceful": graceful}, int(uptime_s)
    return None


class Recorder:
    def __init__(self) -> None:
        self.feed = FeedAggregator()
        self._edges = EdgeLogFollower(EDGE_CONTAINERS, lambda: docker_service.new_client(_LOG_READ_TIMEOUT_S))
        self._restarts = RestartWatch(CORE_CONTAINERS)
        self._boot = host.boot_id()
        self._synced = False
        self._nic: tuple[int, int] | None = None
        self._ticks = 0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        store.init_health()
        self._edges.start()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._edges.stop()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._event, "stop", None)

    def _event(self, kind: str, data: dict | None, at_uptime: float | None = None) -> None:
        now, up = time.time(), host.uptime()
        at = up if at_uptime is None else at_uptime
        payload = json.dumps(data, separators=(",", ":")) if data else None
        store.add_health_event(int(now - up + at), self._boot, int(at), host.clock_synced(), kind, payload)

    def _on_start(self) -> None:
        self._synced = host.clock_synced()
        last = store.last_health_sample()
        last_event = store.last_health_event(last[0]) if last else None
        event = start_event(last, last_event, self._boot, host.uptime())
        if event is not None:
            self._event(*event)

    async def _run(self) -> None:
        try:
            await asyncio.to_thread(self._on_start)
        except Exception:
            logger.debug("health recorder start bookkeeping failed", exc_info=True)
        while True:
            await asyncio.sleep(60 - time.time() % 60 + 0.05)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("health recorder tick failed", exc_info=True)

    def _host(self) -> dict:
        reading = host.read(iface=settings.host_lan_interface)
        nic = reading.pop("nic", None)
        if nic is not None:
            prev, self._nic = self._nic, nic
            if prev is not None and nic[0] >= prev[0] and nic[1] >= prev[1]:
                reading["eth0_rx"], reading["eth0_tx"] = nic[0] - prev[0], nic[1] - prev[1]
        return reading

    async def _tick(self) -> None:
        now, up, synced = time.time(), host.uptime(), host.clock_synced()
        if synced and not self._synced:
            n = await asyncio.to_thread(store.restamp_unsynced, self._boot, int(now - up))
            await asyncio.to_thread(self._event, "clock_sync", {"restamped": n})
        self._synced = synced

        ts = int(now // 60 * 60) - 60
        data: dict = {"v": 1, "feed": self.feed.drain()}
        err: list[str] = []
        edges, blind = self._edges.drain()
        if edges:
            data["edge"] = edges
        err += [f"edge:{n}" for n in blind]
        try:
            p = await paths.read()
            if p is not None:
                data["paths"] = p
        except Exception:
            err.append("paths")
        try:
            data["host"] = await asyncio.to_thread(self._host)
        except Exception:
            err.append("host")
        try:
            for change in await asyncio.to_thread(self._restarts.poll):
                await asyncio.to_thread(self._event, "container", change)
        except Exception:
            err.append("containers")
        if err:
            data["err"] = err
        payload = json.dumps(data, separators=(",", ":"))
        await asyncio.to_thread(store.add_health_sample, self._boot, int(up - (now - ts)), ts, synced, payload)

        self._ticks += 1
        if self._ticks % _PRUNE_TICKS == 1:
            await asyncio.to_thread(
                store.prune_health_raw,
                int(now) - SAMPLE_RETENTION_DAYS * 86400,
                int(now) - EVENT_RETENTION_DAYS * 86400,
            )


recorder = Recorder()
