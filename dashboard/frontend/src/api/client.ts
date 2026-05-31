import axios from "axios";
import type {
  AuthStatus,
  ContainerListResponse,
  ConnectivityResult,
  ConfigFile,
  IpInfo,
  SpeedTestProgress,
  SystemHealthResult,
  SystemProxyMode,
  SystemProxyReorderResult,
  SystemProxyState,
  ServiceEnv,
  ServiceUpdateResult,
  ScannerStatus,
  EdgeTestResponse,
  TrafficSnapshot,
} from "@/types";

const api = axios.create({ baseURL: "/api/v1" });

let onUnauthorized: () => void = () => {};

export function setOnUnauthorized(cb: () => void): void {
  onUnauthorized = cb;
}

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      const url = error.config?.url ?? "";
      if (!url.startsWith("/auth/")) onUnauthorized();
    }
    return Promise.reject(error);
  },
);

// ---------- Auth ----------

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const { data } = await api.get<AuthStatus>("/auth/status");
  return data;
}

export async function login(password: string): Promise<void> {
  await api.post("/auth/login", { password });
}

export async function logout(): Promise<void> {
  await api.post("/auth/logout");
}

// ---------- Containers ----------

export async function restartContainer(name: string): Promise<void> {
  await api.post(`/containers/${name}/restart`);
}

export async function startContainer(name: string): Promise<void> {
  await api.post(`/containers/${name}/start`);
}

export async function stopContainer(name: string): Promise<void> {
  await api.post(`/containers/${name}/stop`);
}

// ---------- Scanner ----------

export async function fetchScannerStatus(): Promise<ScannerStatus> {
  const { data } = await api.get<ScannerStatus>("/scanner/status");
  return data;
}

export async function runScan(): Promise<void> {
  await api.post("/scanner/run");
}

export async function cancelScan(): Promise<void> {
  await api.post("/scanner/cancel");
}

export async function testEdge(ip: string): Promise<EdgeTestResponse> {
  const { data } = await api.post<EdgeTestResponse>("/scanner/test", { ip });
  return data;
}

// ---------- Connectivity ----------

export async function testConnectivity(name: string): Promise<ConnectivityResult> {
  const { data } = await api.get<ConnectivityResult>(`/connectivity/test/${name}`);
  return data;
}

// ---------- Config / env ----------

export async function fetchConfig(name: string): Promise<ConfigFile> {
  const { data } = await api.get<ConfigFile>(`/config/${name}`);
  return data;
}

export async function saveConfig(name: string, content: string): Promise<ServiceUpdateResult> {
  const { data } = await api.put<ServiceUpdateResult>(`/config/${name}`, { content });
  return data;
}

export async function fetchServiceEnv(name: string): Promise<ServiceEnv> {
  const { data } = await api.get<ServiceEnv>(`/env/${name}`);
  return data;
}

export async function saveServiceEnv(name: string, values: Record<string, string>): Promise<ServiceUpdateResult> {
  const { data } = await api.put<ServiceUpdateResult>(`/env/${name}`, { values });
  return data;
}

// ---------- System proxy (generic — implementation-agnostic) ----------

export async function fetchSystemProxyState(): Promise<SystemProxyState> {
  const { data } = await api.get<SystemProxyState>("/system-proxy/state");
  return data;
}

export async function setSystemProxyMode(mode: SystemProxyMode): Promise<void> {
  await api.put("/system-proxy/mode", { mode });
}

export async function switchSystemProxy(name: string): Promise<void> {
  await api.put("/system-proxy/active", { name });
}

export async function reorderSystemProxy(routes: string[]): Promise<SystemProxyReorderResult> {
  const { data } = await api.put<SystemProxyReorderResult>("/system-proxy/order", { routes });
  return data;
}

export async function testSystemProxyLatencies(): Promise<Record<string, number>> {
  const { data } = await api.get<Record<string, number>>("/system-proxy/latencies");
  return data;
}

export async function fetchSystemProxyEgressIp(): Promise<IpInfo | null> {
  const { data } = await api.get<IpInfo | null>("/system-proxy/egress-ip");
  return data;
}

// ---------- System ----------

export async function fetchSystemHealth(): Promise<SystemHealthResult> {
  const { data } = await api.get<SystemHealthResult>("/system/health");
  return data;
}

export async function cancelSpeedTest(): Promise<void> {
  await api.post("/system/speed/cancel");
}

// ---------- SSE streams ----------

/** Listen for a custom SSE event and parse its JSON payload. */
function jsonEvent<T>(es: EventSource, name: string, handler: (data: T) => void): void {
  es.addEventListener(name, (e) => handler(JSON.parse((e as MessageEvent).data) as T));
}

