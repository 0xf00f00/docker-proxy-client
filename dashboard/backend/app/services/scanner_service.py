import ipaddress
import json
import os
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.models.schemas import EdgeSurvival, EdgeTest, ScannerStatus
from app.services import docker_service

# The scanner's Go control API
_API_BASE = os.environ.get("DASHBOARD_SCANNER_API_URL", "http://127.0.0.1:8088")
_API_TIMEOUT = 2.0
# SSE read timeout: must exceed the scanner's heartbeat (15s) so a healthy idle
# stream isn't torn down; a longer gap means the connection is dead -> reconnect.
_EVENT_READ_TIMEOUT = 30.0

_BASE = Path(settings.compose_project_path)
_POOL = _BASE / "cf-edge-manager" / "out" / "pool.txt"
_BYEDPI_HOSTS = _BASE / "coredns" / "fallback" / "edge.hosts"
_SNISPOOF_CONF = _BASE / "sni-spoofing-fallback" / "config.ini"

_CONNECT_RE = re.compile(r"^\s*connect\s*=\s*([^:\s]+)", re.MULTILINE)


def _running(name: str) -> bool:
    try:
        return docker_service.get_client().containers.get(name).status == "running"
    except Exception:
        return False


def _first_token(path: Path) -> str | None:
    try:
        for line in path.read_text().splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                return s.split()[0]
    except OSError:
        pass
    return None


def _pool() -> list[str]:
    try:
        lines = _POOL.read_text().splitlines()
    except OSError:
        return []
    return [s for line in lines if (s := line.strip()) and not s.startswith("#")]


def _snispoof_ip() -> str | None:
    try:
        match = _CONNECT_RE.search(_SNISPOOF_CONF.read_text())
    except OSError:
        return None
    return match.group(1) if match else None


def _last_scan() -> datetime | None:
    # pool.txt's mtime: it's rewritten atomically only on a successful scan.
    try:
        return datetime.fromtimestamp(_POOL.stat().st_mtime, tz=UTC)
    except OSError:
        return None


def _api_get(path: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{_API_BASE}{path}", timeout=_API_TIMEOUT) as resp:
            return json.load(resp)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _api_post(path: str, body: dict | None = None) -> dict | None:
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(
        f"{_API_BASE}{path}",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
        raw = resp.read()
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def iter_events():
    """Block on the scanner's SSE event stream, yielding once per pushed event.

    Used purely as a change signal -- the caller re-reads merged status via
    get_status(). Returns (or raises) on disconnect; the caller reconnects.
    """
    req = urllib.request.Request(f"{_API_BASE}/events", headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=_EVENT_READ_TIMEOUT) as resp:
        for raw in resp:
            if raw.startswith(b"data:"):
                yield


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _survival_from_api(raw: object) -> EdgeSurvival | None:
    if not isinstance(raw, dict):
        return None
    try:
        survived = raw.get("survived")
        return EdgeSurvival(
            checked=bool(raw.get("checked", False)),
            survived=bool(survived) if survived is not None else None,
            fail_rate=float(raw.get("fail_rate", 0.0)),
            fails=int(raw.get("fails", 0)),
            probes=int(raw.get("probes", 0)),
            skipped=raw.get("skipped") or None,
            error=raw.get("error") or None,
        )
    except (ValueError, TypeError):
        return None


def _tests_from_api(raw: dict) -> dict[str, EdgeTest]:
    out: dict[str, EdgeTest] = {}
    for ip, r in raw.items():
        ts = _parse_dt(r.get("ts"))
        if ts is None:
            continue
        try:
            out[ip] = EdgeTest(
                sent=int(r["sent"]),
                received=int(r["received"]),
                loss=float(r["loss"]),
                latency_ms=float(r["latency_ms"]),
                ts=ts,
                survival=_survival_from_api(r.get("survival")),
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out


def get_status() -> ScannerStatus:
    snap = _api_get("/status")
    reachable = snap is not None
    if reachable:
        scanning = bool(snap.get("scanning", False))
        last_scan = _parse_dt(snap.get("last_scan"))
        pool = snap.get("pool") or []
        tests = _tests_from_api(snap.get("tests") or {})
        testing_ip = snap.get("testing_ip") or None
        test_pending = bool(snap.get("test_pending", False))
    else:
        # API unreachable: degrade off the only persisted artifacts (pool +
        # last_scan). Live state (scanning/tests/testing) is simply shown idle.
        scanning = False
        last_scan = _last_scan()
        pool = _pool()
        tests = {}
        testing_ip = None
        test_pending = False

    return ScannerStatus(
        scanner_running=_running("cf-edge-manager"),
        scanner_api_reachable=reachable,
        scanning=scanning,
        picker_running=_running("cf-edge-manager"),
        last_scan=last_scan,
        pool=pool,
        byedpi_ip=_first_token(_BYEDPI_HOSTS),
        snispoof_ip=_snispoof_ip(),
        tests=tests,
        testing_ip=testing_ip,
        test_pending=test_pending,
    )


def trigger_scan() -> None:
    _api_post("/scans")


def cancel_scan() -> None:
    _api_post("/scans/cancel")


def trigger_test(ip: str) -> bool:
    """Enqueue an interactive probe of `ip`.

    Returns True when a fresh probe is actually in flight (the dashboard should
    keep its spinner until a new result streams in), and False when the scanner
    served a cached result within its cooldown -- in that case no probe runs and
    no further state change is coming, so the caller must stop waiting and just
    show the result already present in get_status().
    """
    # Validate before it reaches the scanner's `cfst -ip <ip>` (injection guard).
    ipaddress.ip_address(ip)
    resp = _api_post("/tests", {"ip": ip})
    job_id = (resp or {}).get("job_id")
    if not job_id:
        return False
    # A cooldown-reused job is already terminal (done/failed); a freshly
    # enqueued one is queued or running.
    job = _api_get(f"/jobs/{job_id}") or {}
    return job.get("state") in ("queued", "running")
