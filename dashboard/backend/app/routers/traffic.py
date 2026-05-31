"""Real-time traffic SSE.

One shared collector (see ``traffic_service``) fans the latest throughput
snapshot out to every connected client, so N open dashboards cost the same as
one. Read-only — no auth dependency, matching the other ``/stream`` endpoints.
"""

import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter

from app.services.traffic_service import collector
from app.sse import comment, event, sse_response

router = APIRouter(prefix="/traffic", tags=["traffic"])

# If no fresh snapshot arrives within this window, send a keep-alive comment so
# proxies/load-balancers don't drop the idle connection.
HEARTBEAT_SEC = 10.0


@router.get("/stream")
async def stream_traffic():
    """Stream a compact throughput snapshot ~once per second.

    Each ``traffic`` event carries::

        {"ts": <epoch>, "system": {"up": <B/s>, "down": <B/s>},
         "proxies": {"<container.name>": <B/s>, ...}}

    ``system`` is the TUN system-proxy total (from Clash); ``proxies`` is the
    dominant per-container throughput. Values are bytes/second.
    """

    async def gen() -> AsyncGenerator[str]:
        q = await collector.subscribe()
        try:
            snap = collector.latest()
            if snap is not None:
                yield event("traffic", snap)
            while True:
                try:
                    snap = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SEC)
                    yield event("traffic", snap)
                except TimeoutError:
                    yield comment()
        finally:
            await collector.unsubscribe(q)

    return sse_response(gen())
