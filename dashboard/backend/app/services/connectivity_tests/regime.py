"""Network-regime classifier (see docs/proxy-stability-detection.md §4a).

Distinguishes "this edge is dirty" from "the whole link is down" so a censorship
window isn't misread as a proxy fault. Regimes: normal (intl up) / iran_only
(intl blocked, Iran sites up) / total_outage / unknown.
"""

import asyncio
import os
import socket
import ssl
import time

import httpx

from app.models.schemas import RegimeInfo

INTL_HOST = os.environ.get("STABILITY_INTL_ANCHOR", "www.google.com")
IR_HOSTS = [
    h.strip()
    for h in os.environ.get("STABILITY_IR_ANCHORS", "digikala.com,snapp.ir").split(",")
    if h.strip()
]
ANCHOR_PORT = 443
ANCHOR_TIMEOUT = 5.0
DIRECT_GOODPUT_URL = os.environ.get("STABILITY_DIRECT_GOODPUT_URL", "https://www.gstatic.com/generate_204")
DIRECT_GOODPUT_BYTES = int(os.environ.get("STABILITY_DIRECT_GOODPUT_BYTES", str(3_000_000)))
DIRECT_GOODPUT_TIMEOUT = 20.0

# Cache the regime so a batch of per-proxy tests resolves the link once, and the
# real Iran-resident anchor sites aren't hit repeatedly.
CACHE_TTL_S = 300.0

_cache: tuple[float, RegimeInfo] | None = None
_cache_lock = asyncio.Lock()
_intl_lkg_ip: str | None = None


def _resolve(host: str) -> str | None:
    try:
        return socket.getaddrinfo(host, ANCHOR_PORT, proto=socket.IPPROTO_TCP)[0][4][0]
    except (socket.gaierror, OSError, IndexError):
        return None


def _tls_reachable(host: str, ip: str, timeout: float) -> bool:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with (
            socket.create_connection((ip, ANCHOR_PORT), timeout=timeout) as sock,
            ctx.wrap_socket(sock, server_hostname=host) as ssock,
        ):
            ssock.do_handshake()
        return True
    except (OSError, ssl.SSLError):
        return False


def _probe_intl_sync() -> tuple[bool, bool]:
    """Returns (reachable, dns_ok). Resolves Google by name and caches a
    last-known-good IP; on resolution failure retries against the cache, so a
    dead (international) resolver doesn't masquerade as "Google is down"."""
    global _intl_lkg_ip
    ip = _resolve(INTL_HOST)
    dns_ok = ip is not None
    if ip is None:
        ip = _intl_lkg_ip
        if ip is None:
            return False, False
    reachable = _tls_reachable(INTL_HOST, ip, ANCHOR_TIMEOUT)
    if reachable and dns_ok:
        _intl_lkg_ip = ip
    return reachable, dns_ok


def _probe_ir_sync() -> bool:
    for host in IR_HOSTS:
        ip = _resolve(host)
        if ip and _tls_reachable(host, ip, ANCHOR_TIMEOUT):
            return True
    return False


async def _measure_direct_goodput() -> float | None:
    """Direct (un-proxied) throughput in MB/s — the §3 baseline, only meaningful
    when international is up."""
    start = time.monotonic()
    total = 0
    try:
        async with (
            httpx.AsyncClient(timeout=DIRECT_GOODPUT_TIMEOUT) as client,
            client.stream("GET", DIRECT_GOODPUT_URL) as resp,
        ):
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total >= DIRECT_GOODPUT_BYTES:
                    break
    except Exception:
        return None
    elapsed = time.monotonic() - start
    if elapsed <= 0 or total == 0:
        return None
    return round((total / 1_000_000) / elapsed, 2)


async def _classify() -> RegimeInfo:
    intl_reachable, dns_ok = await asyncio.to_thread(_probe_intl_sync)

    if intl_reachable:
        goodput = await _measure_direct_goodput()
        return RegimeInfo(
            regime="normal",
            intl_up=True,
            direct_goodput_mbps=goodput,
            detail="International reachable (Google OK).",
        )

    # Only touch the real Iran-resident sites once international already looks down.
    if await asyncio.to_thread(_probe_ir_sync):
        why = "Iran-only mode: international blocked, Iran sites reachable."
        if not dns_ok:
            why += " International DNS is also failing (corroborates Iran-only)."
        return RegimeInfo(regime="iran_only", intl_up=False, detail=why)

    return RegimeInfo(
        regime="total_outage",
        intl_up=False,
        detail="Nothing reachable — international and Iran-resident sites both down.",
    )


async def get_regime(force: bool = False) -> RegimeInfo:
    global _cache
    now = time.monotonic()
    async with _cache_lock:
        if not force and _cache is not None and now - _cache[0] < CACHE_TTL_S:
            return _cache[1]
        info = await _classify()
        _cache = (time.monotonic(), info)
        return info
