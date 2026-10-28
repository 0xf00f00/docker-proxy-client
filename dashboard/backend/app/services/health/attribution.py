"""Which layer broke: the §8 table as one pure function from collected facts to (cause, plain sentence)."""

from dataclasses import dataclass, field

from app.services.health.model import BUSY, CHOPPY, DOWN, GOOD, NOT_MEASURED, POWER_CUT, SLOW

UNREACH_BURST = 10
DECOY_RATIO = 0.9
DECOY_ACK_SHARE = 0.8
DECOY_MIN_INSTANCES = 2

LABELS = {
    "ok": "Network healthy",
    "busy": "The connection is full right now",
    "calls": "Calls may be choppy",
    "calls_busy": "Calls may be choppy while the connection is full",
    "regime": "International internet blocked",
    "internet": "Internet is down",
    "local_network": "Home network is down",
    "proxy": "Proxy unreachable",
    "proxy_restarted": "The proxy was restarting",
    "decoy_blocked": "The network is blocking our connections",
    "overheating": "The device is running hot",
    "route_degraded": "This route is struggling; a backup route is available",
    "slow_path": "Downloads are slower than usual",
    "slow": "Connection is slow",
    "power_cut": "The power was cut",
    "restart": "The device was restarted",
    "monitor": "Not measured",
    "feed": "Not measured",
}


@dataclass(slots=True)
class Facts:
    state: str
    regime: str | None = None  # newest ladder result, None when the ladder hasn't run
    all_dead: bool = False
    # per sni-spoofing instance over the last 5 minutes: (attempts, failures, ack timeouts)
    edges: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    unreach: int = 0
    standby_alive: bool = False
    switch_helps: bool | None = None  # ladder's standby check, None when not run
    restarted: bool = False
    hot: bool = False
    browse_bad: bool = False
    call_busy: bool = False


def decoy_blocked(edges: dict[str, tuple[int, int, int]]) -> bool:
    """Every measured instance failing handshakes by timeout at once: the decoy name, not an edge."""
    seen = [(a, f, k) for a, f, k in edges.values() if a >= 10]
    return len(seen) >= DECOY_MIN_INSTANCES and all(
        f / a >= DECOY_RATIO and f and k / f >= DECOY_ACK_SHARE for a, f, k in seen
    )


def attribute(f: Facts) -> tuple[str, str]:
    cause = _cause(f)
    return cause, LABELS[cause]


def _cause(f: Facts) -> str:
    if f.state == GOOD:
        return "ok"
    if f.state == POWER_CUT:
        return "power_cut"
    if f.state == NOT_MEASURED:
        return "monitor"
    if f.state == BUSY:
        return "busy"
    if f.state == CHOPPY:
        return "calls_busy" if f.call_busy else "calls"
    if f.state not in (DOWN, SLOW):
        return "slow"
    if f.regime == "iran_only":
        return "regime"
    if f.regime == "total_outage":
        return "internet"
    if f.regime == "local_network":
        return "local_network"
    if f.restarted:
        return "proxy_restarted"
    if decoy_blocked(f.edges) and f.regime in (None, "normal") and f.unreach < UNREACH_BURST:
        return "decoy_blocked"
    if f.unreach >= UNREACH_BURST and f.regime is None:
        return "internet"
    if f.state == DOWN:
        return "proxy"
    if f.hot:
        return "overheating"
    if f.browse_bad:
        return "route_degraded" if f.switch_helps or (f.switch_helps is None and f.standby_alive) else "slow"
    return "slow_path"
