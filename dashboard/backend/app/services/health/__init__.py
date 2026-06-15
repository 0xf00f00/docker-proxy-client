"""Overall network-health-over-time monitor.

A single always-on probe samples whether the user can actually reach the open
internet *through the active system proxy* and how fast, tags the cause via the
regime classifier, and records a time series so the dashboard can show outages
and quality degradation over time — and answer "how was the network today?".

This is composite system health, not per-proxy. The package is split by concern:

- ``settings``  — env-overridable tunables and the shared ``Status`` type.
- ``cause``     — map a (status, regime) pair to plain language for the UI.
- ``timeline``  — pure analytics over stored segments (buckets, summary, incidents).
- ``probe``     — the probe I/O and regime-backed classification.
- ``monitor``   — the always-on loop + persistence; exposes the ``monitor`` singleton.

This module is the public facade: import what you need from ``app.services.health``.
"""

from app.services.health.cause import cause_for, snapshot_from_segment
from app.services.health.monitor import HealthMonitor, monitor
from app.services.health.settings import MAX_GAP_S, STATUSES, Status
from app.services.health.timeline import (
    bucketize,
    clip_segments,
    find_incidents,
    summarize,
)

__all__ = [
    "MAX_GAP_S",
    "STATUSES",
    "HealthMonitor",
    "Status",
    "bucketize",
    "cause_for",
    "clip_segments",
    "find_incidents",
    "monitor",
    "snapshot_from_segment",
    "summarize",
]
