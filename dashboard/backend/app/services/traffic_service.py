"""Real-time network-traffic collection for the dashboard.

A single shared collector samples throughput and fans it out to every connected
SSE client. It is *lazily* started when the first subscriber attaches and fully
stopped when the last one leaves, so an idle dashboard costs nothing.

We never inspect payloads — byte counts only.
"""

import asyncio
import contextlib
import logging
import time

from docker.errors import APIError, NotFound

from app.services.docker_service import _is_host_network, get_client, parse_dashboard_labels

logger = logging.getLogger(__name__)

# Cadence. One sample per second matches Clash's /traffic emit rate and is plenty
# for a human-facing "how busy are we" readout.
TICK_SEC = 1.0
# The container set changes rarely (start/stop), so re-listing every tick would be
# wasteful — refresh the topology on a slower beat.
TOPOLOGY_REFRESH_SEC = 10.0
# Cap concurrent stats reads so we never flood the socket-proxy in one tick.
MAX_CONCURRENT_STATS = 8
# Exponential smoothing factor for the displayed rates. Lower = smoother but laggier.
EWMA_ALPHA = 0.4
# Counters this far below the previous reading mean the container restarted and its
# counters reset; treat as a fresh baseline rather than a huge negative spike.
QUEUE_MAXSIZE = 8


def _fetch_net_counters(container_id: str) -> tuple[int, int] | None:
    """Return (rx_bytes, tx_bytes) summed across the container's interfaces.

    Uses a one-shot stats read (no daemon-side sampling delay) since we compute
    our own deltas. Returns None if the container vanished or stats are absent.
    """
    client = get_client()
    try:
        try:
            stats = client.api.stats(container_id, stream=False, one_shot=True)
        except TypeError:
            # Older docker-py without the one_shot kwarg.
            stats = client.api.stats(container_id, stream=False)
    except (NotFound, APIError):
        return None
    except Exception:
        return None
    networks = (stats or {}).get("networks") or {}
    if not networks:
        return None
    rx = sum(int(v.get("rx_bytes", 0)) for v in networks.values())
    tx = sum(int(v.get("tx_bytes", 0)) for v in networks.values())
    return rx, tx


def _list_measurable() -> dict[str, str]:
    """Map ``container.name`` -> id for running, dashboard-enabled, non-host-net containers.

    Keyed by Docker name to match the frontend's ``container.name``. Host-network
    containers are excluded: their stats reflect the whole host, not the proxy.
    """
    out: dict[str, str] = {}
    for c in get_client().containers.list():
        if parse_dashboard_labels(c.labels) is None:
            continue
        if _is_host_network(c):
            continue
        out[c.name] = c.id
    return out


class TrafficCollector:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._system_task: asyncio.Task | None = None
        # Cumulative counter + timestamp per container, for delta computation.
        self._prev: dict[str, tuple[int, int, float]] = {}
        # Smoothed per-proxy throughput (bytes/sec).
        self._bps: dict[str, float] = {}
        self._system = {"up": 0.0, "down": 0.0}
        self._latest: dict | None = None

    async def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers.add(q)
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run())
            if self._system_task is None or self._system_task.done():
                self._system_task = asyncio.create_task(self._run_system_traffic())
        return q

    async def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        async with self._lock:
            self._subscribers.discard(q)
            if not self._subscribers:
                for task in (self._task, self._system_task):
                    if task is not None:
                        task.cancel()
                self._task = None
                self._system_task = None
                # Drop stale baselines so a future viewer starts clean.
                self._prev.clear()
                self._bps.clear()
                self._system = {"up": 0.0, "down": 0.0}
                self._latest = None

    def latest(self) -> dict | None:
        return self._latest

    def _fan_out(self, snapshot: dict) -> None:
        self._latest = snapshot
        for q in self._subscribers:
            if q.full():
                # Slow consumer — drop the oldest so it always gets the freshest.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(snapshot)

    def _update_proxy_rates(self, counters: dict[str, tuple[int, int] | None], now: float) -> None:
        seen = set(counters)
        for name, c in counters.items():
            if c is None:
                continue
            rx, tx = c
            prev = self._prev.get(name)
            self._prev[name] = (rx, tx, now)
            if prev is None:
                continue
            prx, ptx, pts = prev
            dt = now - pts
            if dt <= 0:
                continue
            # Counter reset (restart) → re-baseline, skip this sample.
            if rx < prx or tx < ptx:
                continue
            inst = max((rx - prx) / dt, (tx - ptx) / dt)
            smoothed = self._bps.get(name)
            self._bps[name] = inst if smoothed is None else EWMA_ALPHA * inst + (1 - EWMA_ALPHA) * smoothed
        # Forget containers that disappeared so they don't linger in the snapshot.
        for stale in set(self._prev) - seen:
            self._prev.pop(stale, None)
            self._bps.pop(stale, None)

    async def _run(self) -> None:
        topology: dict[str, str] = {}
        last_topology = 0.0
        sem = asyncio.Semaphore(MAX_CONCURRENT_STATS)

        async def read(name: str, cid: str) -> tuple[str, tuple[int, int] | None]:
            async with sem:
                return name, await asyncio.to_thread(_fetch_net_counters, cid)

        try:
            while True:
                tick_start = time.monotonic()
                if tick_start - last_topology >= TOPOLOGY_REFRESH_SEC or not topology:
                    try:
                        topology = await asyncio.to_thread(_list_measurable)
                    except Exception:
                        logger.debug("traffic: topology refresh failed", exc_info=True)
                    last_topology = tick_start

                try:
                    results = await asyncio.gather(*(read(n, i) for n, i in topology.items()))
                    counters = dict(results)
                except Exception:
                    logger.debug("traffic: stats sweep failed", exc_info=True)
                    counters = {}

                self._update_proxy_rates(counters, time.monotonic())

                snapshot = {
                    "ts": int(time.time()),
                    "system": {"up": round(self._system["up"]), "down": round(self._system["down"])},
                    "proxies": {n: round(v) for n, v in self._bps.items()},
                }
                self._fan_out(snapshot)

                elapsed = time.monotonic() - tick_start
                await asyncio.sleep(max(0.0, TICK_SEC - elapsed))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("traffic collector loop crashed")

    async def _run_system_traffic(self) -> None:
        """Mirror the whole-system up/down rate from the active proxy controller."""
        # Imported here to avoid a module-load cycle (registry -> docker_service).
        from app.services.system_proxy import registry
        from app.services.system_proxy.base import SupportsTrafficStream

        while True:
            try:
                controller = await asyncio.to_thread(registry.get_active_controller)
                if controller is None or not isinstance(controller, SupportsTrafficStream):
                    self._system = {"up": 0.0, "down": 0.0}
                    await asyncio.sleep(5.0)
                    continue
                async for up, down in controller.stream_traffic():
                    self._system["up"] = EWMA_ALPHA * up + (1 - EWMA_ALPHA) * self._system["up"]
                    self._system["down"] = EWMA_ALPHA * down + (1 - EWMA_ALPHA) * self._system["down"]
            except asyncio.CancelledError:
                raise
            except Exception:
                self._system = {"up": 0.0, "down": 0.0}
                await asyncio.sleep(3.0)


collector = TrafficCollector()
