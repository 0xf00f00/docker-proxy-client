import ipaddress
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models.schemas import EdgeTest, ScannerStatus
from app.services import docker_service

_BASE = Path(settings.compose_project_path)
_POOL = _BASE / "cf-edge-scanner" / "out" / "pool.txt"
_LAST_SCAN = _BASE / "cf-edge-scanner" / "out" / ".last-scan"
_TRIGGER = _BASE / "cf-edge-scanner" / "out" / ".scan-now"
_SCANNING = _BASE / "cf-edge-scanner" / "out" / ".scanning"
_TEST_REQ = _BASE / "cf-edge-scanner" / "out" / ".test-request"
_TESTING = _BASE / "cf-edge-scanner" / "out" / ".testing"
_TEST_RESULTS = _BASE / "cf-edge-scanner" / "out" / "test-results.txt"
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
    try:
        return datetime.fromtimestamp(int(_LAST_SCAN.read_text().strip()), tz=timezone.utc)
    except (OSError, ValueError):
        return None


def _tests() -> dict[str, EdgeTest]:
    # one line per IP: "<ip> <sent> <received> <loss> <latency_ms> <epoch>"
    out: dict[str, EdgeTest] = {}
    try:
        lines = _TEST_RESULTS.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) != 6:
            continue
        ip, sent, recv, loss, lat, ts = parts
        try:
            out[ip] = EdgeTest(
                sent=int(sent),
                received=int(recv),
                loss=float(loss),
                latency_ms=float(lat),
                ts=datetime.fromtimestamp(int(ts), tz=timezone.utc),
            )
        except ValueError:
            continue
    return out


def _testing_ip() -> str | None:
    try:
        return _TESTING.read_text().strip() or None
    except OSError:
        return None


def get_status() -> ScannerStatus:
    return ScannerStatus(
        scanner_running=_running("cf-edge-scanner"),
        scanning=_SCANNING.exists(),
        picker_running=_running("cf-edge-picker"),
        last_scan=_last_scan(),
        pool=_pool(),
        byedpi_ip=_first_token(_BYEDPI_HOSTS),
        snispoof_ip=_snispoof_ip(),
        tests=_tests(),
        testing_ip=_testing_ip(),
    )


def trigger_scan() -> None:
    _TRIGGER.parent.mkdir(parents=True, exist_ok=True)
    _TRIGGER.write_text("")


def trigger_test(ip: str) -> None:
    # Validate before it reaches the scanner's `cfst -ip <ip>` (injection guard).
    ipaddress.ip_address(ip)
    _TEST_REQ.parent.mkdir(parents=True, exist_ok=True)
    _TEST_REQ.write_text(ip)
