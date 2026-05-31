import { Activity } from "lucide-react";
import { useProxyThroughput } from "@/hooks/useTraffic";
import { formatRateShort } from "@/utils/format";
import { cn } from "@/utils/cn";

/** A compact throughput chip for a card's status line. Hidden while idle. */
export function CardThroughput({ name, running }: { name: string; running: boolean }) {
  const { bps } = useProxyThroughput(name);
  // No chip while idle/connecting (bps is 0) — keeps quiet cards uncluttered;
  // it appears the moment real traffic flows.
  if (!running || bps < 1) return null;
  return (
    <span
      className="text-muted inline-flex shrink-0 items-center gap-1 text-[11px] tabular-nums"
      title={`Live throughput · ${formatRateShort(bps)}/s`}
    >
      <Activity className="h-3 w-3 shrink-0" />
      {formatRateShort(bps)}/s
    </span>
  );
}

/**
 * A faint glow behind the card whose intensity tracks the proxy's share of the
 * busiest proxy right now — so the most-used proxies visibly "light up" with no
 * extra layout. Colour-neutral on purpose: brightness is the only signal, so it
 * never collides with the status palette (emerald/red/amber/sky).
 */
export function CardActivityGlow({ name }: { name: string }) {
  const { share } = useProxyThroughput(name);
  if (share <= 0.01) return null;
  return (
    <div
      className={cn(
        "pointer-events-none absolute inset-0 z-0 bg-gradient-to-r from-white/[0.07] to-transparent transition-opacity duration-700",
      )}
      style={{ opacity: share }}
      aria-hidden="true"
    />
  );
}
