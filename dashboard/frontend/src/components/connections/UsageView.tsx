import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, BarChart3, Loader2, Trash2 } from "lucide-react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { clearUsage, fetchUsageTop } from "@/api/client";
import type { UsageBucket, UsagePeriod, UsageReport, UsageSite } from "@/types";
import { cn } from "@/utils/cn";
import { formatBytes } from "@/utils/format";

const PERIODS: { id: UsagePeriod; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "week", label: "Week" },
  { id: "month", label: "Month" },
];

function hostHue(host: string): number {
  let h = 0;
  for (let i = 0; i < host.length; i += 1) h = (h * 31 + host.charCodeAt(i)) % 360;
  return h;
}

function hostInitial(host: string): string {
  const m = host.match(/[a-z0-9]/i);
  return m ? m[0].toUpperCase() : "•";
}

// "used today · Jun 10" / "used this week · Jun 4–10" / "used this month · June"
function periodSubtitle(report: UsageReport): string {
  const start = new Date(report.since * 1000);
  const md = (d: Date) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  if (report.period === "today") return `used today · ${md(start)}`;
  if (report.period === "month") return `used this month · ${start.toLocaleDateString(undefined, { month: "long" })}`;
  if (report.period === "all") return "used all time";
  const end = new Date((report.since + 6 * 86400) * 1000);
  const endLabel = end.getMonth() === start.getMonth() ? end.getDate() : md(end);
  return `used this week · ${md(start)}–${endLabel}`;
}

