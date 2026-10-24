"""Loads recorded rows, runs the pure verdict over them, and caches the result briefly."""

import json
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.services import store
from app.services.health import baseline, rules, shadow, verdict
from app.services.health.model import Event, Probe, Sample
from app.services.health.settings import CAPACITY_DOWN_BPS, CAPACITY_UP_BPS, PROBE_DAILY_BYTES

CURRENT_SPAN_S = 6 * 3600
CURRENT_TTL_S = 30.0
SHADOW_TTL_S = 120.0
CAPACITY_SPAN_S = 7 * 86400
CAPACITY_TTL_S = 3600.0

_memo: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, make: Callable[[], Any]) -> Any:
    now = time.monotonic()
    hit = _memo.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    value = make()
    _memo[key] = (now, value)
    return value


def capacity(now: float) -> rules.Capacity:
    def learn() -> rules.Capacity:
        feeds = [json.loads(d).get("feed", {}) for *_, d in store.health_samples(int(now - CAPACITY_SPAN_S), int(now))]
        default = rules.Capacity(CAPACITY_DOWN_BPS, CAPACITY_UP_BPS)
        return rules.learn_capacity([f.get("pk_down", 0) for f in feeds], [f.get("pk_up", 0) for f in feeds], default)

    return _cached("capacity", CAPACITY_TTL_S, learn)


def load(since: int, until: int) -> tuple[list[Sample], list[Probe], list[Event]]:
    samples = [Sample(ts, boot, json.loads(d)) for ts, boot, _up, d in store.health_samples(since, until)]
    probes = [
        Probe(ts, kind, path, bool(ok), n, json.loads(d) if d else {})
        for ts, kind, path, ok, n, d in store.health_probes(until - baseline.BASELINE_SPAN_S, until)
    ]
    events = [
        Event(ts, boot, kind, json.loads(d) if d else {})
        for ts, boot, kind, _sy, d in store.health_events(since, until)
    ]
    return samples, probes, events


def _run(since: int, until: int, now: float) -> verdict.Run:
    samples, probes, events = load(since, until)
    active = next((s.data["paths"].get("active") for s in reversed(samples) if s.data.get("paths")), None)
    return verdict.evaluate(samples, probes, events, capacity(now), baseline.build(probes, active, now))


def _since(minutes: list) -> int:
    start = minutes[-1].ts
    for m in reversed(minutes):
        if m.state != minutes[-1].state:
            break
        start = m.ts
    return start


def current(now: float) -> dict:
    def make() -> dict:
        run = _run(int(now) - CURRENT_SPAN_S, int(now) + 1, now)
        out = run.tracker.verdict(now)
        last = run.minutes[-1] if run.minutes else None
        if last and last.state == out["state"]:
            out["since"] = _since(run.minutes)
        else:
            out["since"] = out["ts"] + 60 if out["ts"] else None
        return out

    return _cached("current", CURRENT_TTL_S, make)


def compare(window: str, span_s: int, now: float) -> dict:
    def make() -> dict:
        since, until = int(now) - span_s, int(now) + 1
        run = _run(since, until, now)
        old = store.health_segments(since, until)
        return {
            "window": window,
            "since": since,
            "until": until,
            "segments": verdict.segments(run.minutes),
            "old": [{"start": s, "end": e, "status": st, "regime": r} for s, e, st, r, _d in old],
            **shadow.compare(run.minutes, old),
        }

    return _cached(f"shadow:{window}", SHADOW_TTL_S, make)


def day_start(now: float) -> int:
    return int(datetime.fromtimestamp(now).astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def month_start(now: float) -> int:
    day = datetime.fromtimestamp(now).astimezone().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(day.timestamp())


def probe_bytes(now: float) -> dict:
    """What the monitor's own probes have cost, from the ledger."""
    spent = lambda since: sum(b for _, b in store.health_probe_usage(since).values())  # noqa: E731
    return {"today": spent(day_start(now)), "month": spent(month_start(now)), "cap": PROBE_DAILY_BYTES}
