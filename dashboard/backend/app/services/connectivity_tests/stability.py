"""Active proxy-stability probe (see docs/proxy-stability-detection.md §3-§4).

Unlike the plain connectivity test (3 tries, pass if any, mean latency — tuned
to *not* fail a flaky link), this samples the distribution and grades its shape,
catching a "dirty" edge that resets/throttles while still looking fast. Grading
is regime-gated: during an Iran-only blackout/outage a dead link is
indistinguishable from a dirty edge, so the grade is "inconclusive".
"""

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable
from itertools import pairwise

import httpx

from app.models.schemas import ContainerInfo, RegimeInfo, StabilityResult
from app.services.connectivity_tests import regime as regime_mod

EventCb = Callable[[str, dict], Awaitable[None]]
ProgressCb = Callable[[int, dict], Awaitable[None]]
SampleCb = Callable[[int, int], Awaitable[None]]

RELIABILITY_URL = "http://www.gstatic.com/generate_204"
# Exact-byte download riding the same CF edges the proxies exit through.
GOODPUT_BYTES = 3_000_000
GOODPUT_URL = f"https://speed.cloudflare.com/__down?bytes={GOODPUT_BYTES}"

ATTEMPTS = 20
WINDOW_S = 20.0  # spread attempts over time; DPI throttling is often rate-based
MAX_CONCURRENCY = 2  # never burst a thin uplink (would self-inflict timeouts)
ATTEMPT_TIMEOUT = 12.0
GOODPUT_TIMEOUT = 25.0
GOODPUT_SAMPLE_S = 0.5
GOODPUT_STALL_S = 3.0  # no-bytes gap that counts as a stall

# Calibrated so 5/20 failures (Wilson-low ≈ 0.12) grades "bad" while 1-2/20 noise
# stays good/degraded. See docs §4 / §8.
BAD_FAILURE_LOWER = 0.10
DEGRADED_FAILURE_LOWER = 0.03
# Resets trip "bad" at a lower threshold: a reset is the DPI actively tearing the
# connection down, vs. a timeout the slow uplink could explain.
BAD_RESET_LOWER = 0.06
DIRECT_RATIO_BAD = 0.4  # P/gstatic below this (intl up) ⇒ throttled
P95_JITTER_BAD_MS = 4000.0


def _build_proxy_url(protocol: str, address: str) -> str | None:
    proto = protocol.split("+")[0]
    if proto in ("socks5", "socks"):
        return f"socks5://{address}"
    if proto in ("http", "mixed"):
        return f"http://{address}"
    return None


def _wilson_lower_bound(failures: int, n: int, z: float = 1.2816) -> float:
    """Lower bound (z≈1.2816 ⇒ ~90%) of the failure rate — gating on it stops a
    small noisy sample from over-triggering."""
    if n == 0:
        return 0.0
    p = failures / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = pct / 100 * (len(sorted_vals) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _classify_error(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectTimeout | httpx.ReadTimeout | httpx.PoolTimeout | asyncio.TimeoutError):
        return "timeout"
    text = str(exc).lower()
    if "reset" in text or "broken pipe" in text or "econnreset" in text:
        return "reset"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return "other"


async def _one_attempt(proxy_url: str, delay: float) -> tuple[str, float | None]:
    await asyncio.sleep(delay)
    # Zero keepalive ⇒ a brand-new TCP+TLS setup each time, which is where the
    # throttle bites; a reused connection would hide it.
    limits = httpx.Limits(max_keepalive_connections=0, max_connections=1)
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=ATTEMPT_TIMEOUT, limits=limits) as client:
            resp = await client.get(RELIABILITY_URL)
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code == 204:
                return "ok", round(elapsed, 1)
            return "other", round(elapsed, 1)
    except Exception as exc:
        return _classify_error(exc), None


async def _reliability(
    proxy_url: str,
    on_attempt: ProgressCb | None = None,
) -> tuple[dict[str, int], list[float]]:
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    step = WINDOW_S / max(1, ATTEMPTS)
    counts = {"ok": 0, "reset": 0, "timeout": 0, "other": 0}
    latencies: list[float] = []
    lock = asyncio.Lock()
    done = 0

    async def run(i: int) -> None:
        nonlocal done
        async with sem:
            jitter = random.uniform(0, step)
            outcome, ms = await _one_attempt(proxy_url, i * step + jitter)
        async with lock:
            counts[outcome] = counts.get(outcome, 0) + 1
            if outcome == "ok" and ms is not None:
                latencies.append(ms)
            done += 1
            if on_attempt is not None:
                await on_attempt(done, dict(counts))

    await asyncio.gather(*(run(i) for i in range(ATTEMPTS)))
    return counts, latencies


