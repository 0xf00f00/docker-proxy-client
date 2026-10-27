"""Dashboard-side proxy for the dns-scanner Go control API."""

import os
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.models.schemas import (
    DnsFunnel,
    DnsResolver,
    DnsScannerStatus,
)
from app.services.scanner_api import ScannerApi

_api = ScannerApi(
    os.environ.get("DASHBOARD_DNS_SCANNER_API_URL", "http://127.0.0.1:8088"),
    "dns-scanner",
    secret=os.environ.get("DASHBOARD_DNS_SCANNER_SECRET", ""),
    events_path="/scan/events",
)
iter_events = _api.events

# Degraded fallback when the API is unreachable. Markers must match dns-scanner/internal/assign/assign.go.
_RESOLVERS_FILE = Path(settings.compose_project_path) / "mdns" / "client_resolvers.txt"
_MANAGED_BEGIN = "# >>> mdns-scanner managed (do not edit this block) >>>"
_MANAGED_END = "# <<< mdns-scanner managed <<<"


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
                    backup=bool(r.get("backup", False)),
                )
            )
        except (ValueError, TypeError):
            continue
    return out


def _funnel_from_api(raw: object) -> DnsFunnel:
    if not isinstance(raw, dict):
        return DnsFunnel()
    try:
        return DnsFunnel(
            probed=int(raw.get("probed", 0)),
            alive=int(raw.get("alive", 0)),
            nx=int(raw.get("nx", 0)),
            forward=int(raw.get("forward", 0)),
            edns=int(raw.get("edns", 0)),
            upload=int(raw.get("upload", 0)),
            gates=int(raw.get("gates", 0)),
            cert=int(raw.get("cert", 0)),
        )
    except (ValueError, TypeError):
        return DnsFunnel()


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
    snap = _api.get("/scan")
    running = _api.running()

    if snap is not None:
        working = _resolvers_from_api(snap.get("working"))
        return DnsScannerStatus(
            container=_api.container,
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
            funnel=_funnel_from_api(snap.get("funnel")),
            backup_count=int(snap.get("backup_count", 0)),
        )

    # API unreachable: degrade off the only persisted artifact (the managed block
    # of the resolver file). Live state (scanning/progress/schedule) is unknown.
    managed = _managed_resolvers()
    return DnsScannerStatus(
        container=_api.container,
        scanner_running=running,
        api_reachable=False,
        working_count=len(managed),
        working=[DnsResolver(ip=ip) for ip in managed],
    )


def trigger_scan() -> None:
    _api.post("/scan/start")


def pause_scan() -> None:
    _api.post("/scan/pause")


def resume_scan() -> None:
    _api.post("/scan/resume")


def stop_scan() -> None:
    _api.post("/scan/stop")
