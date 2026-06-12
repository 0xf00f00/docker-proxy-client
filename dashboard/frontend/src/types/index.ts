export interface AuthStatus {
  enabled: boolean;
  authenticated: boolean;
}

export interface DashboardLabels {
  enable: boolean;
  category: string;
  name: string;
  protocol: string;
  port: number | null;
  network: string;
  config: string | null;
  widget: string | null;
  testable: boolean;
  env: string[];
  controller: string | null;
}

export interface ServiceEnv {
  keys: string[];
  values: Record<string, string>;
}

export interface ServiceUpdateResult {
  success: boolean;
  applied: boolean;
  message: string;
}

export interface ContainerInfo {
  id: string;
  name: string;
  image: string;
  status: string;
  health: string | null;
  started_at: string | null;
  dashboard: DashboardLabels;
  lan_address: string | null;
  probe_address: string | null;
}

export interface ContainerListResponse {
  containers: ContainerInfo[];
  host_lan_ip: string;
}

export interface EdgeSurvival {
  checked: boolean;
  survived: boolean | null;
  fail_rate: number;
  fails: number;
  probes: number;
  skipped: string | null;
  error: string | null;
}

export interface EdgeTest {
  sent: number;
  received: number;
  loss: number;
  latency_ms: number;
  ts: string;
  survival?: EdgeSurvival | null;
}

export interface EdgeTestResponse {
  success: boolean;
  message: string;
  pending: boolean;
}

export interface ScannerStatus {
  scanner_running: boolean;
  scanner_api_reachable: boolean;
  scanning: boolean;
  picker_running: boolean;
  last_scan: string | null;
  pool: string[];
  byedpi_ip: string | null;
  snispoof_ip: string | null;
  tests: Record<string, EdgeTest>;
  testing_ip: string | null;
  test_pending: boolean;
}

export interface DnsResolver {
  ip: string;
  up_mtu: number;
  down_mtu: number;
  edns_max: number;
  loss_pct: number;
}

export interface DnsScannerStatus {
  scanner_running: boolean;
  api_reachable: boolean;
  state: string;
  scanning: boolean;
  paused: boolean;
  working_count: number;
  working: DnsResolver[];
  run_started: string | null;
  phase: string;
  candidates: number;
  probed: number;
  accepted: number;
  target_n: number;
  last_run: string | null;
  last_run_duration_sec: number;
  last_outcome: string;
  next_scan: string | null;
  interval_days: number;
  history_count: number;
}

export interface IpInfo {
  ip: string;
  country_code: string | null;
  country_name: string | null;
  flag_emoji: string | null;
  city: string | null;
  asn: string | null;
  isp: string | null;
}

export interface ConnectivityResult {
  service: string;
  success: boolean;
  latency_ms: number | null;
  status_code: number | null;
  error: string | null;
  tested_via: string;
  ip_info: IpInfo | null;
  /** ISO-8601 UTC timestamp of when this result was produced; null if un-cached. */
  tested_at: string | null;
}

/** Last-known results from the shared backend cache, plus whether anything is
 *  stale/missing. */
export interface ConnectivityResults {
  results: ConnectivityResult[];
  stale: boolean;
}

export type NetworkRegime = "normal" | "dpi_degraded" | "iran_only" | "total_outage" | "unknown";

export interface RegimeInfo {
  regime: NetworkRegime;
  intl_up: boolean;
  direct_goodput_mbps: number | null;
  detail: string;
}

export type StabilityGrade = "good" | "degraded" | "bad" | "inconclusive";

/** Live progress while the stability probe runs (mirrors backend emit phases). */
export interface StabilityProgress {
  phase: "regime" | "idle" | "load" | "longlived" | "udp";
  /** Long-lived phase only: the fixed hold's length (s). Sent once; the client
   *  runs the visible countdown locally (a streamed per-tick value can freeze if
   *  SSE chunks buffer mid-stream). */
  total_s?: number;
}

