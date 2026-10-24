import pytest

from app.services.health import attribution as a
from app.services.health.attribution import Facts
from app.services.health.model import BUSY, CHOPPY, DOWN, GOOD, NOT_MEASURED, POWER_CUT, SLOW

TIMEOUTS = {"x": (20, 20, 20), "y": (15, 15, 14)}


@pytest.mark.parametrize(
    ("facts", "cause"),
    [
        (Facts(GOOD), "ok"),
        (Facts(BUSY), "busy"),
        (Facts(POWER_CUT), "power_cut"),
        (Facts(NOT_MEASURED), "monitor"),
        (Facts(CHOPPY), "calls"),
        (Facts(CHOPPY, call_busy=True), "calls_busy"),
        (Facts(DOWN, regime="iran_only", edges=TIMEOUTS), "regime"),
        (Facts(DOWN, regime="total_outage"), "local"),
        (Facts(DOWN, regime="normal", edges=TIMEOUTS), "decoy_blocked"),
        (Facts(DOWN, edges=TIMEOUTS), "decoy_blocked"),
        (Facts(DOWN, regime="normal", edges=TIMEOUTS, restarted=True), "proxy_restarted"),
        (Facts(DOWN, regime="normal"), "proxy"),
        (Facts(DOWN, unreach=50), "local"),
        (Facts(SLOW, browse_bad=True, standby_alive=True), "route_degraded"),
        (Facts(SLOW, browse_bad=True, standby_alive=True, switch_helps=False), "slow"),
        (Facts(SLOW, browse_bad=True, standby_alive=False), "slow"),
        (Facts(SLOW, browse_bad=True, standby_alive=False, switch_helps=True), "route_degraded"),
        (Facts(SLOW, browse_bad=True, hot=True, standby_alive=True), "overheating"),
        (Facts(SLOW), "slow_path"),
    ],
)
def test_cause_table(facts, cause):
    assert a.attribute(facts) == (cause, a.LABELS[cause])


def test_decoy_needs_every_measured_instance_failing_by_timeout():
    assert a.decoy_blocked(TIMEOUTS)
    assert not a.decoy_blocked({"x": (20, 20, 20)})
    assert not a.decoy_blocked({"x": (20, 20, 20), "y": (15, 3, 3)})
    assert not a.decoy_blocked({"x": (20, 20, 2), "y": (15, 15, 1)})
    assert not a.decoy_blocked({"x": (20, 20, 20), "y": (4, 4, 4)})


def test_every_cause_has_a_plain_sentence():
    assert all(a.LABELS.values())