function createEventSource(
  url: string,
  onError?: (e: Event) => void,
  { probeAuth = false }: { probeAuth?: boolean } = {},
): EventSource {
  const es = new EventSource(url);
  let probed = false;
  es.onerror = (e) => {
    onError?.(e);
    if (!probeAuth) return;
    // Only probe /auth/status on the first error — once we know whether
    // auth is the cause, subsequent reconnect-attempt errors don't need
    // to re-check. We deliberately don't close the stream: EventSource
    // sends cookies fresh on each retry, so the next reconnect after a
    // successful login picks up the new session and resumes automatically.
    if (probed) return;
    probed = true;
    fetchAuthStatus().then(
      (status) => {
        if (status.enabled && !status.authenticated) onUnauthorized();
      },
      () => {},
    );
  };
  return es;
}

export interface ConnectivityStreamHandlers {
  onServices: (services: string[]) => void;
  onResult: (result: ConnectivityResult) => void;
  onDone: () => void;
  onError: (err: Event) => void;
}

export function openConnectivityStream(handlers: ConnectivityStreamHandlers): EventSource {
  const es = createEventSource("/api/v1/connectivity/test/stream", handlers.onError);
  jsonEvent<{ services: string[] }>(es, "services", ({ services }) => handlers.onServices(services));
  jsonEvent<ConnectivityResult>(es, "result", handlers.onResult);
  es.addEventListener("done", () => {
    handlers.onDone();
    es.close();
  });
  return es;
}

export interface LogStreamHandlers {
  onChunk: (text: string) => void;
  onOpen?: () => void;
  onEnd?: () => void;
  onStreamError?: (detail: string) => void;
  onError?: (err: Event) => void;
}

export function openLogStream(containerName: string, tail: number, handlers: LogStreamHandlers): EventSource {
  const url = `/api/v1/containers/${encodeURIComponent(containerName)}/logs/stream?tail=${tail}`;
  // The only auth-gated stream: a 401 here (expired session) should re-prompt login.
  const es = createEventSource(url, handlers.onError, { probeAuth: true });
  if (handlers.onOpen) es.onopen = handlers.onOpen;
  jsonEvent<{ text: string }>(es, "chunk", ({ text }) => handlers.onChunk(text));
  if (handlers.onStreamError) {
    jsonEvent<{ detail?: string }>(es, "stream-error", ({ detail }) => handlers.onStreamError!(detail ?? "Stream error"));
  }
  es.addEventListener("end", () => {
    handlers.onEnd?.();
    es.close();
  });
  return es;
}

export interface ContainerStreamHandlers {
  onSnapshot: (data: ContainerListResponse) => void;
  onError?: (detail: string) => void;
}

export function openContainerStream(handlers: ContainerStreamHandlers): EventSource {
  const es = createEventSource("/api/v1/containers/stream");
  jsonEvent<ContainerListResponse>(es, "snapshot", handlers.onSnapshot);
  if (handlers.onError) {
    jsonEvent<{ detail?: string }>(es, "stream-error", ({ detail }) => handlers.onError!(detail ?? "Stream error"));
  }
  return es;
}

export interface ScannerStreamHandlers {
  onStatus: (status: ScannerStatus) => void;
  onError?: (detail: string) => void;
}

export function openScannerStream(handlers: ScannerStreamHandlers): EventSource {
  const es = createEventSource("/api/v1/scanner/stream");
  jsonEvent<ScannerStatus>(es, "status", handlers.onStatus);
  if (handlers.onError) {
    jsonEvent<{ detail?: string }>(es, "stream-error", ({ detail }) => handlers.onError!(detail ?? "Stream error"));
  }
  return es;
}

export interface SystemProxyStreamHandlers {
  onState: (state: SystemProxyState) => void;
  onError?: (detail: string) => void;
}

export function openSystemProxyStream(handlers: SystemProxyStreamHandlers): EventSource {
  const es = createEventSource("/api/v1/system-proxy/state/stream");
  jsonEvent<SystemProxyState>(es, "state", handlers.onState);
  if (handlers.onError) {
    jsonEvent<{ detail?: string }>(es, "stream-error", ({ detail }) => handlers.onError!(detail ?? "Stream error"));
  }
  return es;
}

export interface TrafficStreamHandlers {
  onSnapshot: (snapshot: TrafficSnapshot) => void;
  onError?: (err: Event) => void;
}

export function openTrafficStream(handlers: TrafficStreamHandlers): EventSource {
  const es = createEventSource("/api/v1/traffic/stream", handlers.onError);
  jsonEvent<TrafficSnapshot>(es, "traffic", handlers.onSnapshot);
  return es;
}

export interface SpeedStreamHandlers {
  onProgress: (data: SpeedTestProgress) => void;
  onError?: () => void;
}

export function openSpeedTestStream(handlers: SpeedStreamHandlers): EventSource {
  const es = createEventSource("/api/v1/system/speed/stream", () => {
    es.close();
    handlers.onError?.();
  });
  es.onmessage = (event) => {
    const data = JSON.parse(event.data) as SpeedTestProgress;
    handlers.onProgress(data);
    if (data.phase === "done" || data.phase === "cancelled" || data.phase === "error") {
      es.close();
    }
  };
  return es;
}