export interface StabilityResult {
  service: string;
  bulk_grade: StabilityGrade;
  call_grade: StabilityGrade;
  tested_via: string;
  regime: RegimeInfo;
  streams: number;
  completed: number;
  resets: number;
  stalls: number;
  reset_rate: number;
  stall_rate: number;
  idle_p50_ms: number | null;
  idle_p95_ms: number | null;
  loaded_p50_ms: number | null;
  loaded_p95_ms: number | null;
  loaded_max_ms: number | null;
  loaded_jitter_ms: number | null;
  loaded_loss_pct: number | null;
  loaded_spike_pct: number | null;
  loaded_samples: number;
  latency_inflation: number | null;
  longlived_held: number;
  longlived_survived: number;
  longlived_min_ttl_s: number | null;
  udp_supported: boolean | null;
  udp_detail: string;
  summary: string;
  reasons: string[];
  error: string | null;
}

export interface ConfigFile {
  content: string;
  filename: string;
  language: string;
}

export type SystemProxyMode = "auto" | "manual";

export interface SystemProxyRoute {
  name: string;
  latency_ms: number | null;
}

export interface SystemProxyState {
  mode: SystemProxyMode;
  routes: SystemProxyRoute[];
  active: string | null;
  reorderable: boolean;
}

export interface SystemProxyReorderResult {
  success: boolean;
  routes: string[];
  active: string | null;
}

export interface SystemDnsResult {
  success: boolean;
  hostname: string;
  resolved_ip?: string;
  error?: string;
  latency_ms: number;
}

export interface SystemConnectivityResult {
  success: boolean;
  latency_ms: number;
  status_code?: number;
  error?: string;
}

export interface SystemHealthResult {
  dns: SystemDnsResult;
  connectivity: SystemConnectivityResult;
  /** Whether per-host connection tracking (live + usage) is enabled (env opt-in). */
  connection_tracking: boolean;
}

export type UsagePeriod = "today" | "week" | "month" | "all";

/** One domain's data usage over a period. Bytes are cumulative; share is 0..1. */
export interface UsageSite {
  domain: string;
  down: number;
  up: number;
  share: number;
}

/** One bar of the usage trend chart: bucket start (unix) and combined bytes. */
export interface UsageBucket {
  ts: number;
  value: number;
}

/** Top data consumers for a period, with the period grand total and trend. */
export interface UsageReport {
  period: UsagePeriod;
  since: number;
  until: number;
  updatedAt: number;
  totalDown: number;
  totalUp: number;
  sites: UsageSite[];
  series: UsageBucket[];
}

/** A live throughput snapshot. All rates are bytes/second. */
export interface TrafficSnapshot {
  ts: number;
  system: { up: number; down: number };
  proxies: Record<string, number>;
}

/** One open socket within a site group. Cumulative bytes; rates are bytes/second. */
export interface ConnectionDetail {
  id: string;
  down: number;
  up: number;
  downRate: number;
  upRate: number;
  network: string;
  dest: string;
  port: string;
  exit: string;
  rule: string;
  since: string;
}

/** All connections to one website, aggregated. */
export interface ConnectionSite {
  host: string;
  count: number;
  down: number;
  up: number;
  downRate: number;
  upRate: number;
  /** The proxy this site egresses through (most-used among its connections). */
  exit: string;
  /** ISO timestamp of the oldest connection in the group. */
  since: string;
  connections: ConnectionDetail[];
}

/** A live snapshot of all active connections, grouped by website. */
export interface ConnectionsSnapshot {
  ts: number;
  /** Total open connections across all sites. */
  count: number;
  totals: { down: number; up: number; downRate: number; upRate: number };
  sites: ConnectionSite[];
  /** Number of sites dropped past the server-side cap (0 in normal use). */
  truncated: number;
}

export interface SpeedTestProgress {
  phase: "init" | "server" | "download" | "upload" | "done" | "cancelled" | "error";
  download_mbps: number | null;
  upload_mbps: number | null;
  ping_ms: number | null;
  server: string | null;
  error: string | null;
}

export interface SystemSpeedResult {
  download_mbps: number | null;
  upload_mbps: number | null;
  server?: string;
  ping_ms?: number;
  error?: string;
}
