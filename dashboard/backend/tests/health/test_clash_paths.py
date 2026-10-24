import asyncio

import httpx

from app.services.health.collectors.paths import summarize
from app.services.system_proxy import clash
from app.services.system_proxy.base import SupportsPathProbe
from app.services.system_proxy.clash import ClashController, _to_connection, _to_path_status

PROXIES = {
    "auto": {"type": "Fallback", "now": "xray", "all": ["xray", "xray-fallback", "frpc"]},
    "xray": {"alive": True, "history": [{"time": "2026-10-15T00:45:18Z", "delay": 252}]},
    "xray-fallback": {"alive": True, "history": [{"time": "2026-10-15T00:45:18Z", "delay": 126}]},
    "frpc": {"alive": False, "history": [{"time": "2026-10-15T00:45:22Z", "delay": 0}]},
    "DIRECT": {"type": "Direct"},
    "REJECT": {"type": "Reject"},
    "lan-exit": {"type": "Direct"},
    "exit-picker": {"type": "Selector", "now": "lan-exit", "all": ["lan-exit"]},
}


def test_path_status_from_clash_own_checks():
    assert summarize(_to_path_status(PROXIES)) == {
        "active": "xray",
        "alive": {"xray": True, "xray-fallback": True, "frpc": False},
        "delay": {"xray": 252, "xray-fallback": 126, "frpc": None},
        "local": ["DIRECT", "REJECT", "lan-exit"],
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


REAL_CLIENT = httpx.AsyncClient


def probe(monkeypatch, handler):
    monkeypatch.setattr(
        clash.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw)
    )
    return asyncio.run(ClashController("clash").probe_path("xray-fallback"))


def test_probe_path_runs_that_routes_own_delay_check(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"delay": 142})

    assert probe(monkeypatch, handler) == 142
    path, params = seen[0]
    assert path == "/proxies/xray-fallback/delay" and params["timeout"] == "5000"


def test_probe_path_reports_failure_as_none(monkeypatch):
    assert probe(monkeypatch, lambda r: httpx.Response(503, json={"message": "timeout"})) is None
    assert probe(monkeypatch, lambda r: httpx.Response(200, json={"delay": 0})) is None


def test_clash_offers_the_path_probe_capability():
    assert isinstance(ClashController("clash"), SupportsPathProbe)
