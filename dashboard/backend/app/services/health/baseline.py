"""Per-path baselines learned from the active probes' own history."""

from collections.abc import Iterable
from dataclasses import dataclass

from app.services.health.model import Probe
from app.services.health.probes import percentile

BASELINE_SPAN_S = 14 * 86400
GOODPUT_MIN = 5
STUN_MIN = 12
GOODPUT_PCT = 75


@dataclass(frozen=True, slots=True)
class Baseline:
    goodput_mbit: float | None = None
    stun_rtt_p95: float | None = None
    goodput_n: int = 0
    stun_n: int = 0

    @property
    def ready(self) -> bool:
        return self.goodput_mbit is not None and self.stun_rtt_p95 is not None


def build(probes: Iterable[Probe], path: str | None, now: float) -> Baseline:
    """A high quantile of goodput, so a degraded week can't become its own normal;
    the median of the per-probe RTT p95. Unknown until enough runs on that path."""
    since = now - BASELINE_SPAN_S
    goodput: list[float] = []
    rtt: list[float] = []
    for p in probes:
        if not p.ok or p.path != path or p.ts < since:
            continue
        if p.kind == "goodput" and p.data.get("mbit"):
            goodput.append(p.data["mbit"])
        elif p.kind == "stun" and p.data.get("rtt_p95"):
            rtt.append(p.data["rtt_p95"])
    return Baseline(
        percentile(goodput, GOODPUT_PCT) if len(goodput) >= GOODPUT_MIN else None,
        percentile(rtt, 50) if len(rtt) >= STUN_MIN else None,
        len(goodput),
        len(rtt),
    )
