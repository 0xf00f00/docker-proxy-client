"""Proxy stability probe (see docs/realtime-stability-repro.md).

A plain connectivity test (one request, mean latency) reports "stable" on
proxies that are visibly broken in real use, because it never creates the two
conditions that actually break under DPI: connection *concurrency* (what a docker
pull does) and *sustained load* (what makes a Google Meet call choppy). This
creates both and grades them separately:

  Bulk  — under many parallel DOWNLOAD streams, the reset/stall rate.
  Call  — latency spikes/jitter while the UPLOAD is saturated. Calls break in the
          upload direction: the uplink is the scarce side and a single muxed
          tunnel head-of-line-blocks the call behind bulk bytes. The download
          direction has headroom, so saturating downloads does NOT reproduce the
          lag — Call is probed under its own short upload-saturation phase.
  UDP   — can a STUN binding survive over the proxy? If not, real calls fall back
          to a slower TCP relay. Best-effort, SOCKS5-only (HTTP proxies can't
          carry UDP at all).

The Call latency is measured on ONE warm, kept-open connection pinged at a fixed
cadence — the way a live call sends media on an already-established path. An
earlier version opened a fresh connection per sample, which charged every probe a
TCP+proxy handshake *under load* and so measured connection-setup-under-load, not
call lag; it also produced thin samples (a handful of slow probes reading "100%
froze"). Idle and loaded samples now come from the same warm connection, so they
are like-for-like and the inflation ratio compares matching percentiles (p95/p95),
not loaded-max / idle-median.

Quota: users pay for metered data, so this is kept light. The download streams
are byte-capped (~CONCURRENCY x STREAM_CAP_BYTES). The upload phase saturates the
scarce uplink for CALL_ROUNDS x UPLOAD_WINDOW_S and is then cancelled, so it moves
only ~uplink-rate x window (a few MB), well under the download budget.

WARNING: it briefly saturates the tunnel (both directions), degrading any live
user for its duration — on-demand / quiet-window only, never an unattended loop.
"""

import asyncio
import contextlib
import os
import socket
import statistics
import struct
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

from app.models.schemas import ContainerInfo, RegimeInfo, StabilityResult
from app.services.connectivity_tests import regime as regime_mod

EventCb = Callable[[str, dict], Awaitable[None]]

LATENCY_URL = "http://www.gstatic.com/generate_204"
STREAM_CAP_BYTES = 3_000_000
LOAD_URL = f"https://speed.cloudflare.com/__down?bytes={STREAM_CAP_BYTES}"

# Upload-saturation (Call) phase. Calls break in the upload direction, so call
# latency is probed while the uplink is saturated — not during the (healthy)
# download phase. The uplink is the scarce side, so a short fixed window saturates
# it on a few streams; we cancel them when the window ends. The per-stream cap is
# only a ceiling — actual data moved ≈ uplink-rate x window (a few MB). Two short
# rounds sample two moments (DPI degradation is transient) instead of one window.
UPLOAD_URL = "https://speed.cloudflare.com/__up"
UPLOAD_CONCURRENCY = 6
UPLOAD_WINDOW_S = 8.0
CALL_ROUNDS = 2
UPLOAD_STREAM_CAP_BYTES = 2_000_000
UPLOAD_CHUNK = 65_536

# Warm call-path probe: one kept-open connection pinged at a fixed cadence, both
# idle and under load. ~13 pings/s is dense enough for real jitter/percentiles
# without itself being a load.
PING_INTERVAL_S = 0.075
PING_TIMEOUT_S = 8.0
MIN_CALL_SAMPLES = 5  # below this the loaded sample is too thin to grade

CONCURRENCY = 10  # enough parallel streams to trip concurrency-triggered DPI
STREAM_TIMEOUT = 45.0
STALL_S = 5.0  # no bytes for this long = stalled
MIN_BYTES = 2_000_000  # a stream must move this much to count as completed
IDLE_PROBES = 10

