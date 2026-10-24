"""Decides when an active probe may run, keeps the byte budget, and stores each result."""

import asyncio
import contextlib
import json
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field

from app.services import store
from app.services.connectivity_tests import regime as regime_mod
from app.services.health import engine, rules
from app.services.health.collectors.feed import FeedAggregator
from app.services.health.engine import day_start
from app.services.health.probes import ProbeResult, burst, goodput, stun
from app.services.health.settings import (
    ACTIVE_PROBES,
    BURST_MAX_PER_DAY,
    GOODPUT_MAX_PER_DAY,
    PATH_EDGES,
    PROBE_DAILY_BYTES,
)
from app.services.system_proxy import registry
from app.services.system_proxy.base import SupportsPathProbe

logger = logging.getLogger(__name__)

STUN, BURST, GOODPUT, LADDER = "stun", "burst", "goodput", "ladder"

QUIET_UTIL = 0.3
STUN_IDLE_S = 1800.0
STUN_CALL_S = 120.0
STUN_BACKOFF_MAX_S = 3600.0
BURST_GAP_S = 1800.0
GOODPUT_EVERY_S = 86400.0
GOODPUT_GAP_S = 12 * 3600.0  # floor that holds even if the ledger can't be written
JITTER = 0.2
GOODPUT_JITTER_S = 7200.0
LADDER_WIRE = 5000
PROBE_TIMEOUT_S = 40.0
EXCLUDE_GRACE_S = 6.0


@dataclass(slots=True)
class Context:
    now: float
    util: float  # busiest direction's share of capacity; 1.0 when the feed is down
    call: bool
    suspect: bool
    ledger_ok: bool = True  # false while results can't be written, so the byte cap can't be enforced


@dataclass(slots=True)
class State:
    usage: dict[str, tuple[int, int]] = field(default_factory=dict)  # kind -> (runs, bytes) today
    last_run: dict[str, float] = field(default_factory=dict)
    last_ok: dict[str, float] = field(default_factory=dict)
    interval: dict[str, float] = field(default_factory=dict)
    fails: dict[str, int] = field(default_factory=dict)
    escalate: bool = False

    def runs(self, kind: str) -> int:
        return self.usage.get(kind, (0, 0))[0]

    def spent(self) -> int:
        return sum(b for _, b in self.usage.values())


def jittered(base: float, rng: random.Random) -> float:
    return base * rng.uniform(1 - JITTER, 1 + JITTER)


def plan(state: State, ctx: Context) -> str | None:
    """The one probe to start this tick, if any."""
    quiet = ctx.util <= QUIET_UTIL and not ctx.call
    spent = state.spent()
    if (
        ctx.suspect
        and quiet
        and ctx.ledger_ok
        and spent + burst.ESTIMATE <= PROBE_DAILY_BYTES
        and state.runs(BURST) < BURST_MAX_PER_DAY
        and ctx.now - state.last_run.get(BURST, 0) >= BURST_GAP_S
    ):
        return BURST
    if (
        quiet
        and ctx.ledger_ok
        and spent + goodput.ESTIMATE <= PROBE_DAILY_BYTES
        and state.runs(GOODPUT) < GOODPUT_MAX_PER_DAY
        and ctx.now - state.last_run.get(GOODPUT, 0) >= GOODPUT_GAP_S
        and (state.escalate or ctx.now - state.last_ok.get(GOODPUT, 0) >= state.interval.get(GOODPUT, GOODPUT_EVERY_S))
    ):
        return GOODPUT
    wait = state.interval.get(STUN, STUN_IDLE_S)
    if ctx.call:
        wait = min(wait, STUN_CALL_S)
    if ctx.now - state.last_run.get(STUN, 0) >= wait:
        return STUN
    return None


def after(state: State, kind: str, ok: bool, now: float, rng: random.Random) -> None:
    """Fold a finished probe into the schedule."""
    state.last_run[kind] = now
    if ok:
        state.last_ok[kind] = now
    state.fails[kind] = 0 if ok else state.fails.get(kind, 0) + 1
    if kind == STUN:
        retry = min(STUN_BACKOFF_MAX_S, STUN_CALL_S * 2 ** (state.fails[kind] - 1))
        state.interval[STUN] = jittered(STUN_IDLE_S, rng) if ok else retry
    elif kind == GOODPUT:
        state.escalate = False
        state.interval[GOODPUT] = GOODPUT_EVERY_S + rng.uniform(-GOODPUT_JITTER_S, GOODPUT_JITTER_S)
    elif kind == BURST and not ok:
        state.escalate = True


