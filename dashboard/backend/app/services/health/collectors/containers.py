from collections.abc import Callable, Iterable

from app.services import docker_service
from app.services.health.settings import PATH_EDGES
from app.services.system_proxy import registry


def core_names() -> tuple[str, ...]:
    """Containers whose restarts matter: the proxy controller (found by its label) plus every route and edge."""
    try:
        controller = registry.get_active_container()
    except Exception:
        controller = None
    named = [controller.name] if controller else []
    return tuple(dict.fromkeys([*named, *PATH_EDGES, *PATH_EDGES.values()]))


class RestartWatch:
    def __init__(self, names: Callable[[], Iterable[str]]) -> None:
        self._names = names
        self._started: dict[str, str] = {}

    def poll(self) -> list[dict]:
        """Containers that started or stopped since the last poll (blocking)."""
        api = docker_service.get_client().api
        changes = []
        for name in self._names():
            try:
                state = api.inspect_container(name).get("State") or {}
            except Exception:
                continue
            mark = state.get("StartedAt", "") if state.get("Running") else "stopped"
            prev = self._started.get(name)
            self._started[name] = mark
            if prev is not None and mark != prev:
                changes.append({"name": name, "running": bool(state.get("Running"))})
        return changes
