"""Goodput: one small exact-size download, the only way to tell a slow path from an app-limited flow."""

import asyncio
import time

import httpx

from app.services.health.probes import ProbeResult
from app.services.health.settings import SPEED_HOST as HOST

BYTES = 524_288
URL = f"https://{HOST}/__down?bytes={BYTES}"
TIMEOUT_S = 20.0
_HANDSHAKE_WIRE = 6000
ESTIMATE = round((BYTES + _HANDSHAKE_WIRE) * 1.3)


async def run(url: str = URL, expected: int = BYTES, timeout: float = TIMEOUT_S) -> ProbeResult:
    start = time.monotonic()
    got, first = 0, None
    try:
        # a whole-run deadline: httpx's timeout is per read, so a trickle would never trip it
        async with (
            asyncio.timeout(timeout),
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream("GET", url) as resp,
        ):
            async for chunk in resp.aiter_bytes():
                first = first if first is not None else time.monotonic()
                got += len(chunk)
            ok = resp.status_code == 200 and got == expected
    except Exception:
        ok = False
    end = time.monotonic()
    body_s = end - first if first is not None else None
    data = {
        "bytes": got,
        "ttfb": round((first - start) * 1000) if first is not None else None,
        "ms": round((end - start) * 1000),
        "mbit": round(got * 8 / 1e6 / body_s, 2) if body_s and body_s > 0 else None,
    }
    return ProbeResult(ok, round((got + _HANDSHAKE_WIRE) * 1.3), data)
