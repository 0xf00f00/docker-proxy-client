"""Pin a probe to the raw uplink NIC. Under network_mode:host the default route is the Clash TUN, so an
unpinned probe silently egresses through a proxy; anything measuring the real internet must use these."""

import asyncio
import contextlib
import socket
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from app.config import settings

# SO_BINDTODEVICE is Linux-only and absent from the socket module on macOS.
_SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", 25)

ROUTE_TABLE = "/proc/net/route"
ARP_TABLE = "/proc/net/arp"
SYS_NET = "/sys/class/net"
GATEWAY_PORTS = (80, 443, 53, 22)
GATEWAY_TIMEOUT_S = 2.0
_RTF_GATEWAY = 0x2
_ATF_COMPLETE = 0x2


def bind_to_uplink(sock: socket.socket) -> None:
    iface = settings.host_lan_interface
    if not iface:
        return
    # Best-effort: where binding can't apply (no iface, not permitted, non-Linux)
    # the default route is already the real uplink.
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, _SO_BINDTODEVICE, iface.encode() + b"\0")


def socket_options() -> list[tuple[int, int, bytes]] | None:
    iface = settings.host_lan_interface
    if not iface:
        return None
    return [(socket.SOL_SOCKET, _SO_BINDTODEVICE, iface.encode() + b"\0")]


def async_client(**kwargs: object) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(socket_options=socket_options())
    return httpx.AsyncClient(transport=transport, **kwargs)


def default_gateway(route_table: str) -> str | None:
    """The uplink's IPv4 default gateway from /proc/net/route text; None when it has no default route."""
    iface = settings.host_lan_interface
    for row in route_table.splitlines()[1:]:
        f = row.split()
        if len(f) >= 4 and f[1] == "00000000" and int(f[3], 16) & _RTF_GATEWAY and (not iface or f[0] == iface):
            return socket.inet_ntoa(struct.pack("<I", int(f[2], 16)))
    return None


def _answers(gateway: str, port: int) -> bool:
    """A completed handshake or a RST both prove the router is alive; silence proves nothing."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    bind_to_uplink(sock)
    sock.settimeout(GATEWAY_TIMEOUT_S)
    try:
        sock.connect((gateway, port))
        return True
    except (ConnectionRefusedError, ConnectionResetError):
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _in_arp_table(gateway: str) -> bool:
    """A completed ARP entry means the router answered at layer 2 even if it ignores every port we tried."""
    try:
        rows = Path(ARP_TABLE).read_text().splitlines()[1:]
    except OSError:
        return False
    return any(len(f) >= 3 and f[0] == gateway and int(f[2], 16) & _ATF_COMPLETE for f in map(str.split, rows))


def _gateway_reachable_sync() -> bool | None:
    try:
        table = Path(ROUTE_TABLE).read_text()
    except OSError:
        return None
    gateway = default_gateway(table)
    if gateway is None:
        # A configured NIC that exists but has no way out is a real failure; one that doesn't exist is a misconfig.
        iface = settings.host_lan_interface
        return False if iface and Path(SYS_NET, iface).exists() else None
    with ThreadPoolExecutor(len(GATEWAY_PORTS)) as pool:
        if any(pool.map(lambda port: _answers(gateway, port), GATEWAY_PORTS)):
            return True
    return _in_arp_table(gateway)


async def gateway_reachable() -> bool | None:
    """Whether this host can reach its own router; None when that can't be determined (no routing table)."""
    return await asyncio.to_thread(_gateway_reachable_sync)
