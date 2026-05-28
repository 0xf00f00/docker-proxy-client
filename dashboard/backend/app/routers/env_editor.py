import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.auth import RequireAuth
from app.models.schemas import EnvUpdateRequest
from app.services import docker_service, env_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/env", tags=["env"], dependencies=[RequireAuth])


async def _editable_env_keys(container_name: str) -> list[str]:
    container = await asyncio.to_thread(docker_service.find_dashboard_container, container_name)
    if not container:
        raise HTTPException(status_code=404, detail="Container not found")
    if not container.dashboard.env:
        raise HTTPException(status_code=404, detail="No editable env vars for this service")
    return container.dashboard.env


@router.get("/{container_name}")
async def get_env(container_name: str):
    keys = await _editable_env_keys(container_name)
    try:
        values = await asyncio.to_thread(env_service.read_env, keys)
    except FileNotFoundError:
        logger.exception("Compose .env not found while reading env for %s", container_name)
        raise HTTPException(status_code=500, detail="Compose project not found") from None
    return {"keys": keys, "values": values}


@router.put("/{container_name}")
async def update_env(container_name: str, request: EnvUpdateRequest):
    allowed = set(await _editable_env_keys(container_name))
    updates = {k: v for k, v in request.values.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid keys to update")

    try:
        await asyncio.to_thread(env_service.write_env, updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except FileNotFoundError:
        logger.exception("Compose .env not found while writing env for %s", container_name)
        raise HTTPException(status_code=500, detail="Compose project not found") from None

    success, message = await asyncio.to_thread(env_service.recreate_service, container_name)
    if not success:
        return {
            "success": True,
            "applied": False,
            "message": f"Saved, but failed to apply: {message}. Run `docker compose up -d {container_name}` manually.",
        }
    return {"success": True, "applied": True, "message": "Saved and applied"}
