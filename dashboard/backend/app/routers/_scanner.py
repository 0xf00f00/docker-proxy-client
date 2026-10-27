import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.schemas import ContainerActionResponse
from app.sse import snapshot_stream

logger = logging.getLogger(__name__)

STREAM_HEARTBEAT_S = 10.0


def scanner_router(prefix: str, service, status_model: type[BaseModel]) -> APIRouter:
    """`/status` and `/stream` for a scanner service exposing `get_status()` and `iter_events()`."""
    name = prefix.strip("/")
    router = APIRouter(prefix=prefix, tags=[name])

    @router.get("/status", response_model=status_model)
    async def get_status():
        try:
            return await asyncio.to_thread(service.get_status)
        except Exception:
            logger.exception("Failed to read %s status", name)
            raise HTTPException(status_code=503, detail=f"Cannot read {name} status") from None

    @router.get("/stream")
    async def stream_status():
        async def read() -> str:
            return (await asyncio.to_thread(service.get_status)).model_dump_json()

        return snapshot_stream("status", read, interval=STREAM_HEARTBEAT_S, feed=service.iter_events)

    return router


async def run_action(fn: Callable[[], object], ok: str, fail: str) -> ContainerActionResponse:
    try:
        await asyncio.to_thread(fn)
        return ContainerActionResponse(success=True, message=ok)
    except Exception:
        logger.exception(fail)
        raise HTTPException(status_code=500, detail=fail) from None
