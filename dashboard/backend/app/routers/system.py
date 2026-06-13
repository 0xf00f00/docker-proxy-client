import asyncio

from fastapi import APIRouter

from app.config import settings
from app.services import system_service
from app.services.system_proxy import Capability, registry
from app.sse import sse_response

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
async def health():
    dns, connectivity, caps = await asyncio.gather(
        system_service.test_dns(),
        system_service.test_connectivity(),
        asyncio.to_thread(registry.active_capabilities),
    )
    connection_tracking = settings.connection_tracking and Capability.CONNECTIONS in caps
    return {"dns": dns, "connectivity": connectivity, "connection_tracking": connection_tracking}


@router.get("/speed/stream")
async def speed_stream():
    return sse_response(system_service.speed_test_stream())


@router.post("/speed/cancel")
async def cancel_speed():
    return {"cancelled": system_service.cancel_speed_test()}
