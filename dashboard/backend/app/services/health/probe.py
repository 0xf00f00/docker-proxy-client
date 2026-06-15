"""The probe I/O: reach the open internet through the active system proxy and, when
the path is down, ask the regime classifier *why*. Returns named results, not tuples."""

import time
from dataclasses import dataclass

import httpx

from app.services.connectivity_tests import regime as regime_mod
from app.services.health.cause import is_dns_failure
from app.services.health.settings import (
    DEGRADED_LATENCY_MS,
    PROBE_TIMEOUT,
    PROBE_URL,
    Status,
)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    reachable: bool
    latency_ms: float | None
    dns_ok: bool


@dataclass(frozen=True, slots=True)
class Classification:
    status: Status
    regime: str
    latency_ms: float | None
    reachable: bool
    dns_ok: bool


async def probe() -> ProbeResult:
    """Reach the open internet through the active system proxy (default route).
    The request must resolve the host first, so a resolution failure here *is* our
    DNS signal — no separate DNS probe needed. Any other error means the name
    resolved but the path didn't."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.get(PROBE_URL)
        elapsed = (time.monotonic() - start) * 1000
        return ProbeResult(resp.status_code == 204, round(elapsed, 1), True)
    except Exception as e:
        return ProbeResult(False, None, not is_dns_failure(e))


async def classify() -> Classification:
    """One combined probe folded into a status. A reachable path is good/degraded by
    latency; a down path defers to the regime classifier (pinned to the raw uplink)."""
    result = await probe()
    if result.reachable:
        status: Status = (
            "degraded" if (result.latency_ms is not None and result.latency_ms >= DEGRADED_LATENCY_MS) else "good"
        )
        # Got out fine — no need to bother the real Iran-resident anchors.
        return Classification(status, "normal", result.latency_ms, True, True)
    # Path is down: ask the regime classifier *why* (it pins to the raw uplink).
    try:
        info = await regime_mod.get_regime()
        regime = info.regime
    except Exception:
        regime = "unknown"
    return Classification("outage", regime, None, False, result.dns_ok)
