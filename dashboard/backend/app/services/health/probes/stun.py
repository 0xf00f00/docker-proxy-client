"""Call-quality probe: STUN binding requests over UDP on the default route, the path Meet media takes."""

import asyncio
import os
import socket
import time
from itertools import pairwise

from app.services.health.probes import ProbeResult, percentile

HOST = os.environ.get("HEALTH_STUN_HOST", "stun.l.google.com")
PORT = 19302
COUNT = 20
INTERVAL_S = 0.1
SETTLE_S = 2.0
TUNNEL_OVERHEAD = 1.3
_REQUEST_WIRE, _REPLY_WIRE = 48, 60
_COOKIE = b"\x21\x12\xa4\x42"


def build_request(txid: bytes) -> bytes:
    return b"\x00\x01\x00\x00" + _COOKIE + txid


def reply_txid(data: bytes) -> bytes | None:
    if len(data) >= 20 and data[:2] == b"\x01\x01" and data[4:8] == _COOKIE:
        return data[8:20]
    return None


def summarize(sent: int, rtts_ms: list[float]) -> dict:
    """RTT p50/p95, jitter (mean step between consecutive replies) and loss."""
    steps = [abs(b - a) for a, b in pairwise(rtts_ms)]
    p50, p95 = percentile(rtts_ms, 50), percentile(rtts_ms, 95)
    return {
        "sent": sent,
        "recv": len(rtts_ms),
        "loss": round(100 * (sent - len(rtts_ms)) / sent, 1) if sent else None,
        "rtt_p50": round(p50, 1) if p50 is not None else None,
        "rtt_p95": round(p95, 1) if p95 is not None else None,
        "jitter": round(sum(steps) / len(steps), 1) if steps else None,
    }


class _Replies(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.arrived: dict[bytes, float] = {}

    def datagram_received(self, data: bytes, addr) -> None:
        txid = reply_txid(data)
        if txid is not None:
            self.arrived.setdefault(txid, time.monotonic())


async def run(
    count: int = COUNT, host: str = HOST, port: int = PORT, interval: float = INTERVAL_S, settle: float = SETTLE_S
) -> ProbeResult:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
    transport, replies = await loop.create_datagram_endpoint(_Replies, remote_addr=infos[0][4])
    sent_at: dict[bytes, float] = {}
    order: list[bytes] = []
    try:
        for _ in range(count):
            txid = os.urandom(12)
            sent_at[txid] = time.monotonic()
            order.append(txid)
            transport.sendto(build_request(txid))
            await asyncio.sleep(interval)
        await asyncio.sleep(settle)
    finally:
        transport.close()
    rtts = [(replies.arrived[t] - sent_at[t]) * 1000 for t in order if t in replies.arrived]
    wire = (count * _REQUEST_WIRE + len(rtts) * _REPLY_WIRE) * TUNNEL_OVERHEAD
    return ProbeResult(bool(rtts), round(wire), summarize(count, rtts))
