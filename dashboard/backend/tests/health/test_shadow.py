from app.services.health import shadow
from app.services.health.model import BUSY, DOWN, GOOD, NOT_MEASURED, SLOW, Minute
from tests.health.series import T0


def minutes(*states):
    return [Minute(T0 + 60 * i, s, "ok" if s == GOOD else "x", ()) for i, s in enumerate(states)]


def old(start_min, end_s, status):
    return (T0 + 60 * start_min, T0 + end_s, status, "normal", None)


def test_a_half_minute_blip_in_the_old_monitor_is_a_disagreement():
    out = shadow.compare(minutes(GOOD, GOOD, GOOD), [old(0, 600, "good"), old(1, 60 * 1 + 30, "outage")])
    assert out["minutes"] == 3 and out["agree"] == 2
    assert out["pairs"] == {"outage->good": 1}
    (d,) = out["disagreements"]
    assert (d["start"], d["end"], d["old"], d["new"]) == (T0 + 60, T0 + 120, "outage", GOOD)


def test_consecutive_minutes_with_the_same_pair_are_one_span_and_the_new_side_explains():
    out = shadow.compare(minutes(GOOD, BUSY, BUSY, BUSY, GOOD), [old(0, 600, "good")])
    (d,) = out["disagreements"]
    assert d["minutes"] == 3 and d["old"] == "good" and d["new"] == BUSY and d["cause"] == "x"
    assert out["pairs"] == {f"good->{BUSY}": 3}


def test_unknown_on_either_side_is_not_a_disagreement():
    out = shadow.compare(minutes(NOT_MEASURED, GOOD), [old(0, 59, "good"), old(1, 119, "unknown")])
    assert out["minutes"] == 0 and out["disagreements"] == []
    assert shadow.compare(minutes(DOWN), [old(0, 600, "unknown")])["disagreements"] == []


def test_the_new_degraded_family_matches_the_old_degraded():
    out = shadow.compare(minutes(SLOW, SLOW), [old(0, 200, "degraded")])
    assert out["disagreements"] == [] and out["agree"] == 2


def test_the_worst_old_status_in_a_minute_wins():
    assert (
        shadow.old_by_minute([old(0, 600, "good"), old(2, 150, "degraded"), old(2, 140, "outage")], T0, T0 + 600)[
            T0 + 120
        ]
        == "outage"
    )


def test_nothing_to_compare_is_empty():
    assert shadow.compare([], []) == {"minutes": 0, "agree": 0, "pairs": {}, "disagreements": []}
