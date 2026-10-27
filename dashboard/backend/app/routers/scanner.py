import asyncio
import logging

from fastapi import HTTPException

from app.auth import RequireAuth
from app.models.schemas import (
    ContainerActionResponse,
    EdgeTestRequest,
    EdgeTestResponse,
    ScannerStatus,
)
from app.routers._scanner import run_action, scanner_router
from app.services import scanner_service

logger = logging.getLogger(__name__)
router = scanner_router("/scanner", scanner_service, ScannerStatus)


@router.post("/run", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def run_now():
    return await run_action(scanner_service.trigger_scan, "Scan triggered", "Failed to trigger scan")


@router.post("/cancel", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def cancel_now():
    return await run_action(scanner_service.cancel_scan, "Scan stop requested", "Failed to cancel scan")


# Unauthenticated like the other measurement endpoints: it probes one validated IP and changes no config.
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
