"""Pin a probe to the raw uplink NIC, bypassing Clash's TUN.

The dashboard runs network_mode:host, where the default route is the Clash TUN, so
any probe to an internet host silently egresses THROUGH a proxy. Dashboard tests
meant to measure the real internet (regime/baseline/system connectivity) must use
these helpers; tests that intentionally exercise a proxy or the system proxy must
not. The interface is the existing `host_lan_interface` setting (default eth0).
"""

import contextlib
import socket

import httpx

from app.config import settings

# SO_BINDTODEVICE is Linux-only and absent from the socket module on macOS.
_SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", 25)


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
