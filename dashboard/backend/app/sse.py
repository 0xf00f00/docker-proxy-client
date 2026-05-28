"""Helpers for Server-Sent Events responses."""

import json
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def sse_response(generator: AsyncIterator[str]) -> StreamingResponse:
    """Wrap an async string generator as an SSE StreamingResponse."""
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)


def event(event_name: str, data: object) -> str:
    """Format a single SSE event with JSON payload."""
    return f"event: {event_name}\ndata: {json.dumps(data)}\n\n"


def comment(text: str = "ping") -> str:
    """Format an SSE comment line (used as a keep-alive)."""
    return f": {text}\n\n"
