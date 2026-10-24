import asyncio

from app.routers.health import health_raw
from app.services import store


def test_raw_export_includes_samples_events_and_probes(state_dir):
    store.add_health_sample("b", 60, 1_000_000, True, '{"v":1}')
    store.add_health_event(1_000_010, "b", 0, True, "container", '{"name":"clash"}')
    store.add_health_probe(1_000_020, "stun", "xray", True, 2800, '{"loss":0.0}')
    store.add_health_probe(1_000_030, "burst", None, False, 62_000, None)

    out = asyncio.run(health_raw(since=999_000, until=1_001_000))

    assert out["samples"] == [{"ts": 1_000_000, "boot": "b", "uptime": 60, "v": 1}]
    assert out["events"][0]["data"] == {"name": "clash"}
    assert out["probes"] == [
        {"ts": 1_000_020, "kind": "stun", "path": "xray", "ok": True, "bytes": 2800, "data": {"loss": 0.0}},
        {"ts": 1_000_030, "kind": "burst", "path": None, "ok": False, "bytes": 62_000},
    ]


def test_old_probes_age_out_with_the_samples(state_dir):
    store.add_health_probe(100, "stun", None, True, 1, None)
    store.add_health_probe(900, "stun", None, True, 1, None)
    store.prune_health_raw(500, 0)
    assert [r[0] for r in store.health_probes(0, 2**31)] == [900]
