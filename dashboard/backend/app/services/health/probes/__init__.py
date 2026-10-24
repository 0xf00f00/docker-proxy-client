from dataclasses import dataclass, field


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
