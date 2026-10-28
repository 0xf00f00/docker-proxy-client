import pytest

from app.config import settings
from app.services import direct_net

ROUTES = (
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
    "utun\t00000000\t00000000\t0001\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t00000000\t0158A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
    "eth0\t0058A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
)


@pytest.fixture
def uplink(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "host_lan_interface", "eth0")
    monkeypatch.setattr(direct_net, "ROUTE_TABLE", str(tmp_path / "route"))
    monkeypatch.setattr(direct_net, "SYS_NET", str(tmp_path / "net"))
    return tmp_path


def test_the_uplinks_gateway_is_read_past_the_tun_default_route(monkeypatch):
    monkeypatch.setattr(settings, "host_lan_interface", "eth0")
    assert direct_net.default_gateway(ROUTES) == "192.168.88.1"
    monkeypatch.setattr(settings, "host_lan_interface", "wlan0")
    assert direct_net.default_gateway(ROUTES) is None


def test_a_router_that_answers_a_port_or_only_arp_is_reachable_and_silence_is_not(uplink, monkeypatch):
    (uplink / "route").write_text(ROUTES)
    arp = {"seen": False}
    monkeypatch.setattr(direct_net, "_in_arp_table", lambda gateway: arp["seen"])
    monkeypatch.setattr(direct_net, "_answers", lambda gateway, port: port == 80)
    assert direct_net._gateway_reachable_sync() is True
    monkeypatch.setattr(direct_net, "_answers", lambda gateway, port: False)
    assert direct_net._gateway_reachable_sync() is False
    arp["seen"] = True
    assert direct_net._gateway_reachable_sync() is True


def test_no_way_out_is_a_failure_only_when_the_nic_exists(uplink):
    (uplink / "route").write_text(ROUTES.replace("eth0", "wlan0"))
    assert direct_net._gateway_reachable_sync() is None
    (uplink / "net" / "eth0").mkdir(parents=True)
    assert direct_net._gateway_reachable_sync() is False


def test_an_unreadable_routing_table_means_cannot_tell(uplink):
    assert direct_net._gateway_reachable_sync() is None
