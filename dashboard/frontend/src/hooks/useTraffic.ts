import { useSyncExternalStore } from "react";
import { openTrafficStream } from "@/api/client";
import type { TrafficSnapshot } from "@/types";

const HISTORY_LEN = 60; // ~60s of samples for the header sparkline.

// "connecting" = no snapshot yet (show a skeleton, not zeros).
// "live"       = receiving snapshots (numbers may legitimately be 0 = idle).
// "reconnecting" = stream dropped after being live; keep showing last values.
export type TrafficStatus = "connecting" | "live" | "reconnecting";

export interface TrafficState {
  status: TrafficStatus;
  system: { up: number; down: number };
  systemHistory: { up: number; down: number }[];
  proxies: Record<string, number>;
  /** Largest current per-proxy rate, for normalizing the card activity glow. */
  maxProxyBps: number;
}

const IDLE: TrafficState = {
  status: "connecting",
  system: { up: 0, down: 0 },
  systemHistory: [],
  proxies: {},
  maxProxyBps: 0,
};

export type StreamConnection = "connecting" | "live" | "reconnecting" | "offline";

const INITIAL_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 10_000;
const OFFLINE_AFTER_ATTEMPTS = 3;
const STALL_MS = 8_000;
const WATCHDOG_MS = 2_000;

let state: TrafficState = IDLE;
const listeners = new Set<() => void>();

let conn: StreamConnection = "connecting";
const connListeners = new Set<() => void>();

let es: EventSource | null = null;
let attempts = 0;
let lastMessageAt = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let watchdog: ReturnType<typeof setInterval> | null = null;
let refCount = 0;

function emit(): void {
  for (const l of listeners) l();
}

function setConn(next: StreamConnection): void {
  if (conn === next) return;
  conn = next;
  for (const l of connListeners) l();
}

function applySnapshot(snap: TrafficSnapshot): void {
  lastMessageAt = now();
  attempts = 0;
  const systemHistory = [...state.systemHistory, snap.system].slice(-HISTORY_LEN);
  const values = Object.values(snap.proxies);
  state = {
    status: "live",
    system: snap.system,
    systemHistory,
    proxies: snap.proxies,
    maxProxyBps: values.length ? Math.max(...values) : 0,
  };
  emit();
  setConn("live");
}

function markStale(): void {
  if (state.status !== "live") return;
  state = { ...state, status: "reconnecting" };
  emit();
}

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : 0;
}

function clearReconnect(): void {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function connect(): void {
  if (typeof document !== "undefined" && document.hidden) return;
  clearReconnect();
  // Give this attempt a full STALL_MS window before the watchdog can fault it.
  lastMessageAt = now();
  es = openTrafficStream({
    onSnapshot: applySnapshot,
    onError: handleDrop,
  });
  es.onopen = () => {
    lastMessageAt = now();
  };
}

function handleDrop(): void {
  // A reconnect is already queued — don't stack timers (onerror can fire repeatedly).
  if (reconnectTimer) return;
  es?.close();
  es = null;
  markStale();
  attempts += 1;
  setConn(attempts >= OFFLINE_AFTER_ATTEMPTS ? "offline" : "reconnecting");
  const delay = Math.min(INITIAL_BACKOFF_MS * 2 ** (attempts - 1), MAX_BACKOFF_MS);
  const jitter = Math.random() * 300; // de-sync many tabs hammering a recovering backend
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay + jitter);
}

function checkStall(): void {
  if (!es || (typeof document !== "undefined" && document.hidden)) return;
  if (now() - lastMessageAt > STALL_MS) handleDrop();
}

function reset(): void {
  attempts = 0;
  clearReconnect();
  es?.close();
  es = null;
  setConn("reconnecting");
  connect();
}

export function retryStreamConnection(): void {
  reset();
}

function onVisibilityChange(): void {
  if (document.hidden) {
    es?.close();
    es = null;
    clearReconnect();
    markStale();
  } else {
    reset();
  }
}

function onOnline(): void {
  if (conn !== "live") reset();
}

function start(): void {
  lastMessageAt = now();
  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("online", onOnline);
  watchdog = setInterval(checkStall, WATCHDOG_MS);
  connect();
}

function stop(): void {
  document.removeEventListener("visibilitychange", onVisibilityChange);
  window.removeEventListener("online", onOnline);
  if (watchdog) {
    clearInterval(watchdog);
    watchdog = null;
  }
  clearReconnect();
  es?.close();
  es = null;
  state = IDLE; // a future mount starts clean rather than from stale history.
  conn = "connecting";
}

function ref(): void {
  refCount += 1;
  if (refCount === 1) start();
}

function unref(): void {
  refCount -= 1;
  if (refCount === 0) stop();
}

function subscribeTraffic(listener: () => void): () => void {
  listeners.add(listener);
  ref();
  return () => {
    listeners.delete(listener);
    unref();
  };
}

function subscribeConn(listener: () => void): () => void {
  connListeners.add(listener);
  ref();
  return () => {
    connListeners.delete(listener);
    unref();
  };
}

function getTraffic(): TrafficState {
  return state;
}

function getConn(): StreamConnection {
  return conn;
}

/** Subscribe to the live traffic state. Re-renders the caller ~once per second. */
export function useTraffic(): TrafficState {
  return useSyncExternalStore(subscribeTraffic, getTraffic, getTraffic);
}

export function useStreamConnection(): StreamConnection {
  return useSyncExternalStore(subscribeConn, getConn, getConn);
}

export interface ProxyThroughput {
  bps: number;
  /** 0–1 share of the busiest proxy right now (for glow intensity). */
  share: number;
  status: TrafficStatus;
}

/** Live throughput for one container, keyed by `container.name`. */
export function useProxyThroughput(name: string): ProxyThroughput {
  const s = useTraffic();
  const bps = s.proxies[name] ?? 0;
  return {
    bps,
    share: s.maxProxyBps > 0 ? bps / s.maxProxyBps : 0,
    status: s.status,
  };
}
