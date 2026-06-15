import { useRef, useState } from "react";
import type { HealthBucket } from "@/types";
import { cn } from "@/utils/cn";
import { STATUS_FILL, uptimeLabel } from "./status";

function scrubLabel(b: HealthBucket): string {
  const d = new Date(b.ts * 1000);
  const when =
    b.size >= 86400
      ? d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
      : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric" });
  return b.status === "unknown" ? `${when} · no data` : `${when} · ${uptimeLabel(b.uptimePct)} up`;
}

// Sparse axis ticks so the bar is legible without hovering (phones have no hover):
// clock times for an intraday (24h) bar, weekday names for a multi-day (7d) bar.
function tickLabel(b: HealthBucket, i: number, every: number): string {
  if (i % every !== 0) return "";
  const d = new Date(b.ts * 1000);
  return b.size <= 3600
    ? d.toLocaleTimeString(undefined, { hour: "numeric" })
    : d.toLocaleDateString(undefined, { weekday: "short" });
}

/**
 * The classic horizontal uptime bar: one colored segment per time bucket, oldest
 * on the left, with a sparse time axis beneath.
 *
 * When `onSelect` is given the whole bar is a drag scrubber (native pointer
 * events, so mouse + touch behave the same): dragging moves a floating readout
 * across the buckets and releasing selects the one under the finger. This is the
 * primary phone affordance — individual segments are far too thin to tap, but a
 * drag is forgiving. Without `onSelect` it renders as an inert bar (e.g. the mini
 * bar on the dashboard card).
 */
export default function HealthBar({
  buckets,
  className = "h-9",
  selectedTs,
  onSelect,
  showTicks = false,
}: {
  buckets: HealthBucket[];
  className?: string;
  selectedTs?: number | null;
  onSelect?: (b: HealthBucket) => void;
  showTicks?: boolean;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const [active, setActive] = useState<number | null>(null);
  const count = buckets.length;
  const interactive = !!onSelect && count > 0;
  const every = 6; // one tick per quarter of a 24h bar / per day of a 7d bar

  const indexFromX = (clientX: number): number => {
    const el = trackRef.current;
    if (!el || count === 0) return 0;
    const r = el.getBoundingClientRect();
    const frac = (clientX - r.left) / r.width;
    return Math.max(0, Math.min(count - 1, Math.floor(frac * count)));
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (!interactive) return;
    draggingRef.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setActive(indexFromX(e.clientX));
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!interactive) return;
    if (draggingRef.current || e.pointerType === "mouse") setActive(indexFromX(e.clientX));
  };
  const onPointerUp = (e: React.PointerEvent) => {
    if (!interactive) return;
    draggingRef.current = false;
    const b = buckets[indexFromX(e.clientX)];
    if (b) onSelect!(b);
  };
  const onPointerLeave = () => {
    if (!draggingRef.current) setActive(null);
  };

  const activeBucket = active != null ? buckets[active] : null;
  // Center of the active bucket, 0..1, used to anchor the readout horizontally.
  const frac = active != null ? (active + 0.5) / count : 0;
  // Pin to the edges near the ends so the readout never overflows the modal;
  // center it everywhere in between.
  const readoutStyle: React.CSSProperties =
    frac < 0.2 ? { left: 0 } : frac > 0.8 ? { right: 0 } : { left: `${frac * 100}%`, transform: "translateX(-50%)" };

  return (
    <div className="relative">
      {activeBucket && (
        // Below the bar (not above): the bar sits at the top of the modal's
        // scroll container, so an upward readout clips behind the fixed header.
        <div
          className="border-border bg-card text-foreground pointer-events-none absolute top-full z-20 mt-1 max-w-full rounded-md border px-2 py-1 text-[11px] font-medium whitespace-nowrap tabular-nums shadow-lg"
          style={readoutStyle}
        >
          {scrubLabel(activeBucket)}
        </div>
      )}

      <div
        ref={trackRef}
        className={cn(
          "flex items-stretch gap-px",
          className,
          interactive && "cursor-pointer touch-none select-none",
        )}
        role="img"
        aria-label="Network status over time"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerLeave}
        onPointerCancel={onPointerLeave}
      >
        {buckets.map((b, i) => {
          const highlight = selectedTs === b.ts || active === i;
          return (
            <div key={b.ts} className="flex-1" title={interactive ? undefined : scrubLabel(b)}>
              <span
                className={cn("block h-full w-full rounded-[2px]", STATUS_FILL[b.status], highlight && "ring-2 ring-white")}
              />
            </div>
          );
        })}
      </div>

      {showTicks && (
        <div className="text-muted mt-1.5 flex gap-px text-[10px] tabular-nums">
          {buckets.map((b, i) => (
            <span key={b.ts} className="flex-1 overflow-visible text-center whitespace-nowrap">
              {tickLabel(b, i, every)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
