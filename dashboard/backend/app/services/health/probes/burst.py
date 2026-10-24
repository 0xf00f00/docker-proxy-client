"""Handshake burst: several new HTTPS connections at once, to confirm DPI dropping fresh handshakes."""

import asyncio
import time

import httpx

from app.services.health.probes import ProbeResult, percentile

URL = "https://speed.cloudflare.com/__down?bytes=0"
HOST = "speed.cloudflare.com"
COUNT = 8
TIMEOUT_S = 10.0
MAX_FAILED = 1
_CONNECTION_WIRE = 6000
ESTIMATE = round(COUNT * _CONNECTION_WIRE * 1.3)


async def _one(url: str, timeout: float) -> float | None:
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"Connection": "close"})
        return (time.monotonic() - start) * 1000 if resp.status_code < 500 else None
    except Exception:
        return None


async def run(count: int = COUNT, url: str = URL, timeout: float = TIMEOUT_S) -> ProbeResult:
    times = await asyncio.gather(*(_one(url, timeout) for _ in range(count)))
    done = [t for t in times if t is not None]
    p50 = percentile(done, 50)
    data = {
        "n": count,
        "failed": count - len(done),
        "p50": round(p50) if p50 is not None else None,
        "max": round(max(done)) if done else None,
    }
    return ProbeResult(data["failed"] <= MAX_FAILED, round(count * _CONNECTION_WIRE * 1.3), data)
