"""Real-time traffic SSE: one shared collector fans out to every client. Read-only, no auth."""

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
    """~1/s `traffic` events: {"ts", "system": {"up", "down"}, "proxies": {<container.name>: B/s}}."""

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