export default function UsageView() {
  const [period, setPeriod] = useState<UsagePeriod>("today");
  const [confirmClear, setConfirmClear] = useState(false);
  const qc = useQueryClient();

  const usage = useQuery({
    queryKey: ["usage", period],
    queryFn: () => fetchUsageTop(period),
    refetchInterval: 15_000,
  });

  const clear = useMutation({
    mutationFn: clearUsage,
    onSuccess: () => {
      setConfirmClear(false);
      qc.invalidateQueries({ queryKey: ["usage"] });
    },
  });

  const report = usage.data;
  const total = (report?.totalDown ?? 0) + (report?.totalUp ?? 0);
  const hasData = !!report && total > 0;
  const maxShare = report?.sites[0]?.share ?? 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-border bg-card sticky top-0 z-10 border-b px-4 pt-3 pb-4">
        <div className="flex gap-2">
          {PERIODS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => setPeriod(p.id)}
              aria-current={period === p.id}
              className={cn(
                "min-h-9 rounded-full px-4 text-sm font-medium transition-colors",
                period === p.id
                  ? "bg-primary text-primary-foreground"
                  : "bg-zinc-800 text-muted hover:text-foreground active:bg-zinc-700",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>

        <div className="mt-4 flex items-end justify-between gap-3">
          <div className="flex min-w-0 flex-col">
            <span className="text-3xl font-semibold tabular-nums">{formatBytes(total)}</span>
            <span className="text-muted truncate text-xs">{report ? periodSubtitle(report) : " "}</span>
          </div>
          {hasData && (
            <div className="flex shrink-0 flex-col items-end gap-0.5 text-xs tabular-nums">
              <span className="inline-flex items-center gap-1 text-sky-400">
                <ArrowDown className="h-3 w-3 shrink-0" />
                {formatBytes(report!.totalDown)}
              </span>
              <span className="inline-flex items-center gap-1 text-emerald-400">
                <ArrowUp className="h-3 w-3 shrink-0" />
                {formatBytes(report!.totalUp)}
              </span>
            </div>
          )}
        </div>

        {hasData && <UsageBars key={period} series={report!.series} period={period} />}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {usage.isPending ? (
          <div className="flex items-center justify-center gap-2 py-16">
            <Loader2 className="text-muted h-5 w-5 animate-spin" />
            <span className="text-muted text-sm">Loading usage…</span>
          </div>
        ) : !hasData ? (
          <EmptyState />
        ) : (
          <ul className="divide-border/60 divide-y">
            {report!.sites.map((site) => (
              <UsageRow key={site.domain} site={site} barWidth={maxShare ? site.share / maxShare : 0} />
            ))}
          </ul>
        )}
      </div>

      {hasData && (
        <div className="border-border flex shrink-0 justify-end border-t px-3 py-2">
          <button
            type="button"
            onClick={() => setConfirmClear(true)}
            className="text-muted hover:text-foreground inline-flex min-h-10 items-center gap-1.5 rounded-lg px-3 text-xs font-medium active:bg-zinc-800"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clear history
          </button>
        </div>
      )}

      <ConfirmDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title="Clear usage history?"
        message="This permanently erases the saved record of which sites used data. It can't be undone."
        confirmLabel="Clear history"
        variant="destructive"
        busy={clear.isPending}
        onConfirm={() => clear.mutate()}
      />
    </div>
  );
}

// A bar's tick label, or "" for the gaps between labelled bars (cells keep their
// width so bars and labels stay aligned).
function tickLabel(period: UsagePeriod, bucket: UsageBucket, i: number): string {
  if (period === "week") return ["M", "T", "W", "T", "F", "S", "S"][i] ?? "";
  if (period === "today") return i % 6 === 0 ? ["12a", "6a", "12p", "6p"][i / 6] ?? "" : "";
  if (period === "month") return i % 7 === 0 ? String(new Date(bucket.ts * 1000).getDate()) : "";
  return "";
}

function bucketSeconds(period: UsagePeriod): number {
  return period === "today" ? 3600 : 86400;
}

// The scrubbed bucket's time window, e.g. "2–3 PM" (hourly) or "Mon, Jun 9" (daily).
function bucketLabel(period: UsagePeriod, b: UsageBucket): string {
  const start = new Date(b.ts * 1000);
  if (period === "today") {
    const end = new Date((b.ts + 3600) * 1000);
    const h = (d: Date) => d.toLocaleTimeString(undefined, { hour: "numeric" });
    return `${h(start)}–${h(end)}`;
  }
  return start.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function UsageBars({ series, period }: { series: UsageBucket[]; period: UsagePeriod }) {
  const trackRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  // null = follow the default (current bucket); a number = the bucket the user picked.
  const [selected, setSelected] = useState<number | null>(null);
  // The bucket under the finger mid-drag, shown live before release.
  const [active, setActive] = useState<number | null>(null);

  if (series.length === 0) return null;
  const max = Math.max(1, ...series.map((b) => b.value));
  const nowSec = Date.now() / 1000;
  const bucketSec = bucketSeconds(period);
  const count = series.length;

  // Default readout: the in-progress bucket, else the most recent one with data.
  const currentIdx = series.findIndex((b) => b.ts <= nowSec && b.ts + bucketSec > nowSec);
  let defaultIdx = currentIdx;
  if (defaultIdx < 0) for (let i = count - 1; i >= 0; i -= 1) if ((series[i]?.value ?? 0) > 0) { defaultIdx = i; break; }
  if (defaultIdx < 0) defaultIdx = count - 1;

  const shownIdx = active ?? selected ?? defaultIdx;
  const shown = series[shownIdx]!;
  const shownFuture = shown.ts > nowSec;

  const indexFromX = (clientX: number): number => {
    const el = trackRef.current;
    if (!el) return 0;
    const r = el.getBoundingClientRect();
    return Math.max(0, Math.min(count - 1, Math.floor(((clientX - r.left) / r.width) * count)));
  };
  const onPointerDown = (e: React.PointerEvent) => {
    draggingRef.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setActive(indexFromX(e.clientX));
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (draggingRef.current || e.pointerType === "mouse") setActive(indexFromX(e.clientX));
  };
  const onPointerUp = (e: React.PointerEvent) => {
    draggingRef.current = false;
    setSelected(indexFromX(e.clientX));
    setActive(null);
  };
  const onPointerLeave = () => {
    if (!draggingRef.current) setActive(null);
  };

  return (
    <div className="mt-4">
      <div className="mb-1.5 flex items-baseline justify-between gap-2 text-xs">
        <span className="text-muted truncate">{bucketLabel(period, shown)}</span>
        <span className="shrink-0 font-semibold tabular-nums">
          {shownFuture ? "—" : formatBytes(shown.value)}
        </span>
      </div>
      <div
        ref={trackRef}
        className="flex h-14 cursor-pointer touch-none items-end gap-px select-none"
        role="img"
        aria-label="Data used over time"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerLeave}
        onPointerCancel={onPointerLeave}
      >
        {series.map((b, i) => {
          const future = b.ts > nowSec;
          const current = !future && b.ts + bucketSec > nowSec;
          const pct = b.value > 0 ? Math.max(8, (b.value / max) * 100) : 0;
          const highlight = i === shownIdx;
          return (
            <div key={b.ts} className="flex h-full flex-1 items-end">
              {pct > 0 ? (
                <div
                  className={cn(
                    "w-full rounded-sm",
                    current ? "bg-sky-400" : "bg-sky-500/70",
                    highlight && "ring-2 ring-white",
                  )}
                  style={{ height: `${pct}%` }}
                />
              ) : (
                // Empty/future bucket: a faint baseline keeps the axis continuous.
                <div className={cn("w-full rounded-sm", highlight ? "h-1 bg-zinc-600" : "h-0.5 bg-zinc-800")} />
              )}
            </div>
          );
        })}
      </div>
      <div className="text-muted mt-1.5 flex gap-px text-[10px] tabular-nums">
        {series.map((b, i) => (
          <span key={b.ts} className="flex-1 text-center">
            {tickLabel(period, b, i)}
          </span>
        ))}
      </div>
    </div>
  );
}

function UsageRow({ site, barWidth }: { site: UsageSite; barWidth: number }) {
  const hue = hostHue(site.domain);
  const total = site.down + site.up;
  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      <span
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold"
        style={{ backgroundColor: `hsl(${hue} 45% 20%)`, color: `hsl(${hue} 70% 72%)` }}
        aria-hidden="true"
      >
        {hostInitial(site.domain)}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm font-medium">{site.domain}</span>
          <span className="shrink-0 text-sm font-semibold tabular-nums">{formatBytes(total)}</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-800">
            <div className="h-full rounded-full bg-sky-500/80" style={{ width: `${Math.max(2, barWidth * 100)}%` }} />
          </div>
          <span className="text-muted shrink-0 text-[10px] tabular-nums">
            ↓ {formatBytes(site.down)} · ↑ {formatBytes(site.up)}
          </span>
        </div>
      </div>
    </li>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <BarChart3 className="h-12 w-12 text-zinc-700" />
      <p className="text-sm font-medium">No usage in this period</p>
      <p className="text-muted max-w-xs text-xs">
        As apps and websites send data through the proxy, the biggest data users show up here.
      </p>
    </div>
  );
}
