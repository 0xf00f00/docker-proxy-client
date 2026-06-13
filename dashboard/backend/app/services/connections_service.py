"""Real-time live-connections collection for the dashboard."""

import asyncio
import contextlib
import logging
import time

from app.services.connection_stream import drive_connections
from app.services.system_proxy.base import EMPTY_SNAPSHOT, Connection, ConnectionSnapshot

logger = logging.getLogger(__name__)

# Per-connection rate smoothing — matches the traffic collector so the two
# readouts move together. Lower = smoother but laggier.
EWMA_ALPHA = 0.4
QUEUE_MAXSIZE = 8
# Defensive bound on payload size. Home use is tens of connections; this only
# bites in pathological cases, and we surface the drop count rather than lie.
SITE_CAP = 200


def _conn_host(conn: Connection) -> str:
    """The website a connection is talking to, falling back to its dest IP."""
    return conn.host or conn.dest_ip or "unknown"


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

    def _update_rates(self, conns: tuple[Connection, ...], now: float) -> dict[str, tuple[float, float]]:
        """Diff cumulative byte counters into smoothed per-connection rates."""
        seen: set[str] = set()
        for conn in conns:
            cid = conn.id
            if not cid:
                continue
            seen.add(cid)
            up = conn.upload
            down = conn.download
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

    def _build_snapshot(self, snap: ConnectionSnapshot, now: float) -> dict:
        conns = snap.connections
        rates = self._update_rates(conns, now)

        groups: dict[str, dict] = {}
        for conn in conns:
            cid = conn.id
            host = _conn_host(conn)
            exit_proxy = conn.exit
            up = conn.upload
            down = conn.download
            up_bps, down_bps = rates.get(cid, (0.0, 0.0)) if cid else (0.0, 0.0)
            start = conn.start

            detail = {
                "id": cid,
                "down": down,
                "up": up,
                "downRate": round(down_bps),
                "upRate": round(up_bps),
                "network": conn.network,
                "dest": conn.dest_ip,
                "port": conn.dest_port,
                "exit": exit_proxy,
                "rule": conn.rule,
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
                "down": snap.download_total,
                "up": snap.upload_total,
                "downRate": round(sum(s["downRate"] for s in sites)),
                "upRate": round(sum(s["upRate"] for s in sites)),
            },
            "sites": sites,
            "truncated": dropped,
        }

    def observe(self, snap: ConnectionSnapshot) -> None:
        """Fold one upstream frame into the warm rate state and notify viewers."""
        self._fan_out(self._build_snapshot(snap, time.monotonic()))

    def reset_rates(self) -> None:
        """Drop rate baselines after a feed gap so deltas don't span the outage."""
        self._prev.clear()
        self._rate.clear()

    def start(self) -> None:
        """Start the always-on unified feed (lifespan-managed).

        One ``/connections`` WebSocket drives both the live snapshot and usage
        accounting. Keeping rate state warm even while the modal's closed means
        the first frame a viewer gets already carries live rates — the list moves
        the instant it opens instead of sitting frozen for a sample or two.
        """
        if self._task is not None and not self._task.done():
            return
        from app.services import usage_service

        def consume(snap: ConnectionSnapshot) -> None:
            usage_service.recorder.ingest(snap)
            self.observe(snap)

        def on_error() -> None:
            self.reset_rates()
            usage_service.recorder.reset_baseline()

        self._task = asyncio.create_task(
            drive_connections(
                consume,
                # No source yet: show "nothing connected" instead of a stuck skeleton.
                on_no_source=lambda: self.observe(EMPTY_SNAPSHOT),
                on_error=on_error,
            )
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._prev.clear()
        self._rate.clear()
        self._latest = None


collector = ConnectionsCollector()
