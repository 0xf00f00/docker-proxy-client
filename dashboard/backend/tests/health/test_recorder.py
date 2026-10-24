import asyncio
import json

from app.services import store
from app.services.health.collectors import host, paths
from app.services.health.recorder import Recorder, start_event

BOOT_A, BOOT_B = "boot-a", "boot-b"


def test_first_start_has_nothing_to_report():
    assert start_event(None, None, BOOT_A, 30) is None


def test_quick_dashboard_restart_is_not_a_gap():
    assert start_event((BOOT_A, 1000, 1_700_000_000, 1), None, BOOT_A, 1100) is None


def test_monitor_down_on_same_boot_is_a_monitor_gap():
    kind, data, at = start_event((BOOT_A, 1000, 1_700_000_000, 1), (1050, "stop"), BOOT_A, 4000)
    assert kind == "monitor_gap"
    assert data == {"gap_s": 2940, "graceful": True}
    assert at == 4000


def test_new_boot_without_stop_marker_is_a_power_cut():
    last = (BOOT_A, 300_000, 1_791_666_000, 1)
    kind, data, at = start_event(last, (120_000, "stop"), BOOT_B, 95)
    assert kind == "boot"
    assert data == {"prev_boot": BOOT_A, "last_ts": 1_791_666_060, "last_synced": True, "graceful": False}
    assert at == 0


def test_new_boot_after_stop_marker_is_a_restart():
    last = (BOOT_A, 300_000, 1_791_666_000, 1)
    kind, data, _ = start_event(last, (300_030, "stop"), BOOT_B, 95)
    assert kind == "boot"
    assert data["graceful"] is True


def test_unsynced_rows_are_restamped_and_hidden_until_then(state_dir):
    fake_boot_wall = 1_000_000
    store.add_health_sample(BOOT_A, 60, fake_boot_wall + 60, False, "{}")
    store.add_health_sample(BOOT_A, 120, fake_boot_wall + 120, False, "{}")
    store.add_health_event(fake_boot_wall, BOOT_A, 0, False, "boot", None)
    assert store.health_samples(0, 2**31) == []

    real_boot_wall = 1_791_716_430
    assert store.restamp_unsynced(BOOT_A, real_boot_wall) == 2
    rows = store.health_samples(0, 2**31)
    assert [ts for ts, *_ in rows] == [1_791_716_460, 1_791_716_520]
    events = store.health_events(0, 2**31)
    assert events == [(real_boot_wall, BOOT_A, "boot", 1, None)]


def test_last_sample_is_by_write_order_not_wall_time(state_dir):
    store.add_health_sample(BOOT_A, 600, 1_791_000_000, True, "{}")
    store.add_health_sample(BOOT_B, 60, 1_000_060, False, "{}")
    assert store.last_health_sample() == (BOOT_B, 60, 1_000_060, 0)


def test_ticks_write_compact_samples_and_restamp_on_sync(state_dir, monkeypatch):
    clock = {"synced": False, "up": 600.0}
    monkeypatch.setattr(host, "clock_synced", lambda: clock["synced"])
    monkeypatch.setattr(host, "uptime", lambda root=None: clock["up"])
    monkeypatch.setattr(host, "read", lambda root=None, iface="eth0": {"temp": 61.0})

    async def no_paths():
        return None

    monkeypatch.setattr(paths, "read", no_paths)
    r = Recorder()
    r._boot = BOOT_A
    monkeypatch.setattr(r._restarts, "poll", lambda: [])

    asyncio.run(r._tick())
    clock.update(synced=True, up=660.0)
    asyncio.run(r._tick())

    rows = store.health_samples(0, 2**31)
    assert len(rows) == 2
    data = json.loads(rows[-1][3])
    assert data["v"] == 1
    assert data["feed"] == {"frames": 0}
    assert data["host"] == {"temp": 61.0}
    assert data["err"] == ["edge:edge-a", "edge:edge-b"]
    kinds = [k for _ts, _b, k, _s, _d in store.health_events(0, 2**31)]
    assert kinds == ["clock_sync"]
