import asyncio

from app.services.system_proxy import registry
from app.services.system_proxy.base import PathStatus, SupportsPathStatus


def summarize(status: PathStatus) -> dict:
    return {
        "active": status.active,
        "alive": {n: p.alive for n, p in status.paths.items()},
        "delay": {n: p.delay_ms for n, p in status.paths.items()},
    }


async def read() -> dict | None:
    controller = await asyncio.to_thread(registry.get_active_controller)
    if not isinstance(controller, SupportsPathStatus):
        return None
    return summarize(await controller.path_status())
