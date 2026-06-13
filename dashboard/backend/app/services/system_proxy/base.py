from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.models.schemas import SystemProxyReorderResult, SystemProxyState


class Capability(StrEnum):
    """Optional capabilities a controller may implement, as named feature tags.

    The value doubles as the API-facing tag; ``registry`` maps each to the
    protocol a controller must satisfy to offer it.
    """

    CONNECTIONS = "connections"
    TRAFFIC = "traffic"


@dataclass(frozen=True, slots=True)
class Connection:
    """One live connection, normalized across controllers.

    This is the contract consumers depend on — each controller translates its
    own wire format into this shape, so no consumer knows Clash's (or any other
    backend's) JSON schema.
    """

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


@runtime_checkable
class SupportsTrafficStream(Protocol):
    """Optional capability: a controller that can report whole-system throughput."""

    def stream_traffic(self) -> AsyncIterator[tuple[int, int]]:
        """Return an async iterator of ``(up_bps, down_bps)`` samples as they arrive.

        Implemented as an async generator. Long-lived: runs until the underlying
        connection drops, then returns or raises so the caller can reconnect.
        """
        ...


@runtime_checkable
class SupportsConnectionsStream(Protocol):
    """Optional capability: a controller that can report live per-connection state."""

    def stream_connections(self) -> AsyncIterator[ConnectionSnapshot]:
        """Return an async iterator of normalized snapshots as they arrive.

        Each yielded ``ConnectionSnapshot`` is one whole-state view: session byte
        totals plus every currently-open connection. Implemented as an async
        generator; long-lived until the source drops, then returns/raises so the
        caller can reconnect.
        """
        ...
