"""Helpers for Server-Sent Events responses."""

import asyncio
import contextlib
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable

from fastapi.responses import StreamingResponse

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
DEBOUNCE_S = 0.3
RECONNECT_S = 2.0


def sse_response(generator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)


def event_raw(event_name: str, payload: str) -> str:
    return f"event: {event_name}\ndata: {payload}\n\n"


def event(event_name: str, data: object) -> str:
    return event_raw(event_name, json.dumps(data))


def comment(text: str = "ping") -> str:
    return f": {text}\n\n"


def snapshot_stream(
    name: str,
    read: Callable[[], Awaitable[str]],
    *,
    interval: float,
    feed: Callable[[], Iterable[object]] | None = None,
) -> StreamingResponse:
    """Emit `read()`'s JSON payload as a `name` event whenever it changes, else a keep-alive.

    Without `feed` the payload is re-read every `interval` seconds. With `feed` (a blocking
    iterable yielding once per upstream change, re-opened after a drop) a change triggers an
    immediate re-read and `interval` is only the heartbeat floor.
    """

    async def gen() -> AsyncIterator[str]:
        wake = asyncio.Event()
        stopped = threading.Event()
        if feed is not None:
            loop = asyncio.get_running_loop()

            def listen() -> None:
                while not stopped.is_set():
                    with contextlib.suppress(Exception):
                        for _ in feed():
                            if stopped.is_set():
                                return
                            loop.call_soon_threadsafe(wake.set)
                    stopped.wait(RECONNECT_S)

            threading.Thread(target=listen, daemon=True).start()

        last: str | None = None
        try:
            while True:
                try:
                    payload = await read()
                    yield event_raw(name, payload) if payload != last else comment()
                    last = payload
                except Exception as e:
                    yield event("stream-error", {"detail": str(e)})
                if feed is None:
                    await asyncio.sleep(interval)
                    continue
                try:
                    await asyncio.wait_for(wake.wait(), timeout=interval)
                except TimeoutError:
                    continue
                wake.clear()
                await asyncio.sleep(DEBOUNCE_S)  # coalesce a burst of upstream changes
                wake.clear()
        finally:
            stopped.set()

    return sse_response(gen())
