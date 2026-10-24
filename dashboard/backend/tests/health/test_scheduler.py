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


def test_nothing_is_due_right_after_a_run():
    assert plan(settled(), ctx()) is None


def test_stun_is_due_after_its_interval_and_sooner_during_a_call():
    st = settled(last_run={STUN: NOW - 600})
    assert plan(st, ctx()) is None
    assert plan(st, ctx(call=True)) == STUN
    assert plan(settled(last_run={STUN: NOW - 1900}), ctx()) == STUN


def test_stun_backs_off_while_failing_then_recovers():
    st, rng = State(), random.Random(0)
    waits = []
    for _ in range(7):
        after(st, STUN, False, NOW, rng)
        waits.append(st.interval[STUN])
    assert waits == [120, 240, 480, 960, 1920, 3600, 3600]
    after(st, STUN, True, NOW, rng)
    assert 1440 <= st.interval[STUN] <= 2160


def test_stun_ignores_the_byte_cap_and_busy_link():
    st = settled(last_run={STUN: NOW - 1900}, usage={"goodput": (1, 10**9)})
    assert plan(st, ctx(util=0.9)) == STUN


def test_burst_needs_suspicion_and_a_quiet_link():
    assert plan(settled(), ctx(suspect=True)) == BURST
    assert plan(settled(), ctx(suspect=False)) is None
    assert plan(settled(), ctx(suspect=True, util=0.5)) is None
    assert plan(settled(), ctx(suspect=True, call=True)) is None


def test_burst_is_rate_limited():
    assert plan(settled(last_run={STUN: NOW - 10, BURST: NOW - 600}), ctx(suspect=True)) is None
    assert plan(settled(last_run={STUN: NOW - 10, BURST: NOW - 2000}), ctx(suspect=True)) == BURST
    assert plan(settled(usage={BURST: (sch.BURST_MAX_PER_DAY, 0)}), ctx(suspect=True)) is None


def test_goodput_runs_when_a_day_has_passed_and_not_before():
    assert plan(settled(last_ok={GOODPUT: NOW - 90_000}, usage={}), ctx()) == GOODPUT
    assert plan(settled(last_ok={GOODPUT: NOW - 80_000}), ctx()) is None


def test_goodput_is_capped_per_day_and_by_bytes():
    due = {"last_ok": {GOODPUT: NOW - 90_000}}
    assert plan(settled(**due, usage={GOODPUT: (sch.GOODPUT_MAX_PER_DAY, 0)}), ctx()) is None
    assert plan(settled(**due, usage={STUN: (1, sch.PROBE_DAILY_BYTES)}), ctx()) is None


def test_a_failed_goodput_is_not_retried_within_the_gap_even_without_a_ledger():
    st, rng = settled(last_ok={GOODPUT: NOW - 90_000}), random.Random(0)
    after(st, GOODPUT, False, NOW, rng)
    assert plan(st, ctx(now=NOW + 3600)) != GOODPUT
    assert plan(st, ctx(now=NOW + sch.GOODPUT_GAP_S + 1)) == GOODPUT


def test_goodput_waits_for_a_quiet_link_and_no_call():
    due = settled(last_ok={GOODPUT: NOW - 90_000})
    assert plan(due, ctx(util=0.4)) is None
    assert plan(due, ctx(call=True)) is None


def test_failed_burst_escalates_to_goodput_once():
    st, rng = settled(), random.Random(0)
    after(st, BURST, False, NOW, rng)
    assert st.escalate and plan(st, ctx()) == GOODPUT
    after(st, GOODPUT, True, NOW, rng)
    assert not st.escalate and plan(st, ctx()) is None


def test_goodput_reschedules_about_a_day_out():
    st = State()
    after(st, GOODPUT, True, NOW, random.Random(1))
    assert 79_000 <= st.interval[GOODPUT] <= 94_000


def sample(*, frames=47, down=0, up=0, calls=0, active="xray", alive=None, edge=None):
    return {
        "feed": {"frames": frames, "down": down, "up": up, "calls": {"n": calls}},
        "paths": {"active": active, "alive": alive or {"xray": True, "xray-fallback": True}},
        "edge": edge or {"sni-spoofing": {"hello": 3, "ack_to": 0}},
    }


@pytest.fixture
def fakes(monkeypatch, state_dir):
    calls = []
    monkeypatch.setattr(sch, "ACTIVE_PROBES", True)

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
    store.add_health_probe(int(now - 100), GOODPUT, "xray", True, 690_000, None)


