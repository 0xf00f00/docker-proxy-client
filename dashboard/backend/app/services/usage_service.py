"""Persistent per-domain data-usage accounting.

Fed raw Clash ``/connections`` snapshots by the connections pump (so there's one
shared stream, not a second subscriber). Diffs each connection's cumulative byte
counters into per-registrable-domain hourly buckets and flushes them to a small,
separately-deletable SQLite file. Gated entirely by ``settings.connection_tracking``
— nothing here runs unless the operator opted in at deploy time.
"""

import asyncio
import contextlib
import logging
import time

import tldextract

from app.services import store

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_SEC = 30.0
RETENTION_DAYS = 30
_PRUNE_EVERY = int(3600 / FLUSH_INTERVAL_SEC)

# Offline PSL snapshot — no network fetch.
_extract = tldextract.TLDExtract(suffix_list_urls=())


def _floor_hour(ts: float) -> int:
    return int(ts) // 3600 * 3600


# Anything without a public registrable domain (no SNI, raw-IP dests) is bucketed
# here rather than persisting an IP — keeps the byte total, drops the identifier.
OTHER = "Other"


def _domain_of(conn: dict) -> str:
    """Registrable domain a connection talks to (``*.googlevideo.com`` ->
    ``googlevideo.com``); non-domain traffic collapses to ``Other``."""
    meta = conn.get("metadata") or {}
    host = (meta.get("host") or "").strip()
    if host:
        return _extract(host).registered_domain or OTHER
    return OTHER


class UsageRecorder:
    def __init__(self) -> None:
        # Cumulative (up, down) per connection id, for delta computation.
        self._prev: dict[str, tuple[int, int]] = {}
        # Pending (domain, hour_ts) -> [down, up] awaiting flush.
        self._buf: dict[tuple[str, int], list[int]] = {}
        self._task: asyncio.Task | None = None
        self._flushes = 0

    def ingest(self, raw: dict) -> None:
        conns = raw.get("connections") or []
        hour = _floor_hour(time.time())
        seen: set[str] = set()
        for conn in conns:
            cid = conn.get("id")
            if not cid:
                continue
            seen.add(cid)
            up = int(conn.get("upload", 0) or 0)
            down = int(conn.get("download", 0) or 0)
            prev = self._prev.get(cid)
            self._prev[cid] = (up, down)
            if prev is None:
                # First sighting: baseline only — we can't attribute bytes moved
                # before we started watching.
                continue
            pup, pdown = prev
            d_down, d_up = max(0, down - pdown), max(0, up - pup)
            if d_down or d_up:
                bucket = self._buf.setdefault((_domain_of(conn), hour), [0, 0])
                bucket[0] += d_down
                bucket[1] += d_up
        for stale in set(self._prev) - seen:
            self._prev.pop(stale, None)

    def _drain(self) -> list[tuple[str, int, int, int]]:
        if not self._buf:
            return []
        rows = [(d, h, b[0], b[1]) for (d, h), b in self._buf.items()]
        self._buf.clear()
        return rows

    async def flush_now(self) -> None:
        rows = self._drain()
        if rows:
            await asyncio.to_thread(store.upsert_usage, rows)

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(FLUSH_INTERVAL_SEC)
                await self.flush_now()
                self._flushes += 1
                if self._flushes % _PRUNE_EVERY == 0:
                    cutoff = _floor_hour(time.time() - RETENTION_DAYS * 86400)
                    await asyncio.to_thread(store.prune_usage, cutoff)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("usage flush loop error; retrying", exc_info=True)

    def start(self) -> None:
        store.init_usage()
        self._prev.clear()
        self._buf.clear()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def flush_and_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.flush_now()

    def purge_all(self) -> None:
        """Drop everything — in-memory buffer and the on-disk file. Re-creates an
        empty schema so a running recorder keeps writing after a clear."""
        self._prev.clear()
        self._buf.clear()
        store.wipe_usage()
        store.init_usage()


recorder = UsageRecorder()
