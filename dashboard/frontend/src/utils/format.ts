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
