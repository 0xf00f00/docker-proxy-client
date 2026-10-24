import asyncio

from app.services.health.probes import stun


def test_request_is_a_binding_request_with_the_txid():
    txid = bytes(range(12))
    req = stun.build_request(txid)
    assert len(req) == 20
    assert req[:2] == b"\x00\x01" and req[8:] == txid


def test_reply_txid_only_for_binding_success():
    txid = bytes(range(12))
    reply = b"\x01\x01\x00\x00\x21\x12\xa4\x42" + txid
    assert stun.reply_txid(reply) == txid
    assert stun.reply_txid(b"\x01\x11\x00\x00\x21\x12\xa4\x42" + txid) is None
    assert stun.reply_txid(b"\x01\x01") is None


def test_summary_of_a_clean_run():
    out = stun.summarize(4, [100.0, 110.0, 100.0, 110.0])
    assert out == {"sent": 4, "recv": 4, "loss": 0.0, "rtt_p50": 110.0, "rtt_p95": 110.0, "jitter": 10.0}


def test_summary_counts_loss_and_survives_total_loss():
    assert stun.summarize(4, [100.0])["loss"] == 75.0
    lost = stun.summarize(4, [])
    assert lost["loss"] == 100.0 and lost["rtt_p50"] is None and lost["jitter"] is None


class _Echo(asyncio.DatagramProtocol):
    def __init__(self, drop_every=0):
        self.seen = 0
        self.drop_every = drop_every

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.seen += 1
        if self.drop_every and self.seen % self.drop_every == 0:
            return
        self.transport.sendto(b"\x01\x01\x00\x00\x21\x12\xa4\x42" + data[8:20], addr)


def _serve(drop_every=0):
    async def go():
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: _Echo(drop_every), local_addr=("127.0.0.1", 0))
        port = transport.get_extra_info("sockname")[1]
        try:
            return await stun.run(count=10, host="127.0.0.1", port=port, interval=0.005, settle=0.2)
        finally:
            transport.close()

    return asyncio.run(go())


def test_probe_against_a_local_responder():
    result = _serve()
    assert result.ok
    assert result.data["recv"] == 10 and result.data["loss"] == 0.0
    assert result.bytes == round((10 * 48 + 10 * 60) * 1.3)


def test_probe_reports_loss():
    result = _serve(drop_every=2)
    assert result.ok
    assert result.data["recv"] == 5 and result.data["loss"] == 50.0


def test_probe_with_no_responder_fails():
    async def go():
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
        port = transport.get_extra_info("sockname")[1]
        try:
            return await stun.run(count=3, host="127.0.0.1", port=port, interval=0.005, settle=0.1)
        finally:
            transport.close()

    result = asyncio.run(go())
    assert not result.ok and result.data["recv"] == 0
