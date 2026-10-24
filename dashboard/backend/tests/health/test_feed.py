from app.services.health.collectors.feed import FeedAggregator
from app.services.system_proxy.base import Connection, ConnectionSnapshot


def conn(cid, up=0, down=0, *, internal=False, net="tcp", port="443", chain="xray", host=""):
    return Connection(
        id=cid,
        host=host,
        dest_ip="",
        dest_port=port,
        network=net,
        chains=(chain, "auto"),
        upload=up,
        download=down,
        rule="",
        start="",
        internal=internal,
    )


def snap(*conns, up_total=0, down_total=0):
    return ConnectionSnapshot(upload_total=up_total, download_total=down_total, connections=tuple(conns))


def aggregator():
    return FeedAggregator()


def test_no_frames_reports_zero():
    assert aggregator().drain() == {"frames": 0}


def test_flows_open_at_start_are_baseline_only():
    agg = aggregator()
    agg.ingest(snap(conn("a", 500, 0)), now=0)
    agg.ingest(snap(), now=1)
    user = agg.drain()["user"]
    assert user["new"] == 0
    assert user["closed"] == 1
    assert user["nores"] == 0


def test_no_response_flow_counts_as_nores_on_its_chain():
    agg = aggregator()
    agg.ingest(snap(), now=0)
    agg.ingest(snap(conn("a", 517, 0)), now=1)
    agg.ingest(snap(conn("a", 517, 0)), now=3)
    agg.ingest(snap(), now=4)
    out = agg.drain()
    assert out["user"]["new"] == 1
    assert out["user"]["nores"] == 1
    assert out["user"]["early1k"] == 1
    assert out["chains"]["xray"]["nores"] == 1


def test_small_successful_response_is_not_nores():
    agg = aggregator()
    agg.ingest(snap(), now=0)
    agg.ingest(snap(conn("a", 120, 150, port="80")), now=1)
    agg.ingest(snap(), now=2)
    user = agg.drain()["user"]
    assert user["nores"] == 0
    assert user["early1k"] == 1


def test_preconnect_that_sent_nothing_is_not_a_failure():
    agg = aggregator()
    agg.ingest(snap(), now=0)
    agg.ingest(snap(conn("a", 0, 0)), now=1)
    agg.ingest(snap(), now=2)
    user = agg.drain()["user"]
    assert user["nores"] == 0
    assert user["early1k"] == 0


def test_controller_own_health_checks_are_excluded():
    agg = aggregator()
    agg.ingest(snap(), now=0)
    agg.ingest(snap(conn("check", 300, 0, internal=True)), now=1)
    agg.ingest(snap(), now=2)
    out = agg.drain()
    assert out["user"]["new"] == 0
    assert out["user"]["nores"] == 0
    assert out["chains"] == {}


def test_bulk_rate_is_bytes_per_active_second():
    agg = aggregator()
    agg.ingest(snap(), now=0)
    for i in range(1, 6):
        agg.ingest(snap(conn("dl", 1000, i * 250_000)), now=i)
    out = agg.drain()
    assert out["bulk"]["n"] == 1
    assert out["bulk"]["p50"] == 250_000
    assert out["user"]["down"] == 1_250_000


def test_slow_long_download_shows_as_low_bulk_rate():
    agg = aggregator()
    agg.ingest(snap(), now=0)
    for i in range(1, 61):
        agg.ingest(snap(conn("dl", 2000, i * 5_000)), now=i)
    out = agg.drain()
    assert out["bulk"]["n"] == 1
    assert out["bulk"]["p90"] == 5_000


def test_call_shaped_udp_flow_and_quic_are_separated():
    agg = aggregator()
    agg.ingest(snap(), now=0)
    for i in range(1, 61):
        call = conn("call", i * 3_000, i * 3_500, net="udp", port="19305")
        quic = conn("quic", i * 2_000, i * 90_000, net="udp", port="443")
        agg.ingest(snap(call, quic), now=i)
    out = agg.drain()
    assert out["calls"]["n"] == 1
    assert out["calls"]["meet"] == 1
    assert out["quic"]["down"] == 60 * 90_000


def test_totals_survive_controller_restart_without_negative_bytes():
    agg = aggregator()
    agg.ingest(snap(up_total=1_000, down_total=10_000), now=0)
    agg.ingest(snap(up_total=2_000, down_total=20_000), now=1)
    agg.ingest(snap(up_total=50, down_total=100), now=2)
    agg.ingest(snap(up_total=150, down_total=600), now=3)
    out = agg.drain()
    assert out["up"] == 1_100
    assert out["down"] == 10_500


def test_peak_rate_uses_a_multi_second_window():
    agg = aggregator()
    totals = [0, 100_000, 1_100_000, 2_100_000, 3_100_000, 3_200_000, 3_300_000]
    for i, d in enumerate(totals):
        agg.ingest(snap(down_total=d), now=i)
    out = agg.drain()
    assert out["pk_down"] == 1_000_000


def test_reset_after_feed_error_rebaselines():
    agg = aggregator()
    agg.ingest(snap(), now=0)
    agg.ingest(snap(conn("a", 10, 10)), now=1)
    agg.reset()
    agg.ingest(snap(conn("a", 5_000, 900_000)), now=5)
    agg.ingest(snap(conn("a", 5_000, 900_000)), now=6)
    out = agg.drain()
    assert out["user"]["down"] == 10
    assert out["user"]["new"] == 1


def test_minute_counters_restart_after_drain():
    agg = aggregator()
    agg.ingest(snap(), now=0)
    agg.ingest(snap(conn("a", 100, 400_000)), now=1)
    agg.drain()
    agg.ingest(snap(conn("a", 100, 400_000)), now=61)
    out = agg.drain()
    assert out["user"]["down"] == 0
    assert out["user"]["new"] == 0
    assert out["user"]["conns"] == 1


def test_connections_matching_a_running_probe_are_not_user_traffic():
    agg = aggregator()
    agg.expect_probe("udp", "19302", frozenset(), 5, now=0)
    agg.ingest(snap(), now=0)
    agg.ingest(
        snap(
            conn("probe", 100, 100, net="udp", port="19302"),
            conn("user", 100, 100, net="udp", port="443"),
            conn("meet-tcp", 100, 100, net="tcp", port="19302"),
        ),
        now=1,
    )
    assert agg.drain()["user"]["new"] == 2


def test_probe_window_expires_and_hosts_narrow_the_match():
    agg = aggregator()
    agg.expect_probe("tcp", "443", frozenset({"probe.example"}), 5, now=0)
    agg.ingest(snap(), now=0)
    agg.ingest(snap(conn("probe", 10, 10, host="probe.example"), conn("other", 10, 10, host="www.example")), now=1)
    assert agg.drain()["user"]["new"] == 1
    agg.ingest(snap(conn("late", 10, 10, host="probe.example")), now=9)
    assert agg.drain()["user"]["new"] == 1
