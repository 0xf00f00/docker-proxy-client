"""Composite network health over time (not per-proxy): one always-on probe through the active system proxy,
tagged with a cause by the regime classifier and stored as a time series. Public facade for the package.

- ``settings``  — constants and the shared ``Status`` type.
- ``cause``     — map a (status, regime) pair to plain language for the UI.
- ``timeline``  — pure analytics over stored segments (buckets, summary, incidents).
- ``probe``     — the probe I/O and regime-backed classification.
- ``monitor``   — the always-on loop + persistence; exposes the ``monitor`` singleton.
- ``recorder``  — per-minute raw samples from ``collectors``.
- ``scheduler`` — budgeted active ``probes`` and their ledger.
- ``verdict``   — v2: minutes folded into states (with ``baseline``, ``attribution``); ``engine``
  loads rows and caches, ``shadow`` lists where v2 and the monitor above disagree.
"""

from app.services.health.cause import cause_for, snapshot_from_segment
from app.services.health.monitor import HealthMonitor, monitor
from app.services.health.recorder import recorder
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
    "recorder",
    "snapshot_from_segment",
    "summarize",
]
