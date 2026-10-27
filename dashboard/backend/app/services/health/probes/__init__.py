from dataclasses import dataclass, field
from itertools import pairwise


@dataclass(slots=True)
class ProbeResult:
    ok: bool
    bytes: int  # estimated wire cost, tunnel overhead included
    data: dict = field(default_factory=dict)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]


def jitter(values: list[float]) -> float | None:
    """Mean step between consecutive samples: the delay variation WebRTC cares about, not stdev."""
    steps = [abs(b - a) for a, b in pairwise(values)]
    return sum(steps) / len(steps) if steps else None
