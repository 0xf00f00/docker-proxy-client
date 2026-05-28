"""Generic system-proxy router. Dispatches to whichever controller is
registered + active (see services/system_proxy). The router never imports
a concrete controller — adding a new backend (xray, sing-box, …) only
requires implementing the SystemProxyController interface."""

import asyncio

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    IpInfo,
    SystemProxyModeRequest,
    SystemProxyReorderRequest,
    SystemProxyReorderResult,
    SystemProxyState,
    SystemProxySwitchRequest,
)
from app.services import docker_service, ip_lookup, system_proxy
from app.services.system_proxy.base import SystemProxyController

router = APIRouter(prefix="/system-proxy", tags=["system-proxy"])


def _require_controller() -> SystemProxyController:
    controller = system_proxy.get_active_controller()
    if controller is None:
        raise HTTPException(status_code=404, detail="No active system-proxy controller")
    return controller


@router.get("/state", response_model=SystemProxyState)
async def get_state():
    controller = _require_controller()
    try:
        return await controller.get_state()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Controller error: {e}") from None


@router.put("/mode")
async def set_mode(request: SystemProxyModeRequest):
    controller = _require_controller()
    try:
        await controller.set_mode(request.mode)
        return {"success": True, "mode": request.mode}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Controller error: {e}") from None


@router.put("/active")
async def switch(request: SystemProxySwitchRequest):
    controller = _require_controller()
    try:
        await controller.switch(request.name)
        return {"success": True, "active": request.name}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Controller error: {e}") from None


@router.put("/order", response_model=SystemProxyReorderResult)
async def reorder(request: SystemProxyReorderRequest):
    controller = _require_controller()
    try:
        return await controller.reorder(request.routes)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reorder failed: {e}") from None


@router.get("/latencies")
async def test_latencies():
    controller = _require_controller()
    try:
        return await controller.test_latencies()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Controller error: {e}") from None


def _proxy_url_for(protocol: str, address: str) -> str | None:
    proto = (protocol or "").split("+")[0]
    if proto in ("socks5", "socks"):
        return f"socks5://{address}"
    if proto in ("http", "mixed"):
        return f"http://{address}"
    return None


@router.get("/egress-ip", response_model=IpInfo | None)
async def egress_ip():
    """Resolve the public IP+country reachable *through* the active system
    proxy. Cached briefly so the dashboard can call this freely on mount."""
    container = await asyncio.to_thread(system_proxy.get_active_container)
    if container is None or not container.probe_address:
        return None
    proxy_url = _proxy_url_for(container.dashboard.protocol, container.probe_address)
    if not proxy_url:
        return None
    return await ip_lookup.lookup(proxy_url)
