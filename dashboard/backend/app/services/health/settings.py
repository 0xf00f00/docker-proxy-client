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


def _names(env: str, default: str) -> tuple[str, ...]:
    return tuple(n.strip() for n in (os.environ.get(env) or default).split(",") if n.strip())


SAMPLE_RETENTION_DAYS = 35
EVENT_RETENTION_DAYS = 400
MONITOR_GAP_S = 180

# "route=edge,..." — the one place that says which sni-spoofing instance carries each Clash
# route; the edge and core container lists below derive from it unless overridden.
PATH_EDGES = dict(
    pair.split("=", 1)
    for pair in _names("HEALTH_PATH_EDGES", "xray=sni-spoofing,xray-fallback=sni-spoofing-fallback")
    if "=" in pair
)
EDGE_CONTAINERS = _names("HEALTH_EDGE_CONTAINERS", "") or tuple(dict.fromkeys(PATH_EDGES.values()))
CORE_CONTAINERS = _names("HEALTH_CORE_CONTAINERS", "")  # empty: the controller plus every route and edge

ACTIVE_PROBES = os.environ.get("HEALTH_ACTIVE_PROBES", "on").strip().lower() not in ("off", "false", "0", "no")
PROBE_DAILY_BYTES = int(os.environ.get("HEALTH_PROBE_DAILY_BYTES", "2000000"))
GOODPUT_MAX_PER_DAY = int(os.environ.get("HEALTH_GOODPUT_MAX_PER_DAY", "1"))
BURST_MAX_PER_DAY = int(os.environ.get("HEALTH_BURST_MAX_PER_DAY", "4"))
CAPACITY_DOWN_BPS = float(os.environ.get("HEALTH_CAPACITY_DOWN_MBIT", "16")) * 1e6 / 8
CAPACITY_UP_BPS = float(os.environ.get("HEALTH_CAPACITY_UP_MBIT", "3")) * 1e6 / 8

SPEED_HOST = os.environ.get("HEALTH_SPEED_HOST", "speed.cloudflare.com")  # burst + goodput target
STUN_HOST = os.environ.get("HEALTH_STUN_HOST", "stun.l.google.com")
STUN_PORT = int(os.environ.get("HEALTH_STUN_PORT", "19302"))