class ProbeScheduler:
    def __init__(
        self, feed: FeedAggregator, rng: random.Random | None = None, edges: dict[str, str] | None = None
    ) -> None:
        self._feed = feed
        self._rng = rng or random.Random()
        self._routes = dict(PATH_EDGES if edges is None else edges)
        self._state = State()
        self._edges: dict[str, deque[tuple[int, int]]] = {}
        self._task: asyncio.Task | None = None
        self._loaded = False
        self.ledger_failing = False

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def held(self, synced: bool) -> list[str]:
        """Why active probes aren't running right now. While the ledger can't be written only the
        byte-capped ones (burst, goodput) wait; the tiny call probe keeps running and clears it."""
        return [
            name
            for name, held in (
                ("kill_switch", not ACTIVE_PROBES),
                ("clock_unsynced", not synced),
                ("ledger_failing", self.ledger_failing),
            )
            if held
        ]

    def _suspect(self, data: dict) -> bool:
        paths = data.get("paths") or {}
        alive = paths.get("alive", {})
        active = paths.get("active") or ""
        # a dead standby alone isn't suspect: the burst would only test the healthy active path
        if alive.get(active) is False or (self._routes and all(alive.get(p) is False for p in self._routes)):
            return True
        edge = self._routes.get(active)
        return edge is not None and rules.edge_state(list(self._edges.get(edge, ()))) in ("poor", "fast")

    def _track_edges(self, data: dict) -> None:
        for name, counts in (data.get("edge") or {}).items():
            fails = sum(v for k, v in counts.items() if k != "hello")
            self._edges.setdefault(name, deque(maxlen=rules.EDGE_WINDOW_MIN)).append((counts.get("hello", 0), fails))

    def _load_state(self, now: float) -> None:
        for kind, (last, last_ok) in store.health_probe_last().items():
            self._state.last_run[kind] = last
            if last_ok is not None:
                self._state.last_ok[kind] = last_ok
        if STUN not in self._state.last_run:
            self._state.last_run[STUN] = now - STUN_IDLE_S + self._rng.uniform(60, 180)
        self._loaded = True

    async def on_tick(self, data: dict, now: float, synced: bool) -> None:
        self._track_edges(data)
        if not ACTIVE_PROBES or not synced or (self._task is not None and not self._task.done()):
            return
        if not self._loaded:
            await asyncio.to_thread(self._load_state, now)
        capacity = await asyncio.to_thread(engine.capacity, now)
        self._state.usage = await asyncio.to_thread(store.health_probe_usage, day_start(now))
        feed = data.get("feed") or {}
        ctx = Context(
            now=now,
            util=rules.utilization(feed, capacity) if feed.get("frames") else 1.0,
            call=(feed.get("calls") or {}).get("n", 0) > 0,
            suspect=self._suspect(data),
            ledger_ok=not self.ledger_failing,
        )
        kind = plan(self._state, ctx)
        if kind is not None:
            self._task = asyncio.create_task(self._run(kind, (data.get("paths") or {}).get("active")))

    def _expect(self, kind: str) -> None:
        if kind == STUN:
            self._feed.expect_probe(
                "udp", str(stun.PORT), frozenset(), stun.COUNT * stun.INTERVAL_S + stun.SETTLE_S + EXCLUDE_GRACE_S
            )
        elif kind == BURST:
            self._feed.expect_probe("tcp", "443", frozenset({burst.HOST}), burst.TIMEOUT_S + EXCLUDE_GRACE_S)
        elif kind == GOODPUT:
            self._feed.expect_probe("tcp", "443", frozenset({goodput.HOST}), goodput.TIMEOUT_S + EXCLUDE_GRACE_S)

    async def _execute(self, kind: str) -> ProbeResult:
        self._expect(kind)
        runner = {STUN: stun.run, BURST: burst.run, GOODPUT: goodput.run}[kind]
        return await asyncio.wait_for(runner(), PROBE_TIMEOUT_S)

    async def _record(self, kind: str, path: str | None, result: ProbeResult) -> None:
        payload = json.dumps(result.data, separators=(",", ":")) if result.data else None
        try:
            await asyncio.to_thread(
                store.add_health_probe, int(time.time()), kind, path, result.ok, result.bytes, payload
            )
        except Exception:
            self.ledger_failing = True
            raise
        self.ledger_failing = False

    async def _run(self, kind: str, path: str | None) -> None:
        try:
            try:
                result = await self._execute(kind)
            except Exception as e:
                result = ProbeResult(False, 0, {"error": type(e).__name__})
            after(self._state, kind, result.ok, time.time(), self._rng)
            await self._record(kind, path, result)
            if kind == BURST and not result.ok:
                await self._ladder(path)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("health probe %s failed to record", kind, exc_info=True)

    async def _standby(self, active: str | None) -> dict[str, int | None] | None:
        """Would switching help: the backup routes' own delay check, never the active path."""
        try:
            controller = await asyncio.to_thread(registry.get_active_controller)
            names = [p for p in self._routes if p != active]
            if active is None or not isinstance(controller, SupportsPathProbe) or not names:
                return None
            delays = await asyncio.wait_for(
                asyncio.gather(*(controller.probe_path(n) for n in names), return_exceptions=True), PROBE_TIMEOUT_S
            )
        except Exception:
            return None
        return {n: d if isinstance(d, int) else None for n, d in zip(names, delays, strict=True)}

    async def _ladder(self, path: str | None) -> None:
        try:
            info = await asyncio.wait_for(regime_mod.get_regime(), PROBE_TIMEOUT_S)
            data: dict = {"regime": info.regime, "intl_up": info.intl_up}
            switch = await self._standby(path)
            if switch is not None:
                data["switch"] = switch
            result = ProbeResult(True, LADDER_WIRE, data)
        except Exception as e:
            result = ProbeResult(False, 0, {"error": type(e).__name__})
        await self._record(LADDER, path, result)
