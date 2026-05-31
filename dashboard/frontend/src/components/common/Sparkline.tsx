import { cn } from "@/utils/cn";

interface Props {
  /** Two series sharing one vertical scale (download + upload, bytes/sec). */
  down: number[];
  up: number[];
  className?: string;
  width?: number;
  height?: number;
}

function path(values: number[], max: number, width: number, height: number): string {
  // Too few points to draw a curve — show a flat baseline so the chart reads as
  // "quiet" rather than rendering blank/broken.
  if (values.length < 2) return `M0,${height - 1} L${width},${height - 1}`;
  const step = width / (values.length - 1);
  // Leave 1px headroom so the peak line isn't clipped at the top edge.
  const scaleY = (v: number) => height - 1 - (max > 0 ? (v / max) * (height - 2) : 0);
  return values.map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${scaleY(v).toFixed(1)}`).join(" ");
}

/**
 * A tiny dual-line sparkline for the header traffic readout. Download (sky) and
 * upload (emerald) share a scale so their relative magnitude reads true. Purely
 * decorative — the numbers carry the precise values — so it's aria-hidden.
 */
export default function Sparkline({ down, up, className, width = 52, height = 22 }: Props) {
  const max = Math.max(1, ...down, ...up);
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("overflow-visible", className)}
      aria-hidden="true"
      preserveAspectRatio="none"
    >
      <path d={path(down, max, width, height)} fill="none" stroke="currentColor" className="text-sky-400/80" strokeWidth={1.25} strokeLinejoin="round" />
      <path d={path(up, max, width, height)} fill="none" stroke="currentColor" className="text-emerald-400/80" strokeWidth={1.25} strokeLinejoin="round" />
    </svg>
  );
}
