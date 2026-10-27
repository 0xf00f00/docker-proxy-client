"""Constants for the health monitor and the shared status type."""

import os
from typing import Literal, get_args

PROBE_URL = "http://www.gstatic.com/generate_204"
PROBE_TIMEOUT = 8.0
INTERVAL_OK_S = 120.0
INTERVAL_BAD_S = 30.0
DEGRADED_LATENCY_MS = 2500.0
# A longer gap between samples means the monitor itself was down: "unknown", never an outage.
MAX_GAP_S = 600.0
RETENTION_DAYS = 90
PRUNE_EVERY = 60  # samples
# Health is stored as one row per state change; the open row's end is persisted only this often.
# Keep it below MAX_GAP_S or a stale heartbeat reads as an unknown gap.
HEARTBEAT_S = 300.0

Status = Literal["good", "degraded", "outage", "unknown"]
STATUSES: tuple[Status, ...] = get_args(Status)

SAMPLE_RETENTION_DAYS = 35
EVENT_RETENTION_DAYS = 400
MONITOR_GAP_S = 180

# Clash route -> the sni-spoofing instance that carries it.
PATH_EDGES = {"xray": "sni-spoofing", "xray-fallback": "sni-spoofing-fallback"}
EDGE_CONTAINERS = tuple(dict.fromkeys(PATH_EDGES.values()))

# Active probes spend metered data; this is the one switch operators get.
ACTIVE_PROBES = os.environ.get("HEALTH_ACTIVE_PROBES", "on").strip().lower() not in ("off", "false", "0", "no")
PROBE_DAILY_BYTES = 2_000_000
GOODPUT_MAX_PER_DAY = 1
BURST_MAX_PER_DAY = 4
CAPACITY_DOWN_BPS = 16e6 / 8
CAPACITY_UP_BPS = 3e6 / 8

SPEED_HOST = "speed.cloudflare.com"
STUN_HOST = "stun.l.google.com"
STUN_PORT = 19302
