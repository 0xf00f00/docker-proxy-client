from app.services.health.collectors.edge_logs import EdgeLogFollower, classify

HELLO = "2024/01/01 12:00:00 injector: fake ClientHello sent (1/1, 660 bytes, 1 segment(s))"
ACK = "2024/01/01 12:00:00 proxy: ACK timeout after 2s"
DIAL_TO = "2024/01/01 12:00:00 Failed to connect to 192.0.2.10:443: dial tcp4 192.0.2.10:443: i/o timeout"
UNREACH = (
    "2024/01/01 12:00:00 Failed to connect to 192.0.2.10:443: dial tcp4 192.0.2.10:443: connect: network is unreachable"
)
ABORT = "2024/01/01 12:00:00 proxy: injector aborted handshake"
NOISE = "2024/01/01 12:00:00 nfqueue: recv buffer 4194304"


def test_classify_known_lines():
    assert classify(HELLO) == "hello"
    assert classify(ACK) == "ack_to"
    assert classify(DIAL_TO) == "dial_to"
    assert classify(UNREACH) == "unreach"
    assert classify(ABORT) == "rst"
    assert classify(NOISE) is None


def follower():
    return EdgeLogFollower(["sni-spoofing", "sni-spoofing-fallback"], client_factory=lambda: None)


def test_drain_counts_followed_containers_and_reports_blind_ones():
    f = follower()
    f._set_following("sni-spoofing", True)
    f.feed_lines("sni-spoofing", [HELLO, HELLO, HELLO, ACK, NOISE])
    counts, blind = f.drain()
    assert counts == {"sni-spoofing": {"hello": 3, "ack_to": 1, "dial_to": 0, "unreach": 0, "dial_err": 0, "rst": 0}}
    assert blind == ["sni-spoofing-fallback"]


def test_drain_resets_counters():
    f = follower()
    f._set_following("sni-spoofing", True)
    f.feed_lines("sni-spoofing", [HELLO, ACK])
    f.drain()
    counts, _ = f.drain()
    assert counts["sni-spoofing"]["hello"] == 0
