import random

from app.services.health.model import DOWN, GOOD, NOT_MEASURED, SLOW
from tests.health.series import (
    MATURE,
    T0,
    changes,
    edge,
    goodput,
    healthy,
    ladder,
    run,
    sample,
    states,
)

DEAD = {"route-a": False, "route-b": False, "naive": False}


def seq(*blocks):
    out = []
    for build, n in blocks:
        out += [build(len(out) + i) for i in range(n)]
    return out


def test_a_healthy_link_with_the_usual_trickle_of_failures_never_leaves_good():
    rng = random.Random(7)
    samples = []
    for i in range(3 * 1440):
        hello = rng.choice((2, 3, 3, 4))
        fails = sum(rng.random() < 0.01 for _ in range(hello))
        samples.append(sample(i, edges={"edge-a": edge(hello, fails)}))
    assert set(states(run(samples))) == {GOOD}


def test_a_sustained_elevated_ratio_on_the_active_edge_is_slow_then_clears():
    bad = lambda i: sample(i, edges={"edge-a": edge(3, 1)})  # noqa: E731
    r = run(seq((lambda i: sample(i), 40), (bad, 60), (lambda i: sample(i), 30)))
    flips = changes(r)
    assert flips[:2] == [0, GOOD] and flips[2:4][1] == SLOW and flips[-1] == GOOD
    onset = flips[2]
    assert 40 < onset < 70
    assert states(r)[onset:99] == [SLOW] * (99 - onset)
    assert r.minutes[onset].cause == "route_degraded"
    assert states(r)[109:] == [GOOD] * (len(r.minutes) - 109)


def test_total_handshake_failure_is_caught_within_minutes():
    dead = lambda i: sample(i, edges={"edge-a": edge(3, 3, ack=3)})  # noqa: E731
    r = run(seq((lambda i: sample(i), 40), (dead, 10)))
    assert r.minutes[43].state == SLOW


def test_a_failing_backup_edge_is_evidence_not_a_state():
    bad = lambda i: sample(i, edges={"edge-b": edge(2, 1)})  # noqa: E731
    r = run(seq((bad, 120)))
    assert set(states(r)) == {GOOD}
    evidence = r.tracker.verdict(T0 + 120 * 60)["evidence"]
    assert any(e["signal"] == "edge" and "backup" in e["text"] and e["level"] == "warn" for e in evidence)


def test_every_path_failing_with_no_traffic_is_down_after_two_minutes_and_clears_after_five():
    dead = lambda i: sample(i, alive=DEAD, down=0, chains={})  # noqa: E731
    r = run(seq((lambda i: sample(i), 10), (dead, 20), (lambda i: sample(i), 10)))
    assert changes(r) == [0, GOOD, 11, DOWN, 34, GOOD]
    assert r.minutes[11].cause == "proxy"


def test_traffic_flowing_through_a_proxy_rules_out_down():
    flowing = lambda i: sample(i, alive=DEAD, down=500_000)  # noqa: E731
    assert set(states(run(seq((flowing, 20))))) == {GOOD}


def test_a_dead_standby_alone_is_not_an_outage():
    r = run(seq((lambda i: sample(i, alive={"route-a": True, "route-b": False}), 20)))
    assert set(states(r)) == {GOOD}


def test_decoy_blocked_when_every_instance_times_out_and_the_outside_world_is_reachable():
    both = {"edge-a": edge(4, 4, ack=4), "edge-b": edge(3, 3, ack=3)}
    dead = lambda i: sample(i, alive=DEAD, down=0, chains={}, edges=both)  # noqa: E731
    r = run(seq((lambda i: sample(i), 10), (dead, 15)), probes=[ladder(14, "normal")])
    assert r.minutes[20].state == DOWN and r.minutes[20].cause == "decoy_blocked"


def test_country_wide_block_and_local_outage_come_from_the_outside_check():
    dead = lambda i: sample(i, alive=DEAD, down=0, chains={})  # noqa: E731
    assert run(seq((dead, 10)), probes=[ladder(1, "iran_only")]).minutes[-1].cause == "regime"
    assert run(seq((dead, 10)), probes=[ladder(1, "total_outage")]).minutes[-1].cause == "local"


def test_a_goodput_far_below_the_usual_is_slow_the_august_shape():
    r = run(healthy(60), [goodput(10, ok=True, mbit=0.05)], base=MATURE)
    assert states(r)[10:] == [SLOW] * 50
    assert run(healthy(60), [goodput(10, ok=True, mbit=3.0)], base=MATURE).minutes[-1].state == GOOD


def test_a_hole_on_the_same_boot_is_the_monitor_not_the_network():
    r = run([*healthy(10), *healthy(5, start=60)])
    assert set(states(r)[10:60]) == {NOT_MEASURED} and r.minutes[30].cause == "monitor"


def test_a_series_that_stopped_growing_is_not_measured_never_green():
    r = run(healthy(30))
    assert r.tracker.verdict(T0 + 30 * 60)["state"] == GOOD
    stale = r.tracker.verdict(T0 + 30 * 60 + 600)
    assert stale["state"] == NOT_MEASURED and stale["cause"] == "monitor"


def test_blind_collectors_are_not_measured():
    r = run([*healthy(5), sample(5, frames=0, paths=False)])
    assert r.minutes[-1].state == NOT_MEASURED and r.minutes[-1].cause == "feed"
