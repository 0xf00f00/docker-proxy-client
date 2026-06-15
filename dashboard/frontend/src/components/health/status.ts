import type { HealthStatus } from "@/types";

// One palette, used everywhere health status appears. Red is reserved for outages
// only (amber = slow, grey = no data), matching the rest of the dashboard.
export const STATUS_DOT: Record<HealthStatus, string> = {
  good: "bg-emerald-400",
  degraded: "bg-amber-400",
  outage: "bg-red-400",
  unknown: "bg-zinc-600",
};

/** Fill for timeline bar segments and heatmap cells. */
export const STATUS_FILL: Record<HealthStatus, string> = {
  good: "bg-emerald-500/80",
  degraded: "bg-amber-500/80",
  outage: "bg-red-500/90",
  unknown: "bg-zinc-800",
};

export const STATUS_TEXT: Record<HealthStatus, string> = {
  good: "text-emerald-400",
  degraded: "text-amber-400",
  outage: "text-red-400",
  unknown: "text-zinc-400",
};

/** Headline word for a status when we have no more specific cause label. */
export const STATUS_WORD: Record<HealthStatus, string> = {
  good: "Network healthy",
  degraded: "Connection is slow",
  outage: "Network outage",
  unknown: "Checking…",
};

/** "Mon 2:14 PM"; drops the weekday for today so it reads cleaner. */
function clockLabel(epoch: number): string {
  const d = new Date(epoch * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return sameDay ? time : `${d.toLocaleDateString(undefined, { weekday: "short" })} ${time}`;
}

/** "Mon 2:14 PM → 6:40 PM" / "Today 2:14 PM → ongoing". */
export function incidentRange(start: number, end: number | null): string {
  const from = clockLabel(start);
  if (end == null) return `${from} → ongoing`;
  return `${from} → ${clockLabel(end)}`;
}

/** "4h 26m" / "35m" / "50s" — coarse, human duration of an incident. */
export function durationLabel(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

/** "98%" or "—" when nothing was measured. */
export function uptimeLabel(pct: number | null): string {
  return pct == null ? "—" : `${pct % 1 === 0 ? pct.toFixed(0) : pct.toFixed(1)}%`;
}
