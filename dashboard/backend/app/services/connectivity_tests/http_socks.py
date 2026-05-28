import time

import httpx

from app.models.schemas import ConnectivityResult, ContainerInfo
from app.services import ip_lookup
from app.services.connectivity_tests.registry import register

TEST_URL = "http://www.gstatic.com/generate_204"
TIMEOUT = 15.0


def _build_proxy_url(protocol: str, address: str) -> str | None:
    proto = protocol.split("+")[0]
    if proto in ("socks5", "socks"):
        return f"socks5://{address}"
    if proto in ("http", "mixed"):
        return f"http://{address}"
    return None


@register("socks5", "socks", "http", "mixed")
async def test(container: ContainerInfo) -> ConnectivityResult:
    proxy_url = _build_proxy_url(container.dashboard.protocol, container.probe_address or "")
    if not proxy_url:
        return ConnectivityResult(
            service=container.name,
            success=False,
            error=f"Unsupported protocol: {container.dashboard.protocol}",
            tested_via="n/a",
        )

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=TIMEOUT) as client:
            resp = await client.get(TEST_URL)
            elapsed = (time.monotonic() - start) * 1000
            success = resp.status_code == 204
            # Only fetch IP info on a successful probe — and serially after,
            # not concurrently, so we don't double the load on a thin uplink
            # while the latency measurement is in flight.
            info = await ip_lookup.lookup(proxy_url) if success else None
            return ConnectivityResult(
                service=container.name,
                success=success,
                latency_ms=round(elapsed, 1),
                status_code=resp.status_code,
                tested_via=proxy_url,
                ip_info=info,
            )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return ConnectivityResult(
            service=container.name,
            success=False,
            latency_ms=round(elapsed, 1),
            error=str(e),
            tested_via=proxy_url,
        )
