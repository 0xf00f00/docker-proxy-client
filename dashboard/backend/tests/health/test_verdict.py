import random

from app.services.health import verdict
from app.services.health.baseline import Baseline
from app.services.health.model import BUSY, CHOPPY, DOWN, GOOD, NOT_MEASURED, POWER_CUT, SLOW
from tests.health.series import (
    CAP,
    LEARNING,
    MATURE,
    T0,
    boot_event,
    changes,
    container_event,
    edge,
    goodput,
    healthy,
    ladder,
    run,
    sample,
    states,
    stun,
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


def test_a_single_healthy_failure_after_recovery_does_not_bring_slow_back():
    bad = lambda i: sample(i, edges={"edge-a": edge(3, 1)})  # noqa: E731
    one = lambda i: sample(i, edges={"edge-a": edge(3, 1 if i == 125 else 0)})  # noqa: E731
    r = run(seq((bad, 60), (lambda i: sample(i), 60), (one, 20)))
    assert set(states(r)[125:]) == {GOOD}


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


def test_five_minutes_on_the_backup_route_is_evidence_not_an_outage():
    r = run(seq((lambda i: sample(i), 30), (lambda i: sample(i, active="route-b"), 5), (lambda i: sample(i), 30)))
    assert set(states(r)) == {GOOD}
    assert "backup_route" in r.minutes[31].why


def test_every_path_failing_with_no_traffic_is_down_after_two_minutes_and_clears_after_five():
    dead = lambda i: sample(i, alive=DEAD, down=0, chains={})  # noqa: E731
    r = run(seq((lambda i: sample(i), 10), (dead, 20), (lambda i: sample(i), 10)))
    assert changes(r) == [0, GOOD, 11, DOWN, 34, GOOD]
    assert r.minutes[11].cause == "proxy"


def test_traffic_flowing_through_a_proxy_rules_out_down():
    flowing = lambda i: sample(i, alive=DEAD, down=500_000)  # noqa: E731
    assert set(states(run(seq((flowing, 20))))) == {GOOD}


def test_local_only_traffic_does_not_count_as_proof_of_service():
    direct = lambda i: sample(i, alive=DEAD, down=500_000, chains={"DIRECT": {"down": 500_000}})  # noqa: E731
    assert DOWN in states(run(seq((direct, 20))))


def test_which_exits_are_local_comes_from_the_controller_not_a_name_list():
    exit_ = {"lan-exit": {"down": 500_000}}
    local = lambda i: sample(i, alive=DEAD, down=500_000, chains=exit_, local=["lan-exit"])  # noqa: E731
    assert DOWN in states(run(seq((local, 20))))
    unlisted = lambda i: sample(i, alive=DEAD, down=500_000, chains=exit_, local=[])  # noqa: E731
    assert set(states(run(seq((unlisted, 20))))) == {GOOD}


def test_samples_from_before_the_local_list_fall_back_to_the_controllers_builtins():
    old = lambda i: sample(i, alive=DEAD, down=500_000, chains={"DIRECT": {"down": 500_000}})  # noqa: E731
    assert DOWN in states(run(seq((old, 20))))


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


def test_a_restart_just_before_an_outage_is_the_cause():
    dead = lambda i: sample(i, alive=DEAD, down=0, chains={})  # noqa: E731
    r = run(seq((dead, 6)), events=[container_event(1, "clash")])
    assert r.minutes[-1].cause == "proxy_restarted"


def test_a_full_link_is_busy_only_after_three_of_five_minutes_and_clears_after_five_quiet_ones():
    full = lambda i: sample(i, down=100_000_000)  # noqa: E731
    r = run(seq((lambda i: sample(i), 5), (full, 1), (lambda i: sample(i), 5)))
    assert set(states(r)) == {GOOD}
    r = run(seq((lambda i: sample(i), 5), (full, 10), (lambda i: sample(i), 10)))
    assert changes(r) == [0, GOOD, 7, BUSY, 19, GOOD]
    assert r.minutes[10].cause == "busy"


def test_slow_outranks_busy():
    both = lambda i: sample(i, down=100_000_000, edges={"edge-a": edge(3, 1)})  # noqa: E731
    assert SLOW in states(run(seq((both, 60))))


def test_full_speed_reached_recently_is_proof_and_otherwise_download_is_not_measured():
    fast = lambda i: sample(i, pk_down=1_200_000)  # noqa: E731
    download = lambda r: {a["key"]: a["state"] for a in r.tracker.activities()}["download"]  # noqa: E731
    recent = run(seq((lambda i: sample(i), 10), (fast, 1), (lambda i: sample(i), 30)))
    stale = run(seq((lambda i: sample(i), 10), (fast, 1), (lambda i: sample(i), 70)))
    assert download(recent) == GOOD
    assert download(stale) == NOT_MEASURED
    assert set(states(stale)) == {GOOD}


def test_a_failed_goodput_probe_is_slow_once_a_baseline_exists_until_proof_or_a_good_run():
    quiet = healthy(120)
    failed = [goodput(30, ok=False, mbit=None)]
    r = run(quiet, failed, base=MATURE)
    assert states(r)[29] == GOOD and set(states(r)[30:]) == {SLOW}
    assert r.minutes[40].cause == "slow_path"
    cleared = run(quiet, [*failed, goodput(100, ok=True, mbit=8.0)], base=MATURE)
    assert states(cleared)[99] == SLOW and states(cleared)[100] == GOOD
    proof = [*quiet[:60], sample(60, pk_down=1_200_000), *quiet[61:]]
    assert states(run(proof, failed, base=MATURE))[60] == GOOD


def test_learning_mode_never_reports_slow_from_a_probe():
    r = run(healthy(60), [goodput(30, ok=False, mbit=None)], base=Baseline())
    assert set(states(r)) == {GOOD}


def test_a_goodput_far_below_the_usual_is_slow_the_august_shape():
    r = run(healthy(60), [goodput(10, ok=True, mbit=0.05)], base=MATURE)
    assert states(r)[10:] == [SLOW] * 50
    assert run(healthy(60), [goodput(10, ok=True, mbit=3.0)], base=MATURE).minutes[-1].state == GOOD


def test_a_stale_probe_result_stops_counting():
    r = run(healthy(2200), [goodput(10, ok=False, mbit=None)], base=MATURE)
    assert r.minutes[-1].state == GOOD


def test_one_poor_call_test_is_not_enough_two_in_a_row_are():
    base = healthy(100)
    assert set(states(run(base, [stun(20, jitter=45.0)], base=MATURE))) == {GOOD}
    r = run(base, [stun(20, jitter=45.0), stun(50, loss=5.0)], base=MATURE)
    assert states(r)[49] == GOOD and states(r)[50] == CHOPPY
    assert r.minutes[60].cause == "calls"
    recovered = run(base, [stun(20, jitter=45.0), stun(50, loss=5.0), stun(80)], base=MATURE)
    assert states(recovered)[80] == GOOD


def test_a_poor_call_test_during_a_call_is_enough_on_its_own():
    r = run([sample(i, calls=1 if i < 25 else 0) for i in range(60)], [stun(20, jitter=45.0)], base=MATURE)
    assert r.minutes[21].state == CHOPPY


def test_rtt_is_judged_against_the_baseline_only_when_there_is_one():
    slow = [stun(10, rtt=400.0), stun(40, rtt=410.0)]
    assert states(run(healthy(80), slow, base=MATURE))[40] == CHOPPY
    assert set(states(run(healthy(80), slow, base=Baseline()))) == {GOOD}


def test_a_call_test_with_no_replies_is_choppy_and_says_so():
    r = run(healthy(80), [stun(10, ok=False), stun(40, ok=False)], base=MATURE)
    assert r.minutes[40].state == CHOPPY
    assert any("no replies" in e["text"] for e in r.tracker.verdict(T0 + 80 * 60)["evidence"])


def test_a_choppy_call_on_a_full_uplink_is_attributed_to_the_busy_link():
    r = run(
        [sample(i, calls=1, up=30_000_000) for i in range(50)],
        [stun(10, jitter=45.0), stun(40, jitter=50.0)],
        base=MATURE,
    )
    assert r.minutes[45].state == CHOPPY and r.minutes[45].cause == "calls_busy"


def test_an_abrupt_new_boot_is_a_power_cut_a_graceful_one_a_restart():
    before, after = healthy(10), healthy(5, start=100, boot="b2")
    r = run([*before, *after], events=[boot_event(99, graceful=False)])
    assert states(r)[10:100] == [POWER_CUT] * 90 and r.minutes[50].cause == "power_cut"
    r = run([*before, *after], events=[boot_event(99, graceful=True)])
    assert set(states(r)[10:100]) == {NOT_MEASURED} and r.minutes[50].cause == "restart"


def test_a_hole_on_the_same_boot_is_the_monitor_not_the_network():
    r = run([*healthy(10), *healthy(5, start=60)])
    assert set(states(r)[10:60]) == {NOT_MEASURED} and r.minutes[30].cause == "monitor"


def test_a_missed_minute_or_two_is_not_a_gap():
    r = run([*healthy(10), *healthy(10, start=12)])
    assert len(r.minutes) == 20 and set(states(r)) == {GOOD}


def test_evidence_does_not_cross_a_gap():
    bad = lambda i: sample(i, edges={"edge-a": edge(3, 1)})  # noqa: E731
    r = run([*seq((bad, 40)), *healthy(30, start=500, boot="b2")], events=[boot_event(499, graceful=False)])
    assert set(states(r)[500:]) == {GOOD}


def test_a_series_that_stopped_growing_is_not_measured_never_green():
    r = run(healthy(30))
    assert r.tracker.verdict(T0 + 30 * 60)["state"] == GOOD
    stale = r.tracker.verdict(T0 + 30 * 60 + 600)
    assert stale["state"] == NOT_MEASURED and stale["cause"] == "monitor"


def test_blind_collectors_are_not_measured():
    r = run([*healthy(5), sample(5, frames=0, paths=False)])
    assert r.minutes[-1].state == NOT_MEASURED and r.minutes[-1].cause == "feed"


def test_overheating_is_blamed_only_while_slow():
    bad = lambda i, **kw: sample(i, edges={"edge-a": edge(3, 1)}, **kw)  # noqa: E731
    hot = run([bad(i, host={"temp": 82.0}) for i in range(60)])
    assert hot.minutes[-1].cause == "overheating"
    throttled = run([bad(i, host={"temp": 76.0, "freq": 0.6}) for i in range(60)])
    assert throttled.minutes[-1].cause == "overheating"
    idle = run([bad(i, host={"temp": 66.0, "freq": 0.6}) for i in range(60)])
    assert idle.minutes[-1].cause == "route_degraded"


def test_confidence_says_how_the_verdict_was_reached():
    busy = run(healthy(30, new=3)).tracker
    assert busy.verdict(T0 + 30 * 60)["confidence"] == "real traffic"
    quiet = run(healthy(30, new=0), base=MATURE).tracker
    assert quiet.verdict(T0 + 30 * 60)["confidence"] == "probe only"
    assert run(healthy(30, new=0)).tracker.verdict(T0 + 30 * 60)["confidence"] == "learning"


def test_verdict_names_each_activity_and_carries_evidence():
    v = run(healthy(40), [stun(10)], base=MATURE).tracker.verdict(T0 + 40 * 60)
    assert [a["key"] for a in v["activities"]] == ["browse", "download", "calls"]
    assert {e["signal"] for e in v["evidence"]} >= {"paths", "edge", "download", "calls", "link"}
    assert v["state"] == GOOD and v["cause"] == "ok" and v["label"] == "Network healthy"


def test_capacity_scales_the_busy_threshold():
    assert verdict.BUSY_UTIL * CAP.down_bps * 60 < 100_000_000


def test_the_primary_route_is_the_first_in_the_controllers_failover_order():
    both = {"route-a": True, "route-b": True}
    on_a = run(seq((lambda i: sample(i, active="route-a", alive=both), 3)))
    assert on_a.minutes[-1].why == ()
    swapped = {"route-b": True, "route-a": True}
    on_a_when_b_is_preferred = run(seq((lambda i: sample(i, active="route-a", alive=swapped), 3)))
    assert "backup_route" in on_a_when_b_is_preferred.minutes[-1].why


def test_probes_that_fail_while_every_route_is_down_do_not_outlive_the_outage():
    dead = lambda i: sample(i, alive=DEAD, down=0, chains={})  # noqa: E731
    samples = seq((lambda i: sample(i), 10), (dead, 20), (lambda i: sample(i), 90))
    during = [goodput(15, ok=False, mbit=None), stun(18, ok=False), stun(25, ok=False)]
    r = run(samples, during, base=MATURE)
    assert SLOW not in states(r)[35:] and CHOPPY not in states(r)[35:]
    after = run(samples, [goodput(60, ok=False, mbit=None)], base=MATURE)
    assert SLOW in states(after)


def test_a_topology_that_no_longer_matches_what_is_running_is_reported():
    r = run(seq((lambda i: sample(i, err=["edge:edge-b"]), 3)))
    assert r.tracker.verdict(T0 + 60 * 3)["config"] == ["cannot read edge edge-b's log"]
    gone = verdict.evaluate(seq((lambda i: sample(i), 3)), [], [], CAP, LEARNING, {"renamed": "edge-a"})
    assert gone.tracker.config_issues() == ["route renamed is not known to the proxy controller"]
    assert verdict.evaluate([], [], [], CAP, LEARNING, {}).tracker.config_issues() == [
        "no routes are mapped to edge instances"
    ]
    assert run(seq((lambda i: sample(i), 3))).tracker.config_issues() == []
