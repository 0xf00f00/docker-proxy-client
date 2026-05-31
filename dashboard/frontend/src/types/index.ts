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

export interface EdgeTest {
  sent: number;
  received: number;
  loss: number;
  latency_ms: number;
  ts: string;
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
}

/** A live throughput snapshot. All rates are bytes/second. */
export interface TrafficSnapshot {
  ts: number;
  system: { up: number; down: number };
  proxies: Record<string, number>;
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