# Long-lived survival: small periodic requests over a held connection — trivial
# data, models a call with talk gaps (DPI often resets long/idle sessions). The
# hold must outlast typical idle-reset timers; 60s with a 30s gap between trickles
# leaves a real multi-second silence for the timer to fire on (a tighter trickle
# keeps the connection "active" and hides the reset).
LONGLIVED_COUNT = 2
LONGLIVED_HOLD_S = 60.0
LONGLIVED_TRICKLE_S = 30.0

# UDP/WebRTC reachability: a STUN binding over the proxy. Best-effort, short.
STUN_HOST = "stun.l.google.com"
STUN_PORT = 19302
UDP_PROBE_TIMEOUT_S = 4.0

# Worst-case data ceiling (probes are ~0-byte 204s; trickle is negligible). The
# upload phase is link-bounded in practice, so the real figure is well under this.
DATA_BUDGET_MB = round(
    (CONCURRENCY * STREAM_CAP_BYTES + CALL_ROUNDS * UPLOAD_CONCURRENCY * UPLOAD_STREAM_CAP_BYTES) / 1_000_000
)

# Grading. Tail-spike based: p50 can look fine while the tail hits multiple seconds
# — calls freeze on those spikes, so we grade on p95-inflation and the fraction of
# pings over SPIKE_THRESHOLD. The Call signal comes from the UPLOAD-saturation
# phase, measured on a warm connection (see _load_and_probe). NOTE: these
# thresholds are reasonable defaults for warm-connection p95 numbers but still want
# confirmation against a few live captures on this link.
SPIKE_THRESHOLD_MS = 1000.0  # an RTT spike past this freezes a Meet call
BULK_BAD_RATE = 0.10
BULK_DEGRADED_RATE = 0.03
CALL_BAD_INFLATION = 6.0  # loaded p95 ≥ 6x idle p95
CALL_DEGRADED_INFLATION = 2.5
CALL_BAD_SPIKE_PCT = 3.0
CALL_DEGRADED_SPIKE_PCT = 0.5
CALL_BAD_JITTER_MS = 120.0
CALL_DEGRADED_JITTER_MS = 50.0
CALL_BAD_LOSS = 5.0
CALL_DEGRADED_LOSS = 1.0


def _proxy_url(protocol: str, address: str) -> str | None:
    proto = protocol.split("+")[0]
    if proto in ("socks5", "socks"):
        return f"socks5://{address}"
    if proto in ("http", "mixed"):
        return f"http://{address}"
    return None


