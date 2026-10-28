"""Network-regime classifier: tells "this edge is dirty" from "the whole link is down" so an outage isn't misread as
a proxy fault. Regimes: normal / iran_only (intl blocked, Iran sites up) / total_outage (router reachable, nothing
beyond it) / local_network (router unreachable) / unknown."""

import asyncio
import socket
import ssl
import time

from app.models.schemas import RegimeInfo
from app.services import direct_net

# Independent operators, so one name being filtered or one handshake lost can't read as "the world is down".
INTL_HOSTS = ["www.google.com", "www.cloudflare.com", "www.microsoft.com"]
IR_HOSTS = ["digikala.com", "snapp.ir"]
ANCHOR_PORT = 443
ANCHOR_TIMEOUT = 5.0
CONFIRM_DELAY_S = 1.0

# Short enough that an outage is attributed to its real cause, long enough that the Iran-resident anchors
# aren't hit on every call.
CACHE_TTL_S = 60.0

_cache: tuple[float, RegimeInfo] | None = None
_cache_lock = asyncio.Lock()
_lkg_ips: dict[str, str] = {}


def _resolve(host: str) -> str | None:
    try:
        return str(socket.getaddrinfo(host, ANCHOR_PORT, proto=socket.IPPROTO_TCP)[0][4][0])
    except (socket.gaierror, OSError, IndexError):
        return None


def _tls_reachable(host: str, ip: str, timeout: float) -> bool:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    direct_net.bind_to_uplink(sock)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, ANCHOR_PORT))
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            ssock.do_handshake()
        return True
    except (OSError, ssl.SSLError):
        return False
    finally:
        sock.close()


def _probe_host_sync(host: str) -> tuple[bool, bool]:
    """Returns (reachable, dns_ok). Resolves the host by name and caches a last-known-good IP; on resolution
    failure retries against the cache, so a dead (international) resolver doesn't masquerade as "host is down"."""
    ip = _resolve(host)
    dns_ok = ip is not None
    if ip is None:
        ip = _lkg_ips.get(host)
        if ip is None:
            return False, False
    reachable = _tls_reachable(host, ip, ANCHOR_TIMEOUT)
    if reachable and dns_ok:
        _lkg_ips[host] = ip
    return reachable, dns_ok


async def _probe_intl() -> tuple[bool, bool]:
    """(reachable, dns_ok): any anchor answering means the outside world is reachable. A failure is re-checked
    once before it counts, so a single lost handshake can't flip the verdict."""
    dns_ok = False
    for attempt in range(2):
        results = await asyncio.gather(*(asyncio.to_thread(_probe_host_sync, h) for h in INTL_HOSTS))
        dns_ok = dns_ok or any(d for _, d in results)
        if any(r for r, _ in results):
            return True, dns_ok
        if attempt == 0:
            await asyncio.sleep(CONFIRM_DELAY_S)
    return False, dns_ok


def _probe_ir_sync() -> bool:
    for host in IR_HOSTS:
        ip = _resolve(host)
        if ip and _tls_reachable(host, ip, ANCHOR_TIMEOUT):
            return True
    return False


async def _gateway_reachable() -> bool | None:
    reachable = await direct_net.gateway_reachable()
    if reachable is False:
        await asyncio.sleep(CONFIRM_DELAY_S)
        reachable = await direct_net.gateway_reachable()
    return reachable


async def _classify() -> RegimeInfo:
    if await _gateway_reachable() is False:
        return RegimeInfo(
            regime="local_network",
            intl_up=False,
            detail="Local network down: this device can't reach its router, so nothing else was tested.",
        )

    intl_reachable, dns_ok = await _probe_intl()

    if intl_reachable:
        return RegimeInfo(regime="normal", intl_up=True, detail="International reachable.")

    # Only touch the real Iran-resident sites once international already looks down.
    if await asyncio.to_thread(_probe_ir_sync):
        why = "International blocked: foreign sites unreachable, Iran sites reachable."
        if not dns_ok:
            why += " International DNS is also failing (corroborates the block)."
        return RegimeInfo(regime="iran_only", intl_up=False, detail=why)

    return RegimeInfo(
        regime="total_outage",
        intl_up=False,
        detail="Internet down: the router answers but neither foreign nor Iran-resident sites do.",
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
