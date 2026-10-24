from app.services.health import rules
from app.services.health.rules import Capacity, edge_state

DEFAULT = Capacity(2e6, 4e5)


def minutes(n, attempts, fails):
    return [(attempts, fails)] * n


def test_too_few_attempts_is_not_a_verdict():
    assert edge_state(minutes(30, 1, 0)) == "nodata"
    assert edge_state([]) == "nodata"


def test_healthy_ratio_is_ok():
    assert edge_state(minutes(30, 3, 0)) == "ok"
    assert edge_state([(3, 1), *minutes(29, 3, 0)]) == "ok"


def test_sustained_ratio_grades_watch_then_poor():
    assert edge_state([(4, 0)] * 20 + [(4, 1)] * 10) == "watch"
    assert edge_state([(3, 0)] * 15 + [(3, 1)] * 15) == "poor"


def test_total_handshake_failure_is_fast_even_in_a_short_window():
    quiet = minutes(25, 2, 0)
    assert edge_state(quiet + minutes(5, 2, 2)) == "fast"


def test_fast_needs_enough_attempts():
    assert edge_state(minutes(25, 2, 0) + [(0, 0)] * 4 + [(2, 2)]) != "fast"


def test_only_the_newest_window_counts():
    old_bad = minutes(40, 3, 3)
    assert edge_state(old_bad + minutes(30, 3, 0)) == "ok"


def test_capacity_defaults_until_a_day_of_data():
    assert rules.learn_capacity([1e6] * 100, [1e5] * 100, DEFAULT) == DEFAULT


def test_capacity_reads_download_p95_and_upload_extreme_tail():
    down = [float(i) for i in range(1, 2001)]
    up = [10.0] * 1996 + [100.0, 200.0, 300.0, 400.0]
    cap = rules.learn_capacity(down, up, DEFAULT)
    assert 1900 <= cap.down_bps <= 1902
    assert cap.up_bps >= 100.0


def test_utilization_is_the_busier_direction_per_minute_average():
    cap = Capacity(down_bps=1000.0, up_bps=100.0)
    assert rules.utilization({"down": 30_000, "up": 0}, cap) == 0.5
    assert rules.utilization({"down": 0, "up": 3_000}, cap) == 0.5
    assert rules.utilization({}, cap) == 0
