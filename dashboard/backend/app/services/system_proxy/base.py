from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.models.schemas import SystemProxyReorderResult, SystemProxyState


class Capability(StrEnum):
    """Optional controller features; the value is the API-facing tag, and ``registry`` maps each to its protocol."""

    CONNECTIONS = "connections"
    TRAFFIC = "traffic"
    PATHS = "paths"
    PATH_PROBE = "path_probe"


@dataclass(frozen=True, slots=True)
class Connection:
    """One live connection, normalized so no consumer depends on a controller's wire format."""

    id: str
    host: str  # SNI/hostname the connection targets; "" when unknown (raw-IP dest).
    dest_ip: str
    dest_port: str
    network: str  # "tcp" | "udp"
    chains: tuple[str, ...]  # proxy chain, outermost-group-last (so chains[0] is the egress).
    upload: int  # cumulative bytes this session.
    download: int
    rule: str  # matched routing rule, "" if none.
    start: str  # ISO-8601 start time, "" if unknown.
    internal: bool = False  # the controller's own traffic (health checks, DNS)

    @property
    def exit(self) -> str:
        """The proxy this connection actually egresses through."""
        return self.chains[0] if self.chains else "DIRECT"


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    """A whole-state snapshot: session byte totals plus every open connection."""

    upload_total: int
    download_total: int
    connections: tuple[Connection, ...]


EMPTY_SNAPSHOT = ConnectionSnapshot(upload_total=0, download_total=0, connections=())


@dataclass(frozen=True, slots=True)
class PathHealth:
    alive: bool | None
    delay_ms: int | None


@dataclass(frozen=True, slots=True)
class PathStatus:
    active: str | None
    paths: dict[str, PathHealth]
    local: frozenset[str] = frozenset()  # proxies the controller itself types as direct/reject, not a route out


@runtime_checkable
class SystemProxyController(Protocol):
    """What every system-proxy implementation provides. Built per request, so keep construction cheap and stateless."""

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


@runtime_checkable
class SupportsTrafficStream(Protocol):
    """Optional capability: a controller that can report whole-system throughput."""

    def stream_traffic(self) -> AsyncIterator[tuple[int, int]]:
        """Async iterator of ``(up_bps, down_bps)`` samples; ends or raises when the connection drops."""
        ...


@runtime_checkable
class SupportsConnectionsStream(Protocol):
    """Optional capability: a controller that can report live per-connection state."""

    def stream_connections(self) -> AsyncIterator[ConnectionSnapshot]:
        """Async iterator of whole-state snapshots (session totals plus every open connection)."""
        ...


@runtime_checkable
class SupportsPathStatus(Protocol):
    """Optional capability: report the failover choice and each route's own last health check."""

    async def path_status(self) -> PathStatus: ...


@runtime_checkable
class SupportsPathProbe(Protocol):
    """Optional capability: run one route's own delay check on demand."""

    async def probe_path(self, name: str) -> int | None:
        """Delay in ms through that route, or None when it failed."""
        ...
