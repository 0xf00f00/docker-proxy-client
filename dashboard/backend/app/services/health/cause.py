"""Cause attribution: turn a (status, regime) pair into plain language for
non-technical users, and seed a live snapshot from a stored segment."""

import json
import socket

from app.services.health.settings import Status


def is_dns_failure(exc: BaseException) -> bool:
    """True if a probe error was a name-resolution failure (vs. a connect/HTTP
    failure). Walks the exception cause chain for socket.gaierror."""
    cur: BaseException | None = exc
    for _ in range(6):
        if cur is None:
            break
        if isinstance(cur, socket.gaierror):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def cause_for(status: str, regime: str) -> tuple[str, str]:
    """(kind, label) explaining the status. ``kind`` is a stable machine tag;
    ``label`` is what we show. Leads with *whose* problem it is."""
    if status == "good":
        return "ok", "Network healthy"
    if status == "degraded":
        return "slow", "Connection is slow"
    if status == "unknown":
        return "unknown", "Not measured"
    # outage — regime says why
    if regime == "iran_only":
        return "regime", "Country-wide block"
    if regime == "total_outage":
        return "local", "Internet appears down"
    return "proxy", "Proxy unreachable"


def detail_for(status: str, latency_ms: float | None, dns_ok: bool) -> str | None:
    """The granular "why", captured when a segment opens, as a compact JSON string (or
    None when nothing extra to say). Decoded back into prose by ``describe_detail``.

    - degraded -> the latency that tripped it, so the slowdown's severity is recoverable.
    - outage   -> whether name resolution itself failed (regime already carries the rest).
    """
    if status == "degraded" and latency_ms is not None:
        return json.dumps({"latencyMs": round(latency_ms)})
    if status == "outage" and not dns_ok:
        return json.dumps({"dns": False})
    return None


def describe_detail(status: str, regime: str, detail: str | None) -> str | None:
    """Plain-language "why" for the incident detail view, or None when the headline
    ``causeLabel`` already says everything (or no detail was captured)."""
    if not detail:
        return None
    try:
        info = json.loads(detail)
    except (ValueError, TypeError):
        return None
    if status == "degraded" and isinstance(info.get("latencyMs"), (int, float)):
        secs = info["latencyMs"] / 1000
        return f"Responses were taking about {secs:.1f}s"
    # A failed name lookup only adds signal when the regime points at the proxy/path;
    # under a country-wide block or total outage, DNS failing is expected, not the story.
    if status == "outage" and info.get("dns") is False and regime not in ("iran_only", "total_outage"):
        return "Name lookups (DNS) were failing"
    return None


def snapshot_from_segment(end_ts: int, status: Status, regime: str, detail: str | None = None) -> dict:
    """Build a ``current``-shaped dict from the stored open segment (used to seed the
    live status from disk after a restart, before the next probe runs). Latency/DNS
    aren't kept live in history, so they're omitted here — the next probe fills them in —
    but the stored ``detail`` still lets us explain *why* the last known state held."""
    kind, label = cause_for(status, regime)
    return {
        "ts": int(end_ts),
        "status": status,
        "regime": regime,
        "latencyMs": None,
        "reachable": status in ("good", "degraded"),
        "cause": kind,
        "causeLabel": label,
        "causeDetail": describe_detail(status, regime, detail),
    }
