from app.services.health.collectors.paths import summarize
from app.services.system_proxy.clash import _to_connection, _to_path_status

PROXIES = {
    "auto": {"type": "Fallback", "now": "xray", "all": ["xray", "xray-fallback", "frpc"]},
    "xray": {"alive": True, "history": [{"time": "2026-10-15T00:45:18Z", "delay": 252}]},
    "xray-fallback": {"alive": True, "history": [{"time": "2026-10-15T00:45:18Z", "delay": 126}]},
    "frpc": {"alive": False, "history": [{"time": "2026-10-15T00:45:22Z", "delay": 0}]},
}


def test_path_status_from_clash_own_checks():
    assert summarize(_to_path_status(PROXIES)) == {
        "active": "xray",
        "alive": {"xray": True, "xray-fallback": True, "frpc": False},
        "delay": {"xray": 252, "xray-fallback": 126, "frpc": None},
    }


def test_missing_group_yields_empty_status():
    s = _to_path_status({})
    assert s.active is None
    assert s.paths == {}


def test_clash_inner_connections_are_internal():
    tun = _to_connection({"id": "1", "metadata": {"type": "Tun", "network": "TCP"}, "chains": ["xray"]})
    inner = _to_connection({"id": "2", "metadata": {"type": "Inner", "network": "tcp"}, "chains": ["xray"]})
    assert not tun.internal
    assert tun.network == "tcp"
    assert inner.internal
