from app.services import docker_service


class RestartWatch:
    def __init__(self, names: tuple[str, ...]) -> None:
        self._names = names
        self._started: dict[str, str] = {}

    def poll(self) -> list[dict]:
        """Containers that started or stopped since the last poll (blocking)."""
        api = docker_service.get_client().api
        changes = []
        for name in self._names:
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
