"""Dashboard-side proxy for the dns-scanner Go control API."""

import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.models.schemas import DnsResolver, DnsScannerStatus
from app.services import docker_service

_CONTAINER = "dns-scanner"

# The scanner's Go control API + the shared secret it gates every endpoint with.
_API_BASE = os.environ.get("DASHBOARD_DNS_SCANNER_API_URL", "http://127.0.0.1:8088")
_API_SECRET = os.environ.get("DASHBOARD_DNS_SCANNER_SECRET", "")
_API_TIMEOUT = 2.0
# SSE read timeout: must exceed the scanner's heartbeat (15s) so a healthy idle
# stream isn't torn down; a longer gap means the connection is dead -> reconnect.
_EVENT_READ_TIMEOUT = 30.0

# Degraded fallback: when the API is unreachable, the scanner-managed block of the
# resolver file (mounted into the dashboard at /compose/mdns/...) still tells us how
# many resolvers are live. Markers must match dns-scanner/internal/assign/assign.go.
_RESOLVERS_FILE = Path(settings.compose_project_path) / "mdns" / "client_resolvers.txt"
_MANAGED_BEGIN = "# >>> mdns-scanner managed (do not edit this block) >>>"
_MANAGED_END = "# <<< mdns-scanner managed <<<"


def _running() -> bool:
    try:
        return docker_service.get_client().containers.get(_CONTAINER).status == "running"
    except Exception:
        return False


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    h = dict(extra or {})
    if _API_SECRET:
        h["X-Scanner-Secret"] = _API_SECRET
    return h


def _api_get(path: str) -> dict | None:
    req = urllib.request.Request(f"{_API_BASE}{path}", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            return json.load(resp)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _api_post(path: str) -> None:
    req = urllib.request.Request(f"{_API_BASE}{path}", data=b"", method="POST", headers=_headers())
    with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
        resp.read()


def iter_events():
    """Block on the scanner's SSE event stream, yielding once per pushed event.

    Used purely as a change signal -- the caller re-reads merged status via
    get_status(). Returns (or raises) on disconnect; the caller reconnects.
    """
    headers = _headers({"Accept": "text/event-stream"})
    req = urllib.request.Request(f"{_API_BASE}/scan/events", headers=headers)
    with urllib.request.urlopen(req, timeout=_EVENT_READ_TIMEOUT) as resp:
        for raw in resp:
            if raw.startswith(b"data:"):
                yield


def _dt(unix: object) -> datetime | None:
    """Convert a Unix seconds value (0/None == unset) to a UTC datetime."""
    try:
        n = int(unix)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(n, tz=UTC) if n > 0 else None


def _resolvers_from_api(raw: object) -> list[DnsResolver]:
    if not isinstance(raw, list):
        return []
    out: list[DnsResolver] = []
    for r in raw:
        if not isinstance(r, dict) or not r.get("ip"):
            continue
        try:
            out.append(
                DnsResolver(
                    ip=str(r["ip"]),
                    up_mtu=int(r.get("up_mtu", 0)),
                    down_mtu=int(r.get("down_mtu", 0)),
                    edns_max=int(r.get("edns_max", 0)),
                    loss_pct=int(r.get("loss_pct", 0)),
                )
            )
        except (ValueError, TypeError):
            continue
    return out


def _managed_resolvers() -> list[str]:
    """Resolver IPs in the scanner-managed block of client_resolvers.txt (the
    degraded source when the control API is unreachable)."""
    try:
        lines = _RESOLVERS_FILE.read_text().splitlines()
    except OSError:
        return []
    out: list[str] = []
    inside = False
    for line in lines:
        t = line.strip()
        if t == _MANAGED_BEGIN:
            inside = True
        elif t == _MANAGED_END:
            inside = False
        elif inside and t and not t.startswith("#"):
            out.append(t.split()[0])
    return out


def get_status() -> DnsScannerStatus:
    snap = _api_get("/scan")
    running = _running()

    if snap is not None:
        working = _resolvers_from_api(snap.get("working"))
        return DnsScannerStatus(
            scanner_running=running,
            api_reachable=True,
            state=str(snap.get("state", "idle")),
            scanning=bool(snap.get("scanning", False)),
            paused=bool(snap.get("paused", False)),
            working_count=int(snap.get("working_count", len(working))),
            working=working,
            run_started=_dt(snap.get("run_started_unix")),
            phase=str(snap.get("phase", "")),
            candidates=int(snap.get("candidates", 0)),
            probed=int(snap.get("probed", 0)),
            accepted=int(snap.get("accepted", 0)),
            target_n=int(snap.get("target_n", 0)),
            last_run=_dt(snap.get("last_run_unix")),
            last_run_duration_sec=int(snap.get("last_run_duration_sec", 0)),
            last_outcome=str(snap.get("last_outcome", "")),
            next_scan=_dt(snap.get("next_scan_unix")),
            interval_days=int(snap.get("interval_days", 0)),
            history_count=int(snap.get("history_count", 0)),
        )

    # API unreachable: degrade off the only persisted artifact (the managed block
    # of the resolver file). Live state (scanning/progress/schedule) is unknown.
    managed = _managed_resolvers()
    return DnsScannerStatus(
        scanner_running=running,
        api_reachable=False,
        working_count=len(managed),
        working=[DnsResolver(ip=ip) for ip in managed],
    )


def trigger_scan() -> None:
    _api_post("/scan/start")


def pause_scan() -> None:
    _api_post("/scan/pause")


def resume_scan() -> None:
    _api_post("/scan/resume")


def stop_scan() -> None:
    _api_post("/scan/stop")
