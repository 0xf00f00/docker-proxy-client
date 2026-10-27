"""Pin a probe to the raw uplink NIC. Under network_mode:host the default route is the Clash TUN, so an
unpinned probe silently egresses through a proxy; anything measuring the real internet must use these."""

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
