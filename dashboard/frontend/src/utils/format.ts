// Human-friendly byte-rate formatting for the live traffic readouts.

const FULL_UNITS = ["B", "KB", "MB", "GB", "TB"];
const SHORT_UNITS = ["B", "K", "M", "G", "T"];

function scale(bps: number, units: string[]): { value: string; unit: string } {
  let v = bps;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  // One decimal below 10 (e.g. 1.2 MB) reads cleanly; whole numbers above.
  const digits = i > 0 && v < 10 ? 1 : 0;
  return { value: v.toFixed(digits), unit: units[i] ?? "" };
}

/** "1.2 MB/s". Sub-1 B/s collapses to "0" so idle proxies don't flicker. */
export function formatRate(bps: number): string {
  if (!bps || bps < 1) return "0";
  const { value, unit } = scale(bps, FULL_UNITS);
  return `${value} ${unit}/s`;
}

/** Compact "1.2M" for tight card chips. */
export function formatRateShort(bps: number): string {
  if (!bps || bps < 1) return "0";
  const { value, unit } = scale(bps, SHORT_UNITS);
  return `${value}${unit}`;
}

/** "1.2 MB" — a cumulative size, not a rate (no "/s" suffix). */
export function formatBytes(bytes: number): string {
  if (!bytes || bytes < 1) return "0 B";
  const { value, unit } = scale(bytes, FULL_UNITS);
  return `${value} ${unit}`;
}

/** Coarse "just now" / "4 min" / "2 h" / "3 d" from an ISO timestamp. */
export function formatAgo(iso: string): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const s = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s} sec`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h`;
  return `${Math.round(h / 24)} d`;
}

/** Compact elapsed/remaining clock: "45s", "2m 03s", "1h 04m" (seconds resolution
 *  under an hour so a live counter visibly ticks). */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

/** Coarse "soon" / "in 4 min" / "in 2 h" / "in 3 d" from a future ISO timestamp. */
export function formatUntil(iso: string): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const s = Math.round((then - Date.now()) / 1000);
  if (s < 60) return "soon";
  const m = Math.round(s / 60);
  if (m < 60) return `in ${m} min`;
  const h = Math.round(m / 60);
  if (h < 24) return `in ${h} h`;
  return `in ${Math.round(h / 24)} d`;
}
