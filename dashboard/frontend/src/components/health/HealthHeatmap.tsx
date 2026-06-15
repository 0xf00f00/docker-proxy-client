import type { HealthBucket } from "@/types";
import { cn } from "@/utils/cn";
import { STATUS_FILL, uptimeLabel } from "./status";

const WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"];

function dayLabel(b: HealthBucket): string {
  const when = new Date(b.ts * 1000).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  return b.status === "unknown" ? `${when} · not measured` : `${when} · ${uptimeLabel(b.uptimePct)} up`;
}

/**
 * GitHub-contributions-style calendar grid: one cell per day, colored by that
 * day's worst status, weekday-aligned (Monday first). The grid spans the full
 * width of its container — cells are square and size themselves to fill it — and
 * each is tappable to drill into a day; the parent scrolls when the grid is
 * taller than the viewport.
 */
export default function HealthHeatmap({
  buckets,
  selectedTs,
  onSelect,
}: {
  buckets: HealthBucket[];
  selectedTs?: number | null;
  onSelect?: (b: HealthBucket) => void;
}) {
  const first = buckets[0];
  // JS getDay(): Sun=0..Sat=6 → Monday-first index.
  const lead = first ? (new Date(first.ts * 1000).getDay() + 6) % 7 : 0;

  return (
    <div>
      <div className="mb-1.5 grid grid-cols-7 gap-1.5">
        {WEEKDAYS.map((w, i) => (
          <span key={i} className="text-muted text-center text-[10px] font-medium">
            {w}
          </span>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1.5" role="grid" aria-label="Network status by day">
        {Array.from({ length: lead }).map((_, i) => (
          <span key={`pad-${i}`} className="aspect-square" aria-hidden="true" />
        ))}
        {buckets.map((b) => {
          const selected = selectedTs === b.ts;
          return (
            <button
              key={b.ts}
              type="button"
              onClick={() => onSelect?.(b)}
              aria-label={dayLabel(b)}
              aria-pressed={selected}
              title={dayLabel(b)}
              className={cn(
                "aspect-square w-full rounded transition-transform active:scale-90",
                STATUS_FILL[b.status],
                selected && "ring-2 ring-white ring-offset-1 ring-offset-zinc-900",
              )}
            />
          );
        })}
      </div>
    </div>
  );
}
