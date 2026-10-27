from app.auth import RequireAuth
from app.models.schemas import ContainerActionResponse, DnsScannerStatus
from app.routers._scanner import run_action, scanner_router
from app.services import dns_scanner_service

router = scanner_router("/dns-scanner", dns_scanner_service, DnsScannerStatus)


@router.post("/scan", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def run_now():
    return await run_action(dns_scanner_service.trigger_scan, "Scan started", "Failed to start scan")


@router.post("/pause", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def pause_now():
    return await run_action(dns_scanner_service.pause_scan, "Scan paused", "Failed to pause scan")


@router.post("/resume", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def resume_now():
    return await run_action(dns_scanner_service.resume_scan, "Scan resumed", "Failed to resume scan")


@router.post("/stop", response_model=ContainerActionResponse, dependencies=[RequireAuth])
async def stop_now():
    return await run_action(dns_scanner_service.stop_scan, "Scan stopped", "Failed to stop scan")