def test_kill_switch_runs_nothing(fakes, monkeypatch):
    monkeypatch.setattr(sch, "ACTIVE_PROBES", False)
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(alive={"xray": False}), time.time(), True))
    assert fakes.calls == [] and rows() == []


def test_a_failing_ledger_write_does_not_repeat_the_probe(fakes, monkeypatch):
    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "add_health_probe", locked)
    fakes.install(goodput_r=ProbeResult(False, 7800, {"bytes": 0}))
    now = time.time()
    sched = ProbeScheduler(FeedAggregator(), random.Random(0))
    drive(sched, *[(sample(), now + 60 * i, True) for i in range(5)])
    assert fakes.calls.count(GOODPUT) == 1


def test_unsynced_clock_runs_no_probes(fakes):
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(), time.time(), False))
    assert rows() == []


def test_first_stun_comes_a_minute_or_three_after_start(fakes):
    now = time.time()
    seed_goodput_ok(now)
    sched = ProbeScheduler(FeedAggregator(), random.Random(0))
    drive(sched, (sample(), now, True))
    assert rows() == [r for r in rows() if r[1] == GOODPUT]
    drive(sched, (sample(), now + 200, True))
    stun_rows = rows(STUN)
    assert len(stun_rows) == 1 and stun_rows[0][2] == "xray" and stun_rows[0][3] == 1
    assert json.loads(stun_rows[0][5])["recv"] == 20


def test_results_feed_the_ledger(fakes):
    now = time.time()
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(), now, True))
    usage = store.health_probe_usage(sch.day_start(now))
    assert usage[GOODPUT] == (1, 690_000)


def test_byte_cap_stops_everything_but_stun(fakes, monkeypatch):
    monkeypatch.setattr(sch, "PROBE_DAILY_BYTES", 1000)
    now = time.time()
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(alive={"xray": False}), now, True))
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(), now + 400, True))
    assert {r[1] for r in rows()} <= {STUN}


def test_dead_path_triggers_a_burst_and_a_failed_one_runs_the_ladder(fakes, monkeypatch):
    fakes.install(burst_r=ProbeResult(False, 62_000, {"n": 8, "failed": 5}))

    async def regime(*a, **k):
        return SimpleNamespace(regime="normal", intl_up=True)

    monkeypatch.setattr(sch.regime_mod, "get_regime", regime)
    now = time.time()
    seed_goodput_ok(now)
    drive(ProbeScheduler(FeedAggregator(), random.Random(0)), (sample(alive={"xray": False}), now, True))
    assert fakes.calls[:1] == ["burst"]
    ladder = rows("ladder")
    assert len(ladder) == 1 and json.loads(ladder[0][5]) == {"regime": "normal", "intl_up": True}


def test_sustained_edge_failures_on_the_active_path_trigger_a_burst(fakes):
    now = time.time()
    seed_goodput_ok(now)
    sched = ProbeScheduler(FeedAggregator(), random.Random(0))
    bad = {"sni-spoofing": {"hello": 3, "ack_to": 1}}
    drive(sched, *[(sample(edge=bad), now + 60 * i, True) for i in range(30)])
    assert BURST in fakes.calls


def test_a_dead_standby_does_not_trigger_a_burst_but_all_paths_dead_does(fakes):
    now = time.time()
    seed_goodput_ok(now)
    sched = ProbeScheduler(FeedAggregator(), random.Random(0))
    drive(sched, (sample(alive={"xray": True, "xray-fallback": False}), now, True))
    drive(sched, (sample(active="xray-fallback", alive={"xray": False, "xray-fallback": True}), now + 60, True))
    assert BURST not in fakes.calls
    drive(sched, (sample(active="naive", alive={"xray": False, "xray-fallback": False}), now + 120, True))
    assert BURST in fakes.calls


def test_a_degraded_standby_edge_does_not_trigger_a_burst(fakes):
    now = time.time()
    seed_goodput_ok(now)
    sched = ProbeScheduler(FeedAggregator(), random.Random(0))
    bad = {"sni-spoofing": {"hello": 3, "ack_to": 0}, "sni-spoofing-fallback": {"hello": 2, "ack_to": 1}}
    drive(sched, *[(sample(edge=bad), now + 60 * i, True) for i in range(30)])
    assert BURST not in fakes.calls


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


def test_probe_connections_are_not_counted_as_user_traffic(fakes):
    now = time.time()
    seed_goodput_ok(now)
    feed = FeedAggregator()
    sched = ProbeScheduler(feed, random.Random(0))
    sched._expect(STUN)
    assert feed._probe is not None and feed._probe[:2] == ("udp", "19302")
