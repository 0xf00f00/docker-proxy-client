from fastapi import APIRouter

from app.services import system_service
from app.sse import sse_response

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/dns")
async def test_dns():
    return await system_service.test_dns()


@router.get("/connectivity")
async def test_connectivity():
    return await system_service.test_connectivity()


@router.get("/speed/stream")
async def speed_stream():
    return sse_response(system_service.speed_test_stream())


@router.post("/speed/cancel")
async def cancel_speed():
    return {"cancelled": system_service.cancel_speed_test()}
