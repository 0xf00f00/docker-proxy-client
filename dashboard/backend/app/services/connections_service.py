"""Real-time live-connections collection for the dashboard."""

import asyncio
import contextlib
import logging
import time

from app.services import usage_service

logger = logging.getLogger(__name__)

# Per-connection rate smoothing — matches the traffic collector so the two
# readouts move together. Lower = smoother but laggier.
EWMA_ALPHA = 0.4
QUEUE_MAXSIZE = 8
# Defensive bound on payload size. Home use is tens of connections; this only
# bites in pathological cases, and we surface the drop count rather than lie.
SITE_CAP = 200


def _conn_host(meta: dict) -> str:
    """The website a connection is talking to, falling back to its dest IP."""
    host = (meta.get("host") or "").strip()
    if host:
        return host
    ip = (meta.get("destinationIP") or "").strip()
    return ip or "unknown"


def _conn_exit(chains: list) -> str:
    """The proxy a connection actually egresses through.

    Clash orders ``chains`` outermost-group-last, so ``chains[0]`` is the real
    outbound. Empty chains (rare) read as a direct connection.
    """
    return chains[0] if chains else "DIRECT"


class ConnectionsCollector:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        # Cumulative (up, down, monotonic_ts) per connection id, for delta computation.
        self._prev: dict[str, tuple[int, int, float]] = {}
        # Smoothed (up_bps, down_bps) per connection id.
        self._rate: dict[str, tuple[float, float]] = {}
        self._latest: dict | None = None

    async def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        async with self._lock:
            self._subscribers.discard(q)
            if not self._subscribers:
                # No one's watching: drop the live-rate baselines and last snapshot
                # so the next viewer starts clean (usage accounting keeps its own).
                self._prev.clear()
                self._rate.clear()
                self._latest = None

    def start(self) -> None:
        """Start the always-on pump (lifespan-managed; only when tracking is on)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._prev.clear()
        self._rate.clear()
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

    def _update_rates(self, conns: list[dict], now: float) -> dict[str, tuple[float, float]]:
        """Diff cumulative byte counters into smoothed per-connection rates."""
        seen: set[str] = set()
        for conn in conns:
            cid = conn.get("id")
            if not cid:
                continue
            seen.add(cid)
            up = int(conn.get("upload", 0) or 0)
            down = int(conn.get("download", 0) or 0)
            prev = self._prev.get(cid)
            self._prev[cid] = (up, down, now)
            if prev is None:
                continue
            pup, pdown, pts = prev
            dt = now - pts
            if dt <= 0:
                continue
            # Counters only grow for a live connection; a drop means we mismatched
            # ids — skip rather than emit a negative spike.
            up_inst = max(0.0, (up - pup) / dt)
            down_inst = max(0.0, (down - pdown) / dt)
            sm = self._rate.get(cid)
            if sm is None:
                self._rate[cid] = (up_inst, down_inst)
            else:
                self._rate[cid] = (
                    EWMA_ALPHA * up_inst + (1 - EWMA_ALPHA) * sm[0],
                    EWMA_ALPHA * down_inst + (1 - EWMA_ALPHA) * sm[1],
                )
        # Forget connections that closed so they don't linger.
        for stale in set(self._prev) - seen:
            self._prev.pop(stale, None)
            self._rate.pop(stale, None)
        return self._rate

    def _build_snapshot(self, raw: dict, now: float) -> dict:
        conns = raw.get("connections") or []
        rates = self._update_rates(conns, now)

        groups: dict[str, dict] = {}
        for conn in conns:
            cid = conn.get("id")
            meta = conn.get("metadata") or {}
            host = _conn_host(meta)
            exit_proxy = _conn_exit(conn.get("chains") or [])
            up = int(conn.get("upload", 0) or 0)
            down = int(conn.get("download", 0) or 0)
            up_bps, down_bps = rates.get(cid, (0.0, 0.0)) if cid else (0.0, 0.0)
            start = conn.get("start") or ""

            detail = {
                "id": cid,
                "down": down,
                "up": up,
                "downRate": round(down_bps),
                "upRate": round(up_bps),
                "network": (meta.get("network") or "").lower(),
                "dest": meta.get("destinationIP") or "",
                "port": meta.get("destinationPort") or "",
                "exit": exit_proxy,
                "rule": " ".join(x for x in (conn.get("rule"), conn.get("rulePayload")) if x).strip(),
                "since": start,
            }

            site = groups.get(host)
            if site is None:
                site = {
                    "host": host,
                    "count": 0,
                    "down": 0,
                    "up": 0,
                    "downRate": 0.0,
                    "upRate": 0.0,
                    "exits": {},
                    "since": start,
                    "connections": [],
                }
                groups[host] = site
            site["count"] += 1
            site["down"] += down
            site["up"] += up
            site["downRate"] += down_bps
            site["upRate"] += up_bps
            site["exits"][exit_proxy] = site["exits"].get(exit_proxy, 0) + 1
            if start and (not site["since"] or start < site["since"]):
                site["since"] = start
            site["connections"].append(detail)

        sites = []
        for site in groups.values():
            # Most-used exit represents the group; rows rarely span exits.
            exits = site.pop("exits")
            site["exit"] = max(exits, key=exits.get) if exits else "DIRECT"
            site["downRate"] = round(site["downRate"])
            site["upRate"] = round(site["upRate"])
            site["connections"].sort(key=lambda c: c["downRate"] + c["upRate"], reverse=True)
            sites.append(site)

        # Busiest first; cumulative bytes breaks ties so idle sites stay stable.
        sites.sort(key=lambda s: (s["downRate"] + s["upRate"], s["down"] + s["up"]), reverse=True)
        dropped = max(0, len(sites) - SITE_CAP)
        if dropped:
            logger.debug("connections: %d sites over cap, truncating", dropped)
            sites = sites[:SITE_CAP]

        return {
            "ts": int(time.time()),
            "count": len(conns),
            "totals": {
                "down": int(raw.get("downloadTotal", 0) or 0),
                "up": int(raw.get("uploadTotal", 0) or 0),
                "downRate": round(sum(s["downRate"] for s in sites)),
                "upRate": round(sum(s["upRate"] for s in sites)),
            },
            "sites": sites,
            "truncated": dropped,
        }

    def _consume(self, raw: dict) -> None:
        # Usage accrues whether or not the modal is open; the grouped snapshot is
        # only built when someone's watching.
        usage_service.recorder.ingest(raw)
        if self._subscribers:
            self._fan_out(self._build_snapshot(raw, time.monotonic()))

    async def _run(self) -> None:
        # Imported here to avoid a module-load cycle (registry -> docker_service).
        from app.services.system_proxy import registry
        from app.services.system_proxy.base import SupportsConnectionsStream

        while True:
            try:
                controller = await asyncio.to_thread(registry.get_active_controller)
                if controller is None or not isinstance(controller, SupportsConnectionsStream):
                    # No live-connections source — emit an empty snapshot so the UI
                    # shows "nothing connected" rather than a perpetual skeleton.
                    self._fan_out(self._build_snapshot({}, time.monotonic()))
                    await asyncio.sleep(5.0)
                    continue
                async for raw in controller.stream_connections():
                    self._consume(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("connections collector loop error; retrying", exc_info=True)
                self._prev.clear()
                self._rate.clear()
                await asyncio.sleep(3.0)


collector = ConnectionsCollector()
