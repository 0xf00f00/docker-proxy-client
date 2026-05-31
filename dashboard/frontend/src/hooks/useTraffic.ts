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

let state: TrafficState = IDLE;
const listeners = new Set<() => void>();
let es: EventSource | null = null;
let refCount = 0;

function emit(): void {
  for (const l of listeners) l();
}

function applySnapshot(snap: TrafficSnapshot): void {
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
}

function markDisconnected(): void {
  // Only "live" → "reconnecting" matters. While still "connecting" (no data
  // yet) we stay there so the UI keeps its skeleton instead of flashing zeros.
  if (state.status !== "live") return;
  state = { ...state, status: "reconnecting" };
  emit();
}

function openStream(): void {
  if (es || (typeof document !== "undefined" && document.hidden)) return;
  es = openTrafficStream({
    onSnapshot: applySnapshot,
    // EventSource reconnects on its own; just reflect the gap in the UI.
    onError: markDisconnected,
  });
}

function closeStream(): void {
  es?.close();
  es = null;
  markDisconnected();
}

function onVisibilityChange(): void {
  if (document.hidden) closeStream();
  else openStream();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  refCount += 1;
  if (refCount === 1) {
    document.addEventListener("visibilitychange", onVisibilityChange);
    openStream();
  }
  return () => {
    listeners.delete(listener);
    refCount -= 1;
    if (refCount === 0) {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      closeStream();
      state = IDLE; // a future mount starts clean rather than from stale history.
    }
  };
}

function getSnapshot(): TrafficState {
  return state;
}

/** Subscribe to the live traffic state. Re-renders the caller ~once per second. */
export function useTraffic(): TrafficState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
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
