import asyncio

from fastapi import APIRouter, HTTPException

from app.models.schemas import ConfigFile, ConfigUpdate
from app.services import config_service, docker_service, env_service

router = APIRouter(prefix="/config", tags=["config"])


async def _require_config_path(container_name: str) -> str:
    container = await asyncio.to_thread(docker_service.find_dashboard_container, container_name)
    if not container or not container.dashboard.config:
        raise HTTPException(status_code=404, detail="No config available for this service")
    return container.dashboard.config


@router.get("/{container_name}", response_model=ConfigFile)
async def get_config(container_name: str):
    config_path = await _require_config_path(container_name)
    try:
        content, filename, language = config_service.read_config(config_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Config file not found") from None
    return ConfigFile(content=content, filename=filename, language=language)


@router.put("/{container_name}")
async def update_config(container_name: str, update: ConfigUpdate):
    config_path = await _require_config_path(container_name)
    try:
        config_service.write_config(config_path, update.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    success, message = await asyncio.to_thread(env_service.restart_service, container_name)
    if not success:
        return {
            "success": True,
            "applied": False,
            "message": f"Saved, but failed to apply: {message}. Run `docker compose restart {container_name}` manually.",
        }
    return {"success": True, "applied": True, "message": "Saved and applied"}