async def _goodput(proxy_url: str, on_sample: SampleCb | None = None) -> dict:
    """Sized download, sampled over time → mean/peak MB/s, stall, decay, and
    completion (mid-stream survival)."""
    out = {
        "mbps": None,
        "peak_mbps": None,
        "completed": False,
        "stalled": False,
        "decayed": False,
    }
    samples: list[tuple[float, int]] = []
    total = 0
    start = time.monotonic()
    last_byte_t = start
    last_sample_t = start
    try:
        async with (
            httpx.AsyncClient(proxy=proxy_url, timeout=GOODPUT_TIMEOUT) as client,
            client.stream("GET", GOODPUT_URL) as resp,
        ):
            if resp.status_code != 200:
                return out
            async for chunk in resp.aiter_bytes():
                now = time.monotonic()
                if chunk:
                    total += len(chunk)
                    last_byte_t = now
                if now - last_sample_t >= GOODPUT_SAMPLE_S:
                    samples.append((now - start, total))
                    last_sample_t = now
                    if on_sample is not None:
                        await on_sample(total, GOODPUT_BYTES)
                if now - last_byte_t >= GOODPUT_STALL_S:
                    out["stalled"] = True
                    break
                if total >= GOODPUT_BYTES:
                    out["completed"] = True
                    break
    except Exception:
        # A mid-stream reset / timeout is a survival failure, not a hard error.
        out["completed"] = False

    elapsed = time.monotonic() - start
    if elapsed > 0 and total > 0:
        out["mbps"] = round((total / 1_000_000) / elapsed, 2)

    samples.append((elapsed, total))
    out["peak_mbps"] = _peak_rate(samples)
    out["decayed"] = _is_decayed(samples)
    return out


def _peak_rate(samples: list[tuple[float, int]]) -> float | None:
    peak = 0.0
    for (t0, b0), (t1, b1) in pairwise(samples):
        dt = t1 - t0
        if dt > 0:
            peak = max(peak, ((b1 - b0) / 1_000_000) / dt)
    return round(peak, 2) if peak > 0 else None


def _is_decayed(samples: list[tuple[float, int]]) -> bool:
    """Last-third throughput < half the first-third — the "throttle kicks in
    after a few hundred KB" signature."""
    if len(samples) < 6:
        return False
    third = len(samples) // 3
    first = samples[: third + 1]
    last = samples[-third - 1 :]

    def rate(seg: list[tuple[float, int]]) -> float:
        dt = seg[-1][0] - seg[0][0]
        db = seg[-1][1] - seg[0][1]
        return (db / dt) if dt > 0 else 0.0

    r_first = rate(first)
    r_last = rate(last)
    return r_first > 0 and r_last < 0.5 * r_first


