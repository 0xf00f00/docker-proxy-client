import asyncio
import logging
import threading
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException

from app.auth import RequireAuth
from app.models.schemas import (
    ContainerActionResponse,
    EdgeTestRequest,
    EdgeTestResponse,
    ScannerStatus,
)
from app.services import scanner_service
from app.sse import event, sse_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scanner", tags=["scanner"])

# Heartbeat floor: also bounds latency for changes the scanner can't push
# (byedpi/snispoof IPs come from local files, not the scanner's event stream).
STREAM_HEARTBEAT_SEC = 10.0
EVENT_DEBOUNCE_SEC = 0.3
RECONNECT_DELAY_SEC = 2.0


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

    A background thread subscribes to the scanner's own SSE event stream and
    wakes this generator on each change, so updates appear immediately instead
    of on a fixed poll. A periodic heartbeat still runs as a floor (and catches
    byedpi/snispoof changes, which the scanner can't push) and so the stream
    self-heals if the scanner's event socket drops.
    """
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    stopped = threading.Event()

    def listen() -> None:
        # Reconnecting loop: also rides through scanner restarts. Until/if the
        # event stream is up, the heartbeat below keeps the dashboard fresh.
        while not stopped.is_set():
            try:
                for _ in scanner_service.iter_events():
                    if stopped.is_set():
                        return
                    loop.call_soon_threadsafe(wake.set)
            except Exception:
                pass
            stopped.wait(RECONNECT_DELAY_SEC)

    threading.Thread(target=listen, daemon=True).start()

    async def gen() -> AsyncGenerator[str]:
        last: str | None = None
        try:
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

                try:
                    await asyncio.wait_for(wake.wait(), timeout=STREAM_HEARTBEAT_SEC)
                except TimeoutError:
                    continue
                wake.clear()
                # Coalesce a burst of changes (e.g. several tests finishing).
                await asyncio.sleep(EVENT_DEBOUNCE_SEC)
                wake.clear()
        finally:
            stopped.set()

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
@router.post("/cancel", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def cancel_now():
    try:
        await asyncio.to_thread(scanner_service.cancel_scan)
        return ContainerActionResponse(success=True, message="Scan stop requested")
    except Exception:
        logger.exception("Failed to cancel scan")
        raise HTTPException(status_code=500, detail="Failed to cancel scan") from None


@router.post("/test", response_model=EdgeTestResponse)
async def test_edge(req: EdgeTestRequest):
    try:
        pending = await asyncio.to_thread(scanner_service.trigger_test, req.ip)
        message = f"Testing {req.ip}" if pending else f"{req.ip} was tested recently"
        return EdgeTestResponse(success=True, message=message, pending=pending)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid IP address") from None
    except Exception:
        logger.exception("Failed to trigger edge test")
        raise HTTPException(status_code=500, detail="Failed to trigger test") from None
