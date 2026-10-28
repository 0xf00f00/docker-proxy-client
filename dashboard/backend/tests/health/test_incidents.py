from app.services.health.cause import cause_for, describe_detail
from app.services.health.timeline import find_incidents


def incident(*parts):
    """One incident from back-to-back (minutes, status, regime) segments followed by recovery."""
    rows, t = [], 0
    for minutes, status, regime in parts:
        rows.append((t, t + round(minutes * 60), status, regime, None))
        t += round(minutes * 60)
    rows.append((t, t + 300, "good", "normal", None))
    (found,) = find_incidents(rows, t + 300, None)
    return found


def test_a_long_internet_outage_is_not_renamed_by_the_brief_block_before_it():
    # 2026-10-27: 11 min of proxy flaps and 0.5 min of "blocked", then 7 h with nothing reachable.
    inc = incident(
        (10.6, "outage", "normal"),
        (0.6, "degraded", "normal"),
        (0.5, "outage", "iran_only"),
        (430.9, "outage", "total_outage"),
    )
    assert (inc["regime"], inc["cause"], inc["causeLabel"]) == ("total_outage", "internet", "Internet is down")


def test_a_mostly_proxy_incident_keeps_its_cause_despite_a_short_block():
    # 2026-10-16: 106 min with the direct internet fine, 9 min of "blocked".
    inc = incident((9.4, "outage", "iran_only"), (106.3, "outage", "normal"), (2.4, "degraded", "normal"))
    assert (inc["regime"], inc["causeLabel"]) == ("normal", "Proxy unreachable")


def test_a_genuine_block_is_still_reported():
    inc = incident((2.5, "outage", "iran_only"), (0.5, "outage", "normal"))
    assert (inc["regime"], inc["causeLabel"]) == ("iran_only", "International internet blocked")


def test_a_slow_only_incident_is_slow():
    assert incident((4, "degraded", "normal"))["causeLabel"] == "Connection is slow"


def test_home_network_internet_and_block_read_differently():
    labels = {r: cause_for("outage", r) for r in ("iran_only", "total_outage", "local_network", "normal")}
    assert len({kind for kind, _ in labels.values()}) == 4 and len({label for _, label in labels.values()}) == 4
    details = {r: describe_detail("outage", r, None) for r in ("iran_only", "total_outage", "local_network")}
    assert all(details.values()) and len(set(details.values())) == 3
    assert describe_detail("outage", "normal", None) is None
