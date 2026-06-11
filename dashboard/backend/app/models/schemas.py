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
    # When this result was produced (UTC). Lets the dashboard show "tested N ago"
    # and skip re-probing still-fresh results on page load. None for an un-cached
    # / legacy result.
    tested_at: datetime | None = None


class ConnectivityResultsResponse(BaseModel):
    """Last-known connectivity results from the shared backend cache (no probing),
    plus a hint on whether any testable proxy is missing a result or stale enough
    that an auto-probe on page load is warranted."""

    results: list[ConnectivityResult]
    stale: bool


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
    """Outcome of the stability probe (see docs/realtime-stability-repro.md).

    Grades two things users experience separately: bulk-transfer survival under
    many parallel streams (downloads / docker pull) and real-time call quality
    under load (loaded-latency spikes + jitter, what governs Google Meet).
    Regime-gated: inconclusive during an Iran-only blackout.
    """

    service: str
    # good | degraded | bad | inconclusive
    bulk_grade: str
    call_grade: str
    tested_via: str
    regime: RegimeInfo

    # Bulk: parallel-stream survival.
    streams: int = 0
    completed: int = 0
    resets: int = 0
    stalls: int = 0
    reset_rate: float = 0.0
    stall_rate: float = 0.0

    # Call: latency under load, measured on one warm connection (idle + loaded
    # samples are like-for-like). Calls freeze on tail spikes (>1s), so the spike
    # fraction and p95-inflation are the signals.
    idle_p50_ms: float | None = None
    idle_p95_ms: float | None = None
    loaded_p50_ms: float | None = None
    loaded_p95_ms: float | None = None
    loaded_max_ms: float | None = None
    loaded_jitter_ms: float | None = None
    loaded_loss_pct: float | None = None
    loaded_spike_pct: float | None = None
    loaded_samples: int = 0  # pings collected under load (small → call inconclusive)
    latency_inflation: float | None = None  # loaded_p95 / idle_p95

    # Long-lived session survival (DPI often resets long/idle connections).
    longlived_held: int = 0
    longlived_survived: int = 0
    longlived_min_ttl_s: float | None = None

    # UDP/WebRTC reachability over the proxy. None = couldn't tell. False = calls
    # fall back to a slower TCP relay (HTTP proxy, or UDP blocked on the path).
    udp_supported: bool | None = None
    udp_detail: str = ""

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


class EdgeSurvival(BaseModel):
    """Real-path (DPI-survival) verdict. `survived` is None when inconclusive;
    `checked` is False when the probe didn't run (`skipped` says why)."""

    checked: bool = False
    survived: bool | None = None
    fail_rate: float = 0.0
    fails: int = 0
    probes: int = 0
    skipped: str | None = None
    error: str | None = None


class EdgeTest(BaseModel):
    sent: int
    received: int
    loss: float
    latency_ms: float
    ts: datetime
    survival: EdgeSurvival | None = None


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


class DnsResolver(BaseModel):
    """A working MasterDnsVPN resolver the scanner certified. MTU/EDNS fields are
    internal detail — the dashboard surfaces `ip` and grades health off `loss_pct`."""

    ip: str
    up_mtu: int = 0
    down_mtu: int = 0
    edns_max: int = 0
    loss_pct: int = 0


class DnsScannerStatus(BaseModel):
    """Merged view of the dns-scanner service: its Go control-API snapshot plus the
    container's running state. Unix timestamps are converted to UTC datetimes."""

    scanner_running: bool
    # False when the scanner's control API is unreachable (status then degrades to
    # whatever the on-disk resolver file reveals). Distinct from scanner_running.
    api_reachable: bool = True
    # idle | scanning | paused | stopping
    state: str = "idle"
    scanning: bool = False
    paused: bool = False
    working_count: int = 0
    working: list[DnsResolver] = []
    # Live progress during a run: `accepted` resolvers found of `target_n`, with
    # `probed` of `candidates` IPs checked so far since `run_started`. `phase` is
    # which leg is running ("verify" = re-checking known resolvers, "sweep" = new).
    run_started: datetime | None = None
    phase: str = ""
    candidates: int = 0
    probed: int = 0
    accepted: int = 0
    target_n: int = 0
    last_run: datetime | None = None
    last_run_duration_sec: int = 0
    last_outcome: str = ""
    next_scan: datetime | None = None
    interval_days: int = 0
    history_count: int = 0


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