def _grade(
    counts: dict[str, int],
    failure_lower: float,
    goodput: dict,
    direct_ratio: float | None,
    p95: float | None,
    regime: RegimeInfo,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    bad = False
    degraded = False

    n = sum(counts.values())
    if failure_lower > BAD_FAILURE_LOWER:
        bad = True
        failed = counts["reset"] + counts["timeout"] + counts["other"]
        reasons.append(f"{failed}/{n} connections failed")
    elif failure_lower > DEGRADED_FAILURE_LOWER:
        degraded = True
        reasons.append("some connections failed")

    if counts.get("reset", 0) > 0:
        reset_lower = _wilson_lower_bound(counts["reset"], n)
        if reset_lower > BAD_RESET_LOWER:
            bad = True
        reasons.append(f"{counts['reset']} reset by DPI")

    if goodput.get("mbps") is not None and not goodput.get("completed"):
        bad = True
        reasons.append("download dropped mid-transfer")
    if goodput.get("stalled"):
        bad = True
        reasons.append("transfer stalled")
    if goodput.get("decayed"):
        degraded = True
        reasons.append("throughput decayed mid-transfer")

    if direct_ratio is not None and direct_ratio < DIRECT_RATIO_BAD:
        bad = True
        reasons.append(f"only {int(direct_ratio * 100)}% of direct speed")

    if p95 is not None and p95 > P95_JITTER_BAD_MS:
        degraded = True
        reasons.append(f"high latency tail (p95 {int(p95)}ms)")

    if bad:
        return "bad", reasons
    if degraded:
        return "degraded", reasons
    return "good", reasons


def _summary(result_counts: dict[str, int], goodput: dict, p95: float | None, direct_ratio: float | None) -> str:
    n = sum(result_counts.values())
    parts = [f"{result_counts['ok']}/{n} OK"]
    if result_counts.get("reset"):
        parts.append(f"{result_counts['reset']} resets")
    if goodput.get("mbps") is not None:
        s = f"{goodput['mbps']} MB/s"
        if direct_ratio is not None:
            s += f" ({int(direct_ratio * 100)}% of direct)"
        parts.append(s)
    if p95 is not None:
        parts.append(f"p95 {int(p95)}ms")
    return " · ".join(parts)


def _inconclusive(service: str, proxy_url: str, regime: RegimeInfo) -> StabilityResult:
    return StabilityResult(
        service=service,
        grade="inconclusive",
        tested_via=proxy_url,
        regime=regime,
        attempts=0,
        ok=0,
        resets=0,
        timeouts=0,
        other_errors=0,
        failure_rate=0.0,
        failure_rate_lower=0.0,
        summary=regime.detail,
        reasons=[regime.detail],
    )


async def test_stability(container: ContainerInfo, on_event: EventCb | None = None) -> StabilityResult:
    async def emit(name: str, data: dict) -> None:
        if on_event is not None:
            await on_event(name, data)

    proxy_url = _build_proxy_url(container.dashboard.protocol, container.probe_address or "")

    await emit("phase", {"phase": "regime"})
    regime = await regime_mod.get_regime()
    await emit("regime", regime.model_dump(mode="json"))

    if proxy_url is None:
        return StabilityResult(
            service=container.name,
            grade="inconclusive",
            tested_via="n/a",
            regime=regime,
            attempts=0, ok=0, resets=0, timeouts=0, other_errors=0,
            failure_rate=0.0, failure_rate_lower=0.0,
            error=f"Unsupported protocol: {container.dashboard.protocol}",
        )

    # Blackout/outage: every edge fails for the same reason (the link), so a
    # fault can't be attributed to this proxy.
    if regime.regime in ("iran_only", "total_outage"):
        return _inconclusive(container.name, proxy_url, regime)

    async def on_attempt(done: int, counts: dict) -> None:
        await emit("progress", {
            "phase": "connecting", "done": done, "total": ATTEMPTS,
            "ok": counts["ok"], "resets": counts["reset"], "timeouts": counts["timeout"],
        })

    await emit("phase", {"phase": "connecting"})
    counts, latencies = await _reliability(proxy_url, on_attempt=on_attempt)
    n = sum(counts.values())
    failures = n - counts["ok"]
    failure_rate = round(failures / n, 3) if n else 0.0
    failure_lower = round(_wilson_lower_bound(failures, n), 3)

    latencies.sort()
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)

    # Skip goodput when no connection worked — it'd just waste the thin uplink.
    goodput = {
        "mbps": None, "peak_mbps": None, "completed": False, "stalled": False, "decayed": False,
    }
    if counts["ok"] > 0:
        async def on_sample(downloaded: int, target: int) -> None:
            await emit("progress", {
                "phase": "speed", "done": ATTEMPTS, "total": ATTEMPTS,
                "ok": counts["ok"], "resets": counts["reset"], "timeouts": counts["timeout"],
                "downloaded": downloaded, "download_target": target,
            })

        await emit("phase", {"phase": "speed"})
        goodput = await _goodput(proxy_url, on_sample=on_sample)

    direct_ratio = None
    if regime.intl_up and regime.direct_goodput_mbps and goodput.get("mbps"):
        direct_ratio = round(goodput["mbps"] / regime.direct_goodput_mbps, 2)

    grade, reasons = _grade(counts, failure_lower, goodput, direct_ratio, p95, regime)
    summary = _summary(counts, goodput, p95, direct_ratio)

    return StabilityResult(
        service=container.name,
        grade=grade,
        tested_via=proxy_url,
        regime=regime,
        attempts=n,
        ok=counts["ok"],
        resets=counts["reset"],
        timeouts=counts["timeout"],
        other_errors=counts["other"],
        failure_rate=failure_rate,
        failure_rate_lower=failure_lower,
        latency_p50_ms=round(p50, 1) if p50 is not None else None,
        latency_p95_ms=round(p95, 1) if p95 is not None else None,
        goodput_mbps=goodput["mbps"],
        goodput_peak_mbps=goodput["peak_mbps"],
        goodput_completed=goodput["completed"],
        stalled=goodput["stalled"],
        decayed=goodput["decayed"],
        direct_ratio=direct_ratio,
        summary=summary,
        reasons=reasons,
    )