def _jitter(samples: list[float]) -> float | None:
    """Mean absolute difference between consecutive samples — the packet-delay-
    variation definition WebRTC cares about, not raw stdev."""
    if len(samples) < 2:
        return None
    diffs = [abs(samples[i] - samples[i - 1]) for i in range(1, len(samples))]
    return round(statistics.fmean(diffs), 1)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _pct(values: list[float], p: float) -> float | None:
    """Linear-interpolated percentile (matches the 'inclusive' method)."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return round(s[0], 1)
    k = (len(s) - 1) * (p / 100)
    f = int(k)
    v = s[f] if f + 1 >= len(s) else s[f] + (s[f + 1] - s[f]) * (k - f)
    return round(v, 1)


async def _one_stream(proxy_url: str) -> str:
    """Stream the byte-capped LOAD_URL, classifying: completed | reset | stalled."""
    got = 0
    start = time.monotonic()
    last_byte = start
    try:
        async with (
            httpx.AsyncClient(proxy=proxy_url, timeout=httpx.Timeout(15.0, read=STALL_S + 2)) as client,
            client.stream("GET", LOAD_URL) as resp,
        ):
            if resp.status_code != 200:
                return "reset"
            async for chunk in resp.aiter_bytes():
                now = time.monotonic()
                if chunk:
                    got += len(chunk)
                    last_byte = now
                if now - last_byte >= STALL_S:
                    return "stalled"
                if now - start >= STREAM_TIMEOUT:
                    break
        return "completed" if got >= MIN_BYTES else "stalled"
    except httpx.ReadTimeout, httpx.ReadError:
        return "stalled" if (time.monotonic() - last_byte) >= STALL_S else "reset"
    except Exception:
        return "reset"


async def _warm_ping_series(
    client: httpx.AsyncClient, *, deadline: float | None = None, count: int | None = None
) -> tuple[list[float], int]:
    """Ping LATENCY_URL over an already-warm `client` at PING_INTERVAL_S cadence
    until `deadline` (time.monotonic) or `count` pings. Reuses the kept-open
    connection, so after warm-up each sample is RTT on the established path — not a
    fresh handshake. Returns (latencies_ms, loss)."""
    lat: list[float] = []
    loss = 0
    n = 0
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if count is not None and n >= count:
            break
        t0 = time.monotonic()
        try:
            resp = await client.get(LATENCY_URL)
            dt = (time.monotonic() - t0) * 1000
            if resp.status_code == 204:
                lat.append(round(dt, 1))
            else:
                loss += 1
        except Exception:
            loss += 1
        n += 1
        gap = PING_INTERVAL_S - (time.monotonic() - t0)
        if gap > 0:
            await asyncio.sleep(gap)
    return lat, loss


async def _one_upload_stream(proxy_url: str) -> None:
    """Saturate the uplink with a byte-capped zero-body POST. The cap is only a
    ceiling — on a slow uplink the surrounding window cancels the task first, so
    this moves ~uplink-rate x window, not the cap. Outcome is unused: the Call
    signal is the latency probed *during* this load, not the stream's fate."""
    sent = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal sent
        chunk = b"\x00" * UPLOAD_CHUNK
        while sent < UPLOAD_STREAM_CAP_BYTES:
            yield chunk
            sent += len(chunk)

    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=httpx.Timeout(STREAM_TIMEOUT)) as client:
            await client.post(UPLOAD_URL, content=body())
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def _load_and_probe(proxy_url: str, emit: EventCb) -> dict:
    """Idle baseline + two load phases, all over ONE warm call-path connection.
    DOWNLOAD concurrency feeds the Bulk grade (DPI reset/stall under many parallel
    streams). UPLOAD saturation feeds the Call grade: calls break in the upload
    direction (scarce uplink + a muxed tunnel that HOL-blocks the call behind
    bulk), and the download direction has headroom, so probing call latency under
    downloads would understate the lag."""
    limits = httpx.Limits(max_keepalive_connections=1, max_connections=1)
    await emit("progress", {"phase": "idle"})

    # Warm call-path client: idle + loaded samples share this kept-open connection,
    # so they are like-for-like (no per-sample handshake charged under load).
    async with httpx.AsyncClient(proxy=proxy_url, timeout=PING_TIMEOUT_S, limits=limits) as ping:
        with contextlib.suppress(Exception):
            await ping.get(LATENCY_URL)  # warm the connection before the baseline
        idle, _ = await _warm_ping_series(ping, count=IDLE_PROBES)

        # Call: saturate the uplink over CALL_ROUNDS short windows (two moments,
        # not one), pinging the same warm connection throughout. Merge the samples.
        loaded: list[float] = []
        loss = 0
        for _ in range(CALL_ROUNDS):
            await emit("progress", {"phase": "load"})
            ul = [asyncio.create_task(_one_upload_stream(proxy_url)) for _ in range(UPLOAD_CONCURRENCY)]
            await asyncio.sleep(1.0)  # let the load ramp before sampling
            lat, ls = await _warm_ping_series(ping, deadline=time.monotonic() + UPLOAD_WINDOW_S)
            loaded.extend(lat)
            loss += ls
            for t in ul:
                t.cancel()
            await asyncio.gather(*ul, return_exceptions=True)

    # Bulk: parallel byte-capped downloads → reset/stall counts. Run after the call
    # phase so the warm ping connection isn't left idling mid-test.
    await emit("progress", {"phase": "load"})
    dl = [asyncio.create_task(_one_stream(proxy_url)) for _ in range(CONCURRENCY)]
    counts = {"completed": 0, "reset": 0, "stalled": 0}
    for o in await asyncio.gather(*dl):
        counts[o] += 1

    idle_p95 = _pct(idle, 95)
    loaded_p95 = _pct(loaded, 95)
    loaded_max = max(loaded) if loaded else None
    probed = len(loaded) + loss
    spike = sum(1 for v in loaded if v > SPIKE_THRESHOLD_MS)
    return {
        "streams": CONCURRENCY,
        "completed": counts["completed"],
        "resets": counts["reset"],
        "stalls": counts["stalled"],
        "reset_rate": round(counts["reset"] / CONCURRENCY, 3),
        "stall_rate": round(counts["stalled"] / CONCURRENCY, 3),
        "idle_p50_ms": _median(idle),
        "idle_p95_ms": idle_p95,
        "loaded_p50_ms": _median(loaded),
        "loaded_p95_ms": loaded_p95,
        "loaded_max_ms": round(loaded_max, 1) if loaded_max else None,
        "loaded_jitter_ms": _jitter(loaded),
        "loaded_loss_pct": round(100 * loss / probed, 1) if probed else None,
        "loaded_spike_pct": round(100 * spike / len(loaded), 1) if loaded else None,
        "loaded_samples": len(loaded),
        "latency_inflation": round(loaded_p95 / idle_p95, 1) if (loaded_p95 and idle_p95) else None,
    }


