"""Tunables for the health monitor (env-overridable) and the shared status type."""

import os
from typing import Literal, get_args

PROBE_URL = os.environ.get("HEALTH_PROBE_URL", "http://www.gstatic.com/generate_204")
PROBE_TIMEOUT = 8.0
# Conservative when healthy; tighten during trouble so outage start/end are sharp.
INTERVAL_OK_S = float(os.environ.get("HEALTH_INTERVAL_OK_S", "120"))
INTERVAL_BAD_S = float(os.environ.get("HEALTH_INTERVAL_BAD_S", "30"))
# A working but sluggish path is "degraded" rather than down.
DEGRADED_LATENCY_MS = float(os.environ.get("HEALTH_DEGRADED_LATENCY_MS", "2500"))
# Longer than this between samples means the monitor itself was down: that span is
# "unknown" (grey), never counted as an outage.
MAX_GAP_S = float(os.environ.get("HEALTH_MAX_GAP_S", "600"))
RETENTION_DAYS = 90
PRUNE_EVERY = 60  # samples
# Health is a step function, so we store one row per state change (see store.health_segment),
# not one per probe. While a state holds we just extend the open row's end — a heartbeat
# persisted only every HEARTBEAT_S — so a steady hour costs a couple of writes, not ~30.
# Keep this below MAX_GAP_S: a stale heartbeat older than that reads as an unknown gap.
HEARTBEAT_S = float(os.environ.get("HEALTH_HEARTBEAT_S", "300"))

Status = Literal["good", "degraded", "outage", "unknown"]
STATUSES: tuple[Status, ...] = get_args(Status)
