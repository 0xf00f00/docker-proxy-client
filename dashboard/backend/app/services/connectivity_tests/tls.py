import asyncio
import socket
import ssl
import time

from app.models.schemas import ConnectivityResult, ContainerInfo
from app.services.connectivity_tests.registry import register

TIMEOUT = 15.0
# Sent as SNI to verify a handshake completes. SNI-spoofing proxies substitute
# their own fake SNI on the wire, so this value is only what the local TLS
# stack thinks it's connecting to.
SNI = "www.cloudflare.com"


def _tls_handshake(host: str, port: int, sni: str, timeout: float) -> tuple[bool, str | None]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as sock,
            ctx.wrap_socket(sock, server_hostname=sni or host) as ssock,
        ):
            ssock.do_handshake()
        return True, None
    except Exception as e:
        return False, str(e)


@register("tls", "https")
async def test(container: ContainerInfo) -> ConnectivityResult:
    host, _, port_str = (container.probe_address or "").rpartition(":")
    try:
        port = int(port_str)
    except ValueError:
        return ConnectivityResult(
            service=container.name,
            success=False,
            error="Invalid probe address",
            tested_via=container.probe_address or "n/a",
        )

    start = time.monotonic()
    ok, err = await asyncio.to_thread(_tls_handshake, host, port, SNI, TIMEOUT)
    elapsed = (time.monotonic() - start) * 1000
    return ConnectivityResult(
        service=container.name,
        success=ok,
        latency_ms=round(elapsed, 1),
        error=err,
        tested_via=f"tls://{container.probe_address}",
    )
