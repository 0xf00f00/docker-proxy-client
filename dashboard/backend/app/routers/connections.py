"""Real-time live-connections SSE.

One shared collector (see ``connections_service``) fans the latest grouped
connection snapshot out to every connected client, so N open dashboards cost the
same as one. Read-only — no auth dependency, matching the other ``/stream``
endpoints.
"""

import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter

from app.auth import RequireAuth
from app.services.connections_service import collector
from app.sse import comment, event, sse_response

router = APIRouter(prefix="/connections", tags=["connections"])

# If no fresh snapshot arrives within this window, send a keep-alive comment so
# proxies/load-balancers don't drop the idle connection.
HEARTBEAT_SEC = 10.0


@router.get("/stream", dependencies=[RequireAuth])
async def stream_connections():
    async def gen() -> AsyncGenerator[str]:
        q = await collector.subscribe()
        try:
            snap = collector.latest()
            if snap is not None:
                yield event("connections", snap)
            while True:
                try:
                    snap = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SEC)
                    yield event("connections", snap)
                except TimeoutError:
                    yield comment()
        finally:
            await collector.unsubscribe(q)

    return sse_response(gen())
