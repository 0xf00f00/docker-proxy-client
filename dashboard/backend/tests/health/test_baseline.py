from app.services.health import baseline
from app.services.health.model import Probe
from tests.health.series import T0

NOW = T0 + 20 * 86400


def gp(day, mbit, ok=True, path="route-a"):
    return Probe(NOW - day * 86400, "goodput", path, ok, 0, {"mbit": mbit if ok else None})


def st(hour, rtt, ok=True, path="route-a"):
    return Probe(NOW - hour * 3600, "stun", path, ok, 0, {"rtt_p95": rtt if ok else None})


def test_nothing_is_known_until_enough_runs_exist():
    b = baseline.build([gp(i, 8.0) for i in range(1, 5)] + [st(i, 140.0) for i in range(1, 11)], "route-a", NOW)
    assert (b.goodput_mbit, b.stun_rtt_p95, b.ready) == (None, None, False)
    assert (b.goodput_n, b.stun_n) == (4, 10)


def test_goodput_uses_a_high_quantile_so_a_bad_week_cannot_become_normal():
    runs = [gp(d, 9.0) for d in range(8, 12)] + [gp(d, 0.5) for d in range(1, 7)]
    b = baseline.build(runs, "route-a", NOW)
    assert b.goodput_mbit == 9.0


def test_rtt_is_the_median_of_the_per_probe_p95():
    b = baseline.build([st(i, 100.0 + i) for i in range(1, 25)], "route-a", NOW)
    assert b.stun_rtt_p95 == 113.0


def test_other_paths_failures_and_old_runs_are_ignored():
    runs = [gp(d, 9.0) for d in range(1, 8)]
    mixed = [*runs, gp(1, 1.0, path="route-b"), gp(2, 0.0, ok=False), gp(30, 50.0)]
    b = baseline.build(mixed, "route-a", NOW)
    assert (b.goodput_mbit, b.goodput_n) == (9.0, 7)
    assert baseline.build(runs, "route-b", NOW).goodput_mbit is None