async def _hold_one(proxy_url: str) -> float:
    """Hold a connection open with periodic small requests; return seconds it
    survived (== HOLD if it never reset)."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=PING_TIMEOUT_S) as client:
            while time.monotonic() - start < LONGLIVED_HOLD_S:
                resp = await client.get(LATENCY_URL)
                if resp.status_code != 204:
                    break
                await asyncio.sleep(LONGLIVED_TRICKLE_S)
        return time.monotonic() - start
    except Exception:
        return time.monotonic() - start


async def _longlived(proxy_url: str, emit: EventCb) -> dict:
    """Hold LONGLIVED_COUNT connections open for the fixed window. We announce the
    duration *once* and let the browser run the visible countdown locally —
    streaming a per-second tick is unreliable (SSE chunks can buffer mid-stream
    and freeze the number), and the duration is fixed so the client can derive it."""
    await emit("progress", {"phase": "longlived", "total_s": int(LONGLIVED_HOLD_S)})
    ttls = await asyncio.gather(*(_hold_one(proxy_url) for _ in range(LONGLIVED_COUNT)))
    return {
        "held": LONGLIVED_COUNT,
        "survived": sum(1 for t in ttls if t >= LONGLIVED_HOLD_S - 1),
        "min_ttl_s": round(min(ttls), 1) if ttls else None,
    }


def _recvn(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("short read")
        buf += chunk
    return buf


def _socks5_udp_stun(host: str, port: int) -> tuple[bool | None, str]:
    """Best-effort: open a SOCKS5 UDP ASSOCIATE and send a STUN binding request to
    a public STUN server through it. Blocking sockets — run in a worker thread.
    Returns (supported, detail):
      True  — got a STUN reply: UDP survives, calls can use the fast direct path.
      False — proxy refused UDP, or the relay was set up but no STUN reply came
              back (UDP blocked on the path): calls fall back to a slower TCP relay.
      None  — couldn't determine (network error mid-probe). Never raises."""
    tcp: socket.socket | None = None
    udp: socket.socket | None = None
    try:
        tcp = socket.create_connection((host, port), timeout=UDP_PROBE_TIMEOUT_S)
        tcp.settimeout(UDP_PROBE_TIMEOUT_S)
        tcp.sendall(b"\x05\x01\x00")  # greeting: VER=5, one method, no-auth
        if _recvn(tcp, 2) != b"\x05\x00":
            return False, "The proxy didn't accept an unauthenticated UDP session."
        # UDP ASSOCIATE: VER=5 CMD=3 RSV=0 ATYP=1 0.0.0.0:0
        tcp.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
        rep = _recvn(tcp, 4)
        if rep[1] != 0x00:
            return False, "This proxy can't carry UDP — calls use a slower TCP relay."
        atyp = rep[3]
        if atyp == 0x01:
            relay_host = socket.inet_ntoa(_recvn(tcp, 4))
        elif atyp == 0x04:
            relay_host = socket.inet_ntop(socket.AF_INET6, _recvn(tcp, 16))
        elif atyp == 0x03:
            relay_host = _recvn(tcp, _recvn(tcp, 1)[0]).decode()
        else:
            return None, "Couldn't test UDP this time."
        relay_port = struct.unpack(">H", _recvn(tcp, 2))[0]
        if relay_host in ("0.0.0.0", "::"):  # relay on the proxy host itself
            relay_host = host

        txid = os.urandom(12)
        stun = struct.pack(">HHI", 0x0001, 0, 0x2112A442) + txid  # STUN binding request
        # SOCKS5 UDP datagram header: RSV(2) FRAG(1) ATYP(domain=3) LEN host port
        dst = STUN_HOST.encode()
        dgram = b"\x00\x00\x00\x03" + bytes([len(dst)]) + dst + struct.pack(">H", STUN_PORT) + stun
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.settimeout(UDP_PROBE_TIMEOUT_S)
        udp.sendto(dgram, (relay_host, relay_port))
        data, _ = udp.recvfrom(2048)
        if txid in data:  # STUN reply echoes our transaction id
            return True, "UDP works — calls can use the fast direct path."
        return False, "UDP didn't get through — calls fall back to a slower TCP relay."
    except TimeoutError:
        return False, "UDP didn't get through — calls fall back to a slower TCP relay."
    except Exception:
        return None, "Couldn't test UDP this time."
    finally:
        for s in (udp, tcp):
            if s is not None:
                with contextlib.suppress(Exception):
                    s.close()


