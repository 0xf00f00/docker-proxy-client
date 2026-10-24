import asyncio
import json
import time

import pytest
from fastapi import HTTPException

from app.routers.health import health_raw, health_shadow, health_verdict
from app.services import store
from app.services.health import engine
from app.services.health.model import GOOD, NOT_MEASURED
from tests.health.series import edge


def write_minutes(now, n, *, edges=None, boot="b"):
    last = int(now // 60 * 60) - 60
    for i in range(n):
        ts = last - 60 * (n - 1 - i)
        data = {
            "v": 1,
            "feed": {"frames": 47, "up": 1000, "down": 50_000, "pk_up": 100, "pk_down": 100_000, "user": {"new": 3}},
            "edge": {"edge-a": edges or edge(), "edge-b": edge(2)},
            "paths": {"active": "route-a", "alive": {"route-a": True, "route-b": True}, "delay": {"route-a": 130}},
        }
        store.add_health_sample(boot, 600 + 60 * i, ts, True, json.dumps(data))


def test_current_reads_recorded_minutes_and_says_since_when(state_dir):
    now = time.time()
    write_minutes(now, 40)
    out = engine.current(now)
    assert out["state"] == GOOD and out["cause"] == "ok" and out["confidence"] == "real traffic"
    assert out["since"] == int(now // 60 * 60) - 60 * 40
    assert [a["key"] for a in out["activities"]] == ["browse", "download", "calls"]


def test_a_stopped_recorder_reads_not_measured_since_its_last_minute(state_dir):
    now = time.time()
    write_minutes(now - 900, 40)
    out = engine.current(now)
    assert out["state"] == NOT_MEASURED and out["cause"] == "monitor"
    assert out["since"] == int((now - 900) // 60 * 60)


def test_no_recorded_minutes_is_not_measured(state_dir):
    out = engine.current(time.time())
    assert out["state"] == NOT_MEASURED and out["since"] is None


def test_the_verdict_is_cached_briefly(state_dir, monkeypatch):
    now = time.time()
    write_minutes(now, 40)
    first = engine.current(now)

    def boom(*a, **k):
        raise AssertionError("re-read")

    monkeypatch.setattr(store, "health_samples", boom)
    assert engine.current(now) is first


def test_active_probes_feed_the_baseline_and_the_verdict(state_dir):
    now = time.time()
    write_minutes(now, 60)
    for d in range(1, 8):
        store.add_health_probe(int(now - d * 86400), "goodput", "route-a", True, 700_000, '{"mbit":8.0}')
    store.add_health_probe(int(now - 600), "goodput", "route-a", True, 700_000, '{"mbit":0.05}')
    assert engine.current(now)["state"] == "slow"


def test_probe_bytes_come_from_the_ledger(state_dir):
    now = time.time()
    store.add_health_probe(int(now), "goodput", None, True, 700_000, None)
    store.add_health_probe(int(now), "stun", None, True, 3_000, None)
    store.add_health_probe(int(now - 3 * 86400), "stun", None, True, 1_000, None)
    out = engine.probe_bytes(now)
    assert out["today"] == 703_000 and out["cap"] == 2_000_000
    assert out["month"] >= 703_000


def test_capacity_stays_at_the_default_until_a_day_of_data_exists(state_dir):
    assert engine.capacity(time.time()).down_bps == 2_000_000


def test_the_shadow_view_lists_where_the_old_monitor_disagrees(state_dir):
    now = time.time()
    write_minutes(now, 120)
    blip = int(now // 60 * 60) - 60 * 30
    store.add_segment(blip - 3600, blip, "good", "normal", None)
    store.add_segment(blip, blip + 20, "outage", "normal", None)
    store.add_segment(blip + 20, blip + 3600, "good", "normal", None)
    out = engine.compare("24h", 86400, now)
    assert out["window"] == "24h" and out["segments"][-1]["state"] == GOOD
    assert out["pairs"] == {"outage->good": 1}
    assert out["disagreements"][0]["start"] == blip


def test_verdict_endpoint_adds_the_monitors_own_state(state_dir):
    write_minutes(time.time(), 40)
    out = asyncio.run(health_verdict())
    assert out["state"] == GOOD
    assert out["probeBytes"] == {"today": 0, "month": 0, "cap": 2_000_000}
    assert out["held"] == ["kill_switch", "clock_unsynced"]


def test_raw_export_reports_the_monitors_own_state(state_dir):
    out = asyncio.run(health_raw(since=1, until=100))
    assert out["probeBytes"]["cap"] == 2_000_000 and "kill_switch" in out["held"]


def test_shadow_endpoint_rejects_unknown_windows(state_dir):
    with pytest.raises(HTTPException) as e:
        asyncio.run(health_shadow(window="1y"))
    assert e.value.status_code == 400
    assert asyncio.run(health_shadow(window="24h"))["window"] == "24h"
