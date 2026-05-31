from datetime import datetime

from pydantic import BaseModel


class DashboardLabels(BaseModel):
    enable: bool = False
    category: str = "infra"
    name: str = ""
    protocol: str = ""
    # The LAN port a user connects to (NOT the container-internal port).
    # The host side is derived from Docker's live network config — see
    # services/docker_service.py::resolve_lan_address.
    port: int | None = None
    config: str | None = None
    widget: str | None = None
    testable: bool = True
    env: list[str] = []
    # Name of the registered SystemProxyController this container implements.
    # The system-proxy widget asks the registry for this name to find the
    # active controller. See services/system_proxy/registry.py.
    controller: str | None = None


class ContainerInfo(BaseModel):
    id: str
    name: str
    image: str
    status: str
    health: str | None = None
    started_at: str | None = None
    dashboard: DashboardLabels
    # Address users dial from other LAN devices. May be a macvlan IP that the
    # dashboard backend itself cannot reach (host ↔ macvlan is blocked).
    lan_address: str | None = None
    # Address the dashboard backend uses for connectivity probes. Same as
    # lan_address except macvlan IPs are swapped for the bridge IP, which is
    # reachable from the host network. See docker_service.resolve_probe_address.
    probe_address: str | None = None


class ContainerListResponse(BaseModel):
    containers: list[ContainerInfo]
    host_lan_ip: str


class IpInfo(BaseModel):
    ip: str
    country_code: str | None = None
    country_name: str | None = None
    flag_emoji: str | None = None
    city: str | None = None
    asn: str | None = None
    isp: str | None = None


class ConnectivityResult(BaseModel):
    service: str
    success: bool
    latency_ms: float | None = None
    status_code: int | None = None
    error: str | None = None
    tested_via: str
    ip_info: IpInfo | None = None


class RegimeInfo(BaseModel):
    """Network-regime classification for a stability batch (see
    docs/proxy-stability-detection.md §4a). Decides whether a proxy verdict is
    even meaningful right now, and whether a direct international baseline is
    usable."""

    # normal | dpi_degraded | iran_only | total_outage | unknown
    regime: str
    # Is the direct international path usable as a throughput baseline? False
    # during Iran-only blackouts / outages — proxy grades are then suppressed.
    intl_up: bool
    direct_goodput_mbps: float | None = None
    detail: str = ""


class StabilityResult(BaseModel):
    """Outcome of an active stability probe for one proxy (§3-§4).

    Unlike ConnectivityResult (one pass/fail + mean latency) this samples the
    *distribution*: connection-establishment reliability, mid-stream survival,
    goodput shape, and the latency tail — then grades it.
    """

    service: str
    # good | degraded | bad | inconclusive
    grade: str
    tested_via: str
    regime: RegimeInfo
    # Signal 1 — connection-establishment reliability.
    attempts: int
    ok: int
    resets: int
    timeouts: int
    other_errors: int
    failure_rate: float
    # Wilson lower bound on the failure rate (small samples can't over-trigger).
    failure_rate_lower: float
    # Signal 4 — latency tail (of successful connects), not the mean.
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    # Signal 3 — goodput shape (MB/s). `goodput_completed` doubles as Signal 2
    # (mid-stream survival): a download that drops part-way is a survival failure.
    goodput_mbps: float | None = None
    goodput_peak_mbps: float | None = None
    goodput_completed: bool = False
    stalled: bool = False
    decayed: bool = False
    # P / gstatic, only when the direct international baseline was usable.
    direct_ratio: float | None = None
    summary: str = ""
    reasons: list[str] = []
    error: str | None = None


class ConfigFile(BaseModel):
    content: str
    filename: str
    language: str


class ConfigUpdate(BaseModel):
    content: str


class EnvUpdateRequest(BaseModel):
    values: dict[str, str]


class SystemProxyRoute(BaseModel):
    name: str
    latency_ms: int | None = None


class SystemProxyState(BaseModel):
    # "auto" = controller picks the best route. "manual" = user picks one.
    mode: str
    # Available routes for the *current* mode. In auto mode this is the
    # priority-ordered list; in manual mode it's the full selectable list.
    routes: list[SystemProxyRoute]
    active: str | None = None
    # When true, callers can reorder routes (auto mode in most controllers).
    reorderable: bool = False


class SystemProxyModeRequest(BaseModel):
    mode: str


class SystemProxySwitchRequest(BaseModel):
    name: str


class SystemProxyReorderRequest(BaseModel):
    routes: list[str]


class SystemProxyReorderResult(BaseModel):
    success: bool
    routes: list[str]
    active: str | None = None


class ContainerActionResponse(BaseModel):
    success: bool
    message: str


class EdgeTest(BaseModel):
    sent: int
    received: int
    loss: float
    latency_ms: float
    ts: datetime


class ScannerStatus(BaseModel):
    scanner_running: bool
    # False when the scanner's control API is unreachable (status is then served
    # from the on-disk fallback). Distinct from scanner_running (container up).
    scanner_api_reachable: bool = True
    scanning: bool = False
    picker_running: bool
    last_scan: datetime | None = None
    pool: list[str] = []
    byedpi_ip: str | None = None
    snispoof_ip: str | None = None
    tests: dict[str, EdgeTest] = {}
    testing_ip: str | None = None
    test_pending: bool = False


class EdgeTestRequest(BaseModel):
    ip: str


class EdgeTestResponse(BaseModel):
    """Outcome of a single-IP test request. `pending` is True when a fresh probe
    is actually running (the UI keeps its spinner until a new result streams in)
    and False when the scanner served a cached result within its cooldown -- that
    result is already in /scanner/status, so the UI must stop waiting."""
    success: bool
    message: str
    pending: bool
