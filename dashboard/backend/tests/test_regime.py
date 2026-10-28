import asyncio

import pytest

from app.services import direct_net
from app.services.connectivity_tests import regime


@pytest.fixture
def world(monkeypatch):
    """Fake the network: a gateway answer sequence, which anchors answer, and what got probed."""
    monkeypatch.setattr(regime, "_cache", None)
    monkeypatch.setattr(regime, "CONFIRM_DELAY_S", 0)
    probed: dict[str, list[str]] = {"intl": [], "ir": []}

    def classify(gateway=(True,), intl_up=(), intl_ok_after=0, ir_up=False):
        answers = iter(gateway)

        async def gateway_reachable():
            return next(answers, gateway[-1])

        def probe_host(host):
            probed["intl"].append(host)
            ok = host in intl_up and len(probed["intl"]) > intl_ok_after
            return ok, ok

        def probe_ir():
            probed["ir"].append("ir")
            return ir_up

        monkeypatch.setattr(direct_net, "gateway_reachable", gateway_reachable)
        monkeypatch.setattr(regime, "_probe_host_sync", probe_host)
        monkeypatch.setattr(regime, "_probe_ir_sync", probe_ir)
        return asyncio.run(regime._classify())

    classify.probed = probed
    return classify


ALL = tuple(regime.INTL_HOSTS)


def test_an_unreachable_router_is_a_local_network_failure_and_nothing_else_is_tested(world):
    assert world(gateway=(False, False), intl_up=ALL).regime == "local_network"
    assert world.probed == {"intl": [], "ir": []}


def test_a_router_that_answers_on_the_recheck_is_not_a_failure(world):
    assert world(gateway=(False, True), intl_up=ALL).regime == "normal"


def test_a_gateway_that_cannot_be_determined_does_not_block_the_diagnosis(world):
    assert world(gateway=(None,), intl_up=ALL).regime == "normal"


def test_one_reachable_anchor_is_enough_for_a_normal_internet(world):
    assert world(intl_up=(ALL[1],)).regime == "normal"
    assert world.probed["ir"] == []


def test_a_lost_first_round_is_rechecked_before_the_internet_is_called_blocked(world):
    assert world(intl_up=ALL, intl_ok_after=len(ALL)).regime == "normal"


def test_international_down_with_iran_up_is_a_block_and_with_iran_down_an_outage(world):
    assert world(ir_up=True).regime == "iran_only"
    assert world(ir_up=False).regime == "total_outage"