async def _udp_probe(protocol: str, probe_address: str, emit: EventCb) -> dict:
    await emit("progress", {"phase": "udp"})
    if protocol.split("+")[0] not in ("socks5", "socks"):
        return {
            "udp_supported": False,
            "udp_detail": "This proxy type can't carry UDP, so calls use a slower TCP relay.",
        }
    host, _, port = (probe_address or "").rpartition(":")
    if not host or not port.isdigit():
        return {"udp_supported": None, "udp_detail": "Couldn't test UDP this time."}
    supported, detail = await asyncio.to_thread(_socks5_udp_stun, host, int(port))
    return {"udp_supported": supported, "udp_detail": detail}


def _grade_bulk(m: dict, longlived: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    rr, sr = m["reset_rate"], m["stall_rate"]
    ll_reset = longlived["survived"] < longlived["held"]

    if m["resets"]:
        reasons.append(f"{m['resets']}/{m['streams']} downloads dropped")
    if m["stalls"]:
        reasons.append(f"{m['stalls']}/{m['streams']} downloads stalled")
    if ll_reset:
        reasons.append(f"connection dropped after {longlived['min_ttl_s']}s")

    if rr > BULK_BAD_RATE or sr > BULK_BAD_RATE or ll_reset:
        return "bad", reasons
    if rr > BULK_DEGRADED_RATE or sr > BULK_DEGRADED_RATE:
        return "degraded", reasons
    return "good", reasons


def _grade_call(m: dict) -> tuple[str, list[str]]:
    if m["loaded_samples"] < MIN_CALL_SAMPLES:
        return "inconclusive", ["not enough call samples under load to judge"]

    reasons: list[str] = []
    infl, spike, jit, loss = (
        m["latency_inflation"],
        m["loaded_spike_pct"],
        m["loaded_jitter_ms"],
        m["loaded_loss_pct"],
    )

    def over(value: float | None, threshold: float) -> bool:
        return value is not None and value > threshold

    if m.get("loaded_max_ms") and over(infl, CALL_DEGRADED_INFLATION):
        reasons.append(f"freezes up to {int(m['loaded_max_ms'])}ms under load")
    if over(spike, CALL_DEGRADED_SPIKE_PCT):
        reasons.append(f"{spike}% of checks froze (>1s)")
    if over(jit, CALL_DEGRADED_JITTER_MS):
        reasons.append(f"{int(jit)}ms jitter under load")
    if over(loss, CALL_DEGRADED_LOSS):
        reasons.append(f"{loss}% loss under load")

    if (
        over(infl, CALL_BAD_INFLATION)
        or over(spike, CALL_BAD_SPIKE_PCT)
        or over(jit, CALL_BAD_JITTER_MS)
        or over(loss, CALL_BAD_LOSS)
    ):
        return "bad", reasons
    if (
        over(infl, CALL_DEGRADED_INFLATION)
        or over(spike, CALL_DEGRADED_SPIKE_PCT)
        or over(jit, CALL_DEGRADED_JITTER_MS)
        or over(loss, CALL_DEGRADED_LOSS)
    ):
        return "degraded", reasons
    return "good", reasons


def _summary(m: dict) -> str:
    parts = [f"{m['completed']}/{m['streams']} downloads OK"]
    if m.get("loaded_p95_ms") is not None:
        parts.append(f"{int(m['loaded_p95_ms'])}ms p95 under load")
    if m.get("loaded_spike_pct"):
        parts.append(f"{m['loaded_spike_pct']}% froze")
    return " · ".join(parts)


def _inconclusive(
    service: str, tested_via: str, regime: RegimeInfo, detail: str, error: str | None = None
) -> StabilityResult:
    return StabilityResult(
        service=service,
        bulk_grade="inconclusive",
        call_grade="inconclusive",
        tested_via=tested_via,
        regime=regime,
        summary=detail,
        reasons=[detail],
        error=error,
    )


async def test_stability(container: ContainerInfo, on_event: EventCb | None = None) -> StabilityResult:
    async def emit(name: str, data: dict) -> None:
        if on_event is not None:
            await on_event(name, data)

    proxy_url = _proxy_url(container.dashboard.protocol, container.probe_address or "")

    await emit("phase", {"phase": "regime"})
    regime = await regime_mod.get_regime()
    await emit("regime", regime.model_dump(mode="json"))

    if proxy_url is None:
        detail = f"Unsupported protocol: {container.dashboard.protocol}"
        return _inconclusive(container.name, "n/a", regime, detail, error=detail)
    # Blackout/outage: every edge fails for the same reason (the link), so a fault
    # can't be attributed to this proxy.
    if regime.regime in ("iran_only", "total_outage"):
        return _inconclusive(container.name, proxy_url, regime, regime.detail)

    await emit("phase", {"phase": "load"})
    m = await _load_and_probe(proxy_url, emit)

    await emit("phase", {"phase": "longlived"})
    longlived = await _longlived(proxy_url, emit)

    await emit("phase", {"phase": "udp"})
    udp = await _udp_probe(container.dashboard.protocol, container.probe_address or "", emit)

    bulk_grade, bulk_reasons = _grade_bulk(m, longlived)
    call_grade, call_reasons = _grade_call(m)

    return StabilityResult(
        service=container.name,
        bulk_grade=bulk_grade,
        call_grade=call_grade,
        tested_via=proxy_url,
        regime=regime,
        streams=m["streams"],
        completed=m["completed"],
        resets=m["resets"],
        stalls=m["stalls"],
        reset_rate=m["reset_rate"],
        stall_rate=m["stall_rate"],
        idle_p50_ms=m["idle_p50_ms"],
        idle_p95_ms=m["idle_p95_ms"],
        loaded_p50_ms=m["loaded_p50_ms"],
        loaded_p95_ms=m["loaded_p95_ms"],
        loaded_max_ms=m["loaded_max_ms"],
        loaded_jitter_ms=m["loaded_jitter_ms"],
        loaded_loss_pct=m["loaded_loss_pct"],
        loaded_spike_pct=m["loaded_spike_pct"],
        loaded_samples=m["loaded_samples"],
        latency_inflation=m["latency_inflation"],
        longlived_held=longlived["held"],
        longlived_survived=longlived["survived"],
        longlived_min_ttl_s=longlived["min_ttl_s"],
        udp_supported=udp["udp_supported"],
        udp_detail=udp["udp_detail"],
        summary=_summary(m),
        reasons=bulk_reasons + call_reasons,
    )
