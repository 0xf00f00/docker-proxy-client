"""Proxy stability probe (see docs/realtime-stability-repro.md).

A plain connectivity test (one request, mean latency) reports "stable" on
proxies that are visibly broken in real use, because it never creates the two
conditions that actually break under DPI: connection *concurrency* (what a docker
pull does) and *sustained load* (what makes a Google Meet call choppy). This
creates both and grades them separately:

  Bulk  — under many parallel streams, the reset/stall rate.
  Call  — latency spikes/jitter *while the tunnel is saturated*, which is what
          governs real-time media quality; average throughput does not.

Quota: users pay for metered data, so this is kept light. The N streams that
create the load are each byte-capped, and the latency probes ride *that same*
load (no separate saturation phase) — so a run moves ~CONCURRENCY x
STREAM_CAP_BYTES (DATA_BUDGET_MB), not the hundreds of MB a time-capped soak
would; a DPI reset/stall shows early in a flow, so the cap doesn't hide it.

WARNING: it briefly saturates the tunnel, degrading any live user for its
duration — on-demand / quiet-window only, never an unattended loop.
"""

import asyncio
import statistics
import time
from collections.abc import Awaitable, Callable

import httpx

from app.models.schemas import ContainerInfo, RegimeInfo, StabilityResult
from app.services.connectivity_tests import regime as regime_mod

EventCb = Callable[[str, dict], Awaitable[None]]

LATENCY_URL = "http://www.gstatic.com/generate_204"
STREAM_CAP_BYTES = 3_000_000
LOAD_URL = f"https://speed.cloudflare.com/__down?bytes={STREAM_CAP_BYTES}"

CONCURRENCY = 10  # enough parallel streams to trip concurrency-triggered DPI
STREAM_TIMEOUT = 45.0
STALL_S = 5.0  # no bytes for this long = stalled
MIN_BYTES = 2_000_000  # a stream must move this much to count as completed
IDLE_PROBES = 10
LATENCY_PROBE_TIMEOUT = 8.0

# Long-lived survival: small periodic requests over a held connection — trivial
# data, models a call with talk gaps (DPI often resets long/idle sessions). The
# hold still has to outlast typical idle-reset timers, but 45s does that while
# halving the user's wait; the duration is sent once so the client counts down.
LONGLIVED_COUNT = 2
LONGLIVED_HOLD_S = 45.0
LONGLIVED_TRICKLE_S = 15.0

# Worst-case data a run moves (probes are ~0-byte 204s; trickle is negligible).
DATA_BUDGET_MB = round(CONCURRENCY * STREAM_CAP_BYTES / 1_000_000)

# Grading. Live-calibrated 2026-05-31: under load p50/p95 stayed ~200ms but max
# hit 2-3s — calls freeze on those tail spikes, so we grade on max-inflation and
# the fraction of probes over SPIKE_THRESHOLD, not p95.
SPIKE_THRESHOLD_MS = 1000.0  # an RTT spike past this freezes a Meet call
BULK_BAD_RATE = 0.10
BULK_DEGRADED_RATE = 0.03
CALL_BAD_INFLATION = 8.0
CALL_DEGRADED_INFLATION = 4.0
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
    except (httpx.ReadTimeout, httpx.ReadError):
        return "stalled" if (time.monotonic() - last_byte) >= STALL_S else "reset"
    except Exception:
        return "reset"


async def _latency_probe(proxy_url: str) -> float | None:
    start = time.monotonic()
    try:
        limits = httpx.Limits(max_keepalive_connections=0, max_connections=1)
        async with httpx.AsyncClient(proxy=proxy_url, timeout=LATENCY_PROBE_TIMEOUT, limits=limits) as client:
            resp = await client.get(LATENCY_URL)
            return (time.monotonic() - start) * 1000 if resp.status_code == 204 else None
    except Exception:
        return None


