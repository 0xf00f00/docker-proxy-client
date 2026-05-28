from typing import Protocol, runtime_checkable

from app.models.schemas import SystemProxyReorderResult, SystemProxyState


@runtime_checkable
class SystemProxyController(Protocol):
    """The interface every system-proxy implementation must satisfy.

    Implementations are constructed per-request from the registered factory
    so they pick up live container/config state — they should be cheap to
    construct and must not hold long-lived state across calls.
    """

    # Name of the container in Docker. Used by the router for restart actions.
    container_name: str

    async def get_state(self) -> SystemProxyState: ...

    async def set_mode(self, mode: str) -> None:
        """Switch between 'auto' and 'manual' modes (or controller-equivalent)."""
        ...

    async def switch(self, name: str) -> None:
        """Activate a specific route. Typically only valid in 'manual' mode."""
        ...

    async def reorder(self, routes: list[str]) -> SystemProxyReorderResult:
        """Change the priority order of routes. Typically only valid in 'auto' mode."""
        ...

    async def test_latencies(self) -> dict[str, int]:
        """Probe every route and return a map of name -> latency_ms (-1 for failure)."""
        ...
