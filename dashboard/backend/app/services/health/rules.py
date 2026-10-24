"""Pure rules over recorded samples, calibrated on the first week of real data."""

from collections.abc import Sequence
from dataclasses import dataclass

EDGE_WINDOW_MIN = 30
EDGE_MIN_ATTEMPTS = 60
EDGE_POOR = 0.10
EDGE_WATCH = 0.05
EDGE_FAST_WINDOW_MIN = 5
EDGE_FAST_MIN_ATTEMPTS = 10
EDGE_FAST_RATIO = 0.5

CAPACITY_MIN_MINUTES = 1440
CAPACITY_DOWN_PCT = 95.0
CAPACITY_UP_PCT = 99.9


def _ratio(window: Sequence[tuple[int, int]]) -> tuple[int, float]:
    attempts = sum(a for a, _ in window)
    return attempts, (sum(f for _, f in window) / attempts if attempts else 0.0)


def edge_state(minutes: Sequence[tuple[int, int]]) -> str:
    """Grade one sni-spoofing instance from its newest per-minute (attempts, failures).

    ``fast`` is a near-total handshake failure (a blocked decoy name), ``poor`` and
    ``watch`` are sustained elevated ratios, ``nodata`` means too few attempts to say.
    """
    attempts, ratio = _ratio(minutes[-EDGE_FAST_WINDOW_MIN:])
    if attempts >= EDGE_FAST_MIN_ATTEMPTS and ratio >= EDGE_FAST_RATIO:
        return "fast"
    attempts, ratio = _ratio(minutes[-EDGE_WINDOW_MIN:])
    if attempts < EDGE_MIN_ATTEMPTS:
        return "nodata"
    if ratio > EDGE_POOR:
        return "poor"
    if ratio > EDGE_WATCH:
        return "watch"
    return "ok"


@dataclass(frozen=True, slots=True)
class Capacity:
    down_bps: float
    up_bps: float


def _percentile(values: Sequence[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]


def learn_capacity(peaks_down: Sequence[float], peaks_up: Sequence[float], default: Capacity) -> Capacity:
    """Bytes/s ceilings from per-minute peak rates. Upload is rarely stressed, so its
    ceiling is the extreme tail; the download p95 matches the line rate."""
    if min(len(peaks_down), len(peaks_up)) < CAPACITY_MIN_MINUTES:
        return default
    return Capacity(
        _percentile(peaks_down, CAPACITY_DOWN_PCT) or default.down_bps,
        _percentile(peaks_up, CAPACITY_UP_PCT) or default.up_bps,
    )


def utilization(feed: dict, capacity: Capacity) -> float:
    """Busiest direction's per-minute average rate over its capacity."""
    return max(feed.get("down", 0) / 60 / capacity.down_bps, feed.get("up", 0) / 60 / capacity.up_bps)