async def _load_and_probe(proxy_url: str, emit: EventCb) -> dict:
    """One saturation event serving both signals: CONCURRENCY byte-capped streams
    are the load (→ reset/stall counts) while latency is probed *during* them (→
    loaded spikes/jitter/loss). Combining them is what keeps data to DATA_BUDGET_MB
    instead of running soak and a separate saturation phase back to back."""
    await emit("progress", {"phase": "idle"})
    idle = [ms for _ in range(IDLE_PROBES) if (ms := await _latency_probe(proxy_url)) is not None]
    idle_p50 = _median(idle)

    await emit("progress", {"phase": "load"})
    streams = [asyncio.create_task(_one_stream(proxy_url)) for _ in range(CONCURRENCY)]

    loaded: list[float] = []
    loss = 0
    deadline = time.monotonic() + STREAM_TIMEOUT
    await asyncio.sleep(1.0)  # let the load ramp before the first loaded probe
    while not all(t.done() for t in streams) and time.monotonic() < deadline:
        ms = await _latency_probe(proxy_url)
        if ms is None:
            loss += 1
        else:
            loaded.append(ms)

    counts = {"completed": 0, "reset": 0, "stalled": 0}
    for o in await asyncio.gather(*streams):
        counts[o] += 1

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
        "idle_p50_ms": idle_p50,
        "loaded_p50_ms": _median(loaded),
        "loaded_max_ms": round(loaded_max, 1) if loaded_max else None,
        "loaded_jitter_ms": _jitter(loaded),
        "loaded_loss_pct": round(100 * loss / probed, 1) if probed else None,
        "loaded_spike_pct": round(100 * spike / len(loaded), 1) if loaded else None,
        "latency_inflation": round(loaded_max / idle_p50, 1) if (loaded_max and idle_p50) else None,
    }


async def _hold_one(proxy_url: str) -> float:
    """Hold a connection open with periodic small requests; return seconds it
    survived (== HOLD if it never reset)."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=LATENCY_PROBE_TIMEOUT) as client:
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
    reasons: list[str] = []
    infl, spike, jit, loss = (
        m["latency_inflation"], m["loaded_spike_pct"], m["loaded_jitter_ms"], m["loaded_loss_pct"],
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

    if (over(infl, CALL_BAD_INFLATION) or over(spike, CALL_BAD_SPIKE_PCT)
            or over(jit, CALL_BAD_JITTER_MS) or over(loss, CALL_BAD_LOSS)):
        return "bad", reasons
    if (over(infl, CALL_DEGRADED_INFLATION) or over(spike, CALL_DEGRADED_SPIKE_PCT)
            or over(jit, CALL_DEGRADED_JITTER_MS) or over(loss, CALL_DEGRADED_LOSS)):
        return "degraded", reasons
    return "good", reasons


def _summary(m: dict) -> str:
    parts = [f"{m['completed']}/{m['streams']} downloads OK"]
    if m.get("loaded_max_ms") is not None:
        parts.append(f"up to {int(m['loaded_max_ms'])}ms under load")
    if m.get("loaded_spike_pct"):
        parts.append(f"{m['loaded_spike_pct']}% froze")
    return " · ".join(parts)


def _inconclusive(service: str, tested_via: str, regime: RegimeInfo, detail: str,
                  error: str | None = None) -> StabilityResult:
    return StabilityResult(
        service=service, bulk_grade="inconclusive", call_grade="inconclusive",
        tested_via=tested_via, regime=regime, summary=detail, reasons=[detail], error=error,
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
        loaded_p50_ms=m["loaded_p50_ms"],
        loaded_max_ms=m["loaded_max_ms"],
        loaded_jitter_ms=m["loaded_jitter_ms"],
        loaded_loss_pct=m["loaded_loss_pct"],
        loaded_spike_pct=m["loaded_spike_pct"],
        latency_inflation=m["latency_inflation"],
        longlived_held=longlived["held"],
        longlived_survived=longlived["survived"],
        longlived_min_ttl_s=longlived["min_ttl_s"],
        summary=_summary(m),
        reasons=bulk_reasons + call_reasons,
    )
