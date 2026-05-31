import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException

from app.auth import RequireAuth
from app.models.schemas import ContainerActionResponse, EdgeTestRequest, ScannerStatus
from app.services import scanner_service
from app.sse import event, sse_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scanner", tags=["scanner"])

STREAM_POLL_SEC = 2.0


@router.get("/status", response_model=ScannerStatus)
async def get_status():
    try:
        return await asyncio.to_thread(scanner_service.get_status)
    except Exception:
        logger.exception("Failed to read scanner status")
        raise HTTPException(status_code=503, detail="Cannot read scanner status") from None


@router.get("/stream")
async def stream_status():
    """Push scanner status (scanning/idle, picks, pool) live over SSE.

    Server samples every STREAM_POLL_SEC and emits a `status` event only on
    change, so a multi-second scan is reliably observed without the client
    polling and racing the transient `.scanning` marker.
    """

    async def gen() -> AsyncGenerator[str]:
        last: str | None = None
        while True:
            try:
                status = await asyncio.to_thread(scanner_service.get_status)
                payload = status.model_dump_json()
                if payload != last:
                    yield f"event: status\ndata: {payload}\n\n"
                    last = payload
                else:
                    yield ": ping\n\n"
            except Exception as e:
                yield event("stream-error", {"detail": str(e)})
            await asyncio.sleep(STREAM_POLL_SEC)

    return sse_response(gen())


@router.post("/run", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def run_now():
    try:
        await asyncio.to_thread(scanner_service.trigger_scan)
        return ContainerActionResponse(success=True, message="Scan triggered")
    except Exception:
        logger.exception("Failed to trigger scan")
        raise HTTPException(status_code=500, detail="Failed to trigger scan") from None


# Public, like the other measurement endpoints (connectivity test, speed test):
# it only probes one IP's reachability and reports back over the (public)
# scanner stream — it changes no config. IP is validated as an injection guard.
@router.post("/test", response_model=ContainerActionResponse)
async def test_edge(req: EdgeTestRequest):
    try:
        await asyncio.to_thread(scanner_service.trigger_test, req.ip)
        return ContainerActionResponse(success=True, message=f"Testing {req.ip}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid IP address") from None
    except Exception:
        logger.exception("Failed to trigger edge test")
        raise HTTPException(status_code=500, detail="Failed to trigger test") from None
