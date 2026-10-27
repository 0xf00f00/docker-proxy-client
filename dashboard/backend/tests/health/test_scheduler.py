import asyncio
import json
import random
import sqlite3
import time
from types import SimpleNamespace

import pytest

from app.services import store
from app.services.health import scheduler as sch
from app.services.health.collectors.feed import FeedAggregator
from app.services.health.probes import ProbeResult, burst, goodput, stun
from app.services.health.scheduler import BURST, GOODPUT, STUN, Context, ProbeScheduler, State, after, plan

NOW = 1_800_000_000.0


def ctx(**kw):
    return Context(**{"now": NOW, "util": 0.05, "call": False, "suspect": False, **kw})


def settled(**kw):
    """Goodput recently done and STUN recently run: nothing is due unless a test makes it so."""
    base = {"last_ok": {GOODPUT: NOW - 100}, "last_run": {STUN: NOW - 10}}
    return State(**{**base, **kw})


def test_burst_is_rate_limited():
    assert plan(settled(last_run={STUN: NOW - 10, BURST: NOW - 600}), ctx(suspect=True)) is None
    assert plan(settled(last_run={STUN: NOW - 10, BURST: NOW - 2000}), ctx(suspect=True)) == BURST
    assert plan(settled(usage={BURST: (sch.BURST_MAX_PER_DAY, 0)}), ctx(suspect=True)) is None


def test_goodput_is_capped_per_day_and_by_bytes():
    due = {"last_ok": {GOODPUT: NOW - 90_000}}
    assert plan(settled(**due, usage={GOODPUT: (sch.GOODPUT_MAX_PER_DAY, 0)}), ctx()) is None
    assert plan(settled(**due, usage={STUN: (1, sch.PROBE_DAILY_BYTES)}), ctx()) is None


def test_a_failed_goodput_is_not_retried_within_the_gap_even_without_a_ledger():
    st, rng = settled(last_ok={GOODPUT: NOW - 90_000}), random.Random(0)
    after(st, GOODPUT, False, NOW, rng)
    assert plan(st, ctx(now=NOW + 3600)) != GOODPUT
    assert plan(st, ctx(now=NOW + sch.GOODPUT_GAP_S + 1)) == GOODPUT


def sample(*, frames=47, down=0, up=0, calls=0, active="route-a", alive=None, edge=None):
    return {
        "feed": {"frames": frames, "down": down, "up": up, "calls": {"n": calls}},
        "paths": {"active": active, "alive": alive or {"route-a": True, "route-b": True}},
        "edge": edge or {"edge-a": {"hello": 3, "ack_to": 0}},
    }


@pytest.fixture
def fakes(monkeypatch, state_dir):
    calls = []
    monkeypatch.setattr(sch, "ACTIVE_PROBES", True)
    monkeypatch.setattr(sch.registry, "get_active_controller", lambda: None)

    def fake(name, result):
        async def run(*a, **k):
            calls.append(name)
            if isinstance(result, Exception):
                raise result
            return result

        return run

    def install(stun_r=None, burst_r=None, goodput_r=None):
        monkeypatch.setattr(stun, "run", fake("stun", stun_r or ProbeResult(True, 2800, {"recv": 20, "loss": 0.0})))
        monkeypatch.setattr(burst, "run", fake("burst", burst_r or ProbeResult(True, 62_000, {"n": 8, "failed": 0})))
        monkeypatch.setattr(
            goodput, "run", fake("goodput", goodput_r or ProbeResult(True, 690_000, {"bytes": 524_288, "mbit": 8.1}))
        )

    install()
    return SimpleNamespace(calls=calls, install=install)


def drive(sched, *ticks):
    async def go():
        for data, now, synced in ticks:
            await sched.on_tick(data, now, synced)
            if sched._task is not None:
                await sched._task

    asyncio.run(go())


def rows(kind=None):
    return [r for r in store.health_probes(0, 2**31) if kind in (None, r[1])]


def seed_goodput_ok(now):
    store.add_health_probe(int(now - 100), GOODPUT, "route-a", True, 690_000, None)


def test_kill_switch_runs_nothing(fakes, monkeypatch):
    monkeypatch.setattr(sch, "ACTIVE_PROBES", False)
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(alive={"route-a": False}), time.time(), True))
    assert fakes.calls == [] and rows() == []


def test_unsynced_clock_runs_no_probes(fakes):
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(), time.time(), False))
    assert rows() == []


def test_byte_cap_stops_everything_but_stun(fakes, monkeypatch):
    monkeypatch.setattr(sch, "PROBE_DAILY_BYTES", 1000)
    now = time.time()
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(alive={"route-a": False}), now, True))
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(), now + 400, True))
    assert {r[1] for r in rows()} <= {STUN}


def test_a_crashing_probe_is_recorded_and_does_not_stop_the_scheduler(fakes):
    fakes.install(goodput_r=RuntimeError("boom"))
    now = time.time()
    sched = ProbeScheduler(FeedAggregator(), random.Random(0))
    drive(sched, (sample(), now, True))
    (row,) = rows(GOODPUT)
    assert row[3] == 0 and json.loads(row[5]) == {"error": "RuntimeError"}


def test_busy_link_or_dead_feed_holds_back_heavy_probes(fakes):
    now = time.time()
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(frames=0), now, True))
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(down=60_000_000), now + 5, True))
    assert GOODPUT not in fakes.calls


class FakeController:
    def __init__(self, delays):
        self.delays, self.asked = delays, []

    async def probe_path(self, name):
        self.asked.append(name)
        if isinstance(self.delays[name], Exception):
            raise self.delays[name]
        return self.delays[name]


def run_ladder(monkeypatch, controller):
    async def regime(*a, **k):
        return SimpleNamespace(regime="normal", intl_up=True)

    monkeypatch.setattr(sch.regime_mod, "get_regime", regime)
    monkeypatch.setattr(sch.registry, "get_active_controller", lambda: controller)
    asyncio.run(ProbeScheduler(FeedAggregator(), random.Random(0))._ladder("route-a"))
    return json.loads(rows("ladder")[0][5])


def test_the_ladder_checks_only_the_backup_routes_never_the_active_one(fakes, monkeypatch):
    controller = FakeController({"route-a": 1, "route-b": 140})
    assert run_ladder(monkeypatch, controller)["switch"] == {"route-b": 140}
    assert controller.asked == ["route-b"]


def test_a_ledger_that_cannot_be_written_holds_the_byte_capped_probes_but_not_the_call_probe():
    st = settled(last_ok={}, last_run={STUN: NOW - 1900})
    assert plan(st, ctx(suspect=True, ledger_ok=False)) == STUN
    assert plan(settled(last_ok={}), ctx(ledger_ok=False)) is None
    assert plan(settled(last_ok={}), ctx(ledger_ok=True)) == GOODPUT
    assert plan(settled(), ctx(suspect=True, ledger_ok=False)) is None
    assert plan(settled(), ctx(suspect=True)) == BURST


def test_a_failing_ledger_keeps_a_suspect_link_from_bursting_all_day(fakes, monkeypatch):
    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "add_health_probe", locked)
    fakes.install(goodput_r=ProbeResult(False, 7800, {"bytes": 0}), burst_r=ProbeResult(False, 62_000, {"failed": 8}))
    now = time.time()
    sched = ProbeScheduler(FeedAggregator(), random.Random(0))
    dead = sample(alive={"route-a": False, "route-b": False})
    drive(sched, *[(dead, now + 60 * 40 * i, True) for i in range(40)])
    assert fakes.calls.count(BURST) + fakes.calls.count(GOODPUT) <= 2
