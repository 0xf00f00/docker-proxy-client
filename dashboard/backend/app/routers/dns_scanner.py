import asyncio
import logging
import threading
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException

from app.auth import RequireAuth
from app.models.schemas import ContainerActionResponse, DnsScannerStatus
from app.services import dns_scanner_service
from app.sse import event, sse_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dns-scanner", tags=["dns-scanner"])

# Heartbeat floor: also bounds latency for changes the scanner can't push and so
# the stream self-heals if the scanner's event socket drops.
STREAM_HEARTBEAT_SEC = 10.0
EVENT_DEBOUNCE_SEC = 0.3
RECONNECT_DELAY_SEC = 2.0


@router.get("/status", response_model=DnsScannerStatus)
async def get_status():
    try:
        return await asyncio.to_thread(dns_scanner_service.get_status)
    except Exception:
        logger.exception("Failed to read dns-scanner status")
        raise HTTPException(status_code=503, detail="Cannot read dns-scanner status") from None


@router.get("/stream")
async def stream_status():
    """Push dns-scanner status (state, progress, resolvers, schedule) live over SSE.

    A background thread subscribes to the scanner's own SSE event stream and wakes
    this generator on each change, so updates appear immediately. A periodic
    heartbeat runs as a floor (and rides through scanner restarts).
    """
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    stopped = threading.Event()

    def listen() -> None:
        while not stopped.is_set():
            try:
                for _ in dns_scanner_service.iter_events():
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
                    status = await asyncio.to_thread(dns_scanner_service.get_status)
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
                # Coalesce a burst of changes (e.g. several resolvers accepted).
                await asyncio.sleep(EVENT_DEBOUNCE_SEC)
                wake.clear()
        finally:
            stopped.set()

    return sse_response(gen())


def _action(fn, ok_message: str, fail_message: str) -> ContainerActionResponse:
    try:
        fn()
        return ContainerActionResponse(success=True, message=ok_message)
    except Exception:
        logger.exception(fail_message)
        raise HTTPException(status_code=500, detail=fail_message) from None


@router.post("/scan", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def run_now():
    return await asyncio.to_thread(_action, dns_scanner_service.trigger_scan, "Scan started", "Failed to start scan")


@router.post("/pause", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def pause_now():
    return await asyncio.to_thread(_action, dns_scanner_service.pause_scan, "Scan paused", "Failed to pause scan")


@router.post("/resume", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def resume_now():
    return await asyncio.to_thread(_action, dns_scanner_service.resume_scan, "Scan resumed", "Failed to resume scan")


@router.post("/stop", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def stop_now():
    return await asyncio.to_thread(_action, dns_scanner_service.stop_scan, "Scan stopped", "Failed to stop scan")
