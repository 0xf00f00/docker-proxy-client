from collections.abc import Callable

from app.models.schemas import ContainerInfo
from app.services import docker_service
from app.services.system_proxy.base import SystemProxyController

# A factory takes the container backing the controller and returns an instance.
ControllerFactory = Callable[[ContainerInfo], SystemProxyController]

_REGISTRY: dict[str, ControllerFactory] = {}


def register(name: str, factory: ControllerFactory) -> None:
    _REGISTRY[name] = factory


def get_active_controller() -> SystemProxyController | None:
    """Find the single container with `dashboard.controller=<name>` and
    return an instance of the matching registered controller. Returns None
    if no such container exists, the controller name isn't registered, or
    multiple candidates are found (ambiguous configuration)."""
    candidates = [
        c
        for c in docker_service.list_dashboard_containers()
        if c.dashboard.controller and c.dashboard.controller in _REGISTRY
    ]
    if len(candidates) != 1:
        return None
    container = candidates[0]
    factory = _REGISTRY[container.dashboard.controller or ""]
    return factory(container)
