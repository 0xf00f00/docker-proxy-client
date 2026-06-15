import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronLeft, Loader2, Trash2 } from "lucide-react";
import Modal from "@/components/common/Modal";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { clearHealth, fetchHealthIncidents, fetchHealthTimeline } from "@/api/client";
import type { HealthBucket, HealthIncident, HealthSecs, HealthWindow } from "@/types";
import { cn } from "@/utils/cn";
import HealthBar from "./HealthBar";
import HealthHeatmap from "./HealthHeatmap";
import { STATUS_DOT, durationLabel, incidentRange, uptimeLabel } from "./status";

const WINDOWS: { id: HealthWindow; label: string }[] = [
  { id: "24h", label: "24h" },
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
  { id: "90d", label: "90 days" },
];

const WINDOW_NOUN: Record<HealthWindow, string> = {
  "24h": "in the last 24 hours",
  "7d": "this week",
  "30d": "this month",
  "90d": "in the last 90 days",
};

function bucketTitle(b: HealthBucket): string {
  const d = new Date(b.ts * 1000);
  if (b.size >= 86400) return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  const end = new Date((b.ts + b.size) * 1000);
  const day = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const hr = (x: Date) => x.toLocaleTimeString(undefined, { hour: "numeric" });
  return `${day}, ${hr(d)}–${hr(end)}`;
}

function breakdown(secs: HealthSecs): string {
  const measured = secs.good + secs.degraded + secs.outage;
  if (measured === 0) return "Not measured";
  const pct = (v: number) => Math.round((v / measured) * 100);
  const parts: string[] = [];
  if (secs.good) parts.push(`Healthy ${pct(secs.good)}%`);
  if (secs.degraded) parts.push(`Slow ${pct(secs.degraded)}%`);
  if (secs.outage) parts.push(`Down ${pct(secs.outage)}%`);
  return parts.join(" · ");
}

export default function HealthModal({ onClose }: { onClose: () => void }) {
  const [window, setWindow] = useState<HealthWindow>("24h");
  const [selectedTs, setSelectedTs] = useState<number | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const qc = useQueryClient();

  const timeline = useQuery({
    queryKey: ["health-timeline", window],
    queryFn: () => fetchHealthTimeline(window),
    refetchInterval: 30_000,
  });
  const incidents = useQuery({
    queryKey: ["health-incidents", window],
    queryFn: () => fetchHealthIncidents(window),
    refetchInterval: 30_000,
  });

  const clear = useMutation({
    mutationFn: clearHealth,
    onSuccess: () => {
      setConfirmClear(false);
      setSelectedTs(null);
      qc.invalidateQueries({ queryKey: ["health-timeline"] });
      qc.invalidateQueries({ queryKey: ["health-incidents"] });
      qc.invalidateQueries({ queryKey: ["health-current"] });
    },
  });

  const pickWindow = (w: HealthWindow) => {
    setWindow(w);
    setSelectedTs(null);
  };

  const summary = timeline.data?.summary;
  const buckets = timeline.data?.buckets ?? [];
  const isCalendar = window === "30d" || window === "90d";
  const selected = selectedTs != null ? buckets.find((b) => b.ts === selectedTs) : undefined;

  const toggleSelect = (b: HealthBucket) => setSelectedTs((cur) => (cur === b.ts ? null : b.ts));

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      title="Network health"
      subtitle={`${uptimeLabel(summary?.uptimePct ?? null)} healthy ${WINDOW_NOUN[window]}`}
      headerActions={
        <span className="mr-1 flex items-baseline gap-1">
          <span className="text-base font-semibold tabular-nums sm:text-lg">
            {uptimeLabel(summary?.uptimePct ?? null)}
          </span>
          <span className="text-muted text-[10px]">up</span>
        </span>
      }
      size="full"
    >
      <div className="flex h-full min-h-0 flex-col">
        {/* Fixed header: just the window selector — uptime headline lives in the modal title bar. */}
        <div className="border-border bg-card shrink-0 border-b px-4 pt-3 pb-3">
          <div className="flex gap-2">
            {WINDOWS.map((w) => (
              <button
                key={w.id}
                type="button"
                onClick={() => pickWindow(w.id)}
                aria-current={window === w.id}
                className={cn(
                  "min-h-9 flex-1 rounded-full px-3 text-sm font-medium transition-colors",
                  window === w.id
                    ? "bg-primary text-primary-foreground"
                    : "text-muted hover:text-foreground bg-zinc-800 active:bg-zinc-700",
                )}
              >
                {w.label}
              </button>
            ))}
          </div>

        </div>

        {/* Scrollable body: timeline (tall heatmaps scroll here) then detail / incidents. */}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <div className="border-border/60 border-b px-4 pt-4 pb-4">
            {timeline.isPending ? (
              <div className={cn("animate-pulse rounded bg-zinc-800", isCalendar ? "h-40" : "h-9")} />
            ) : isCalendar ? (
              <HealthHeatmap buckets={buckets} selectedTs={selectedTs} onSelect={toggleSelect} />
            ) : (
              <HealthBar buckets={buckets} selectedTs={selectedTs} onSelect={toggleSelect} showTicks />
            )}
            <p className="text-muted mt-2.5 text-center text-[10px]">Tap a {isCalendar ? "day" : "bar"} for details</p>
          </div>

          {selected ? (
            <DayDetail bucket={selected} incidents={incidents.data?.incidents} onBack={() => setSelectedTs(null)} />
          ) : (
            <IncidentList incidents={incidents.data?.incidents} pending={incidents.isPending} window={window} />
          )}
        </div>

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
      </div>

      <ConfirmDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title="Clear health history?"
        message="This permanently erases the recorded outage and quality history. It can't be undone."
        confirmLabel="Clear history"
        variant="destructive"
        busy={clear.isPending}
        onConfirm={() => clear.mutate()}
      />
    </Modal>
  );
}

function DayDetail({
  bucket,
  incidents,
  onBack,
}: {
  bucket: HealthBucket;
  incidents: HealthIncident[] | undefined;
  onBack: () => void;
}) {
  const nowSec = Date.now() / 1000;
  const span = bucket.ts + bucket.size;
  const within = (incidents ?? []).filter((i) => i.start < span && (i.end ?? nowSec) > bucket.ts);

  return (
    <div>
      <button
        type="button"
        onClick={onBack}
        className="text-muted hover:text-foreground inline-flex min-h-9 items-center gap-1 px-3 pt-2 text-xs font-medium"
      >
        <ChevronLeft className="h-3.5 w-3.5" />
        All issues
      </button>

      <div className="border-border/60 border-b px-4 pt-1 pb-3">
        <div className="flex items-center gap-2">
          <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full", STATUS_DOT[bucket.status])} aria-hidden="true" />
          <span className="text-sm font-semibold">{bucketTitle(bucket)}</span>
          <span className="text-muted ml-auto text-sm tabular-nums">{uptimeLabel(bucket.uptimePct)} up</span>
        </div>
        <p className="text-muted mt-1 text-xs">{breakdown(bucket.secs)}</p>
      </div>

      {within.length === 0 ? (
        bucket.secs.outage > 0 || bucket.secs.degraded > 0 ? (
          // Colored by some trouble, but no incident long enough to list: brief blips
          // shorter than the incident threshold tint the bucket without becoming rows.
          <p className="text-muted px-4 py-10 text-center text-xs">
            A few brief blips, too short to list individually.
          </p>
        ) : (
          <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
            <CheckCircle2 className="h-9 w-9 text-emerald-500/70" />
            <p className="text-muted text-xs">No outages or slowdowns during this time.</p>
          </div>
        )
      ) : (
        <ul className="divide-border/60 divide-y">
          {within.map((inc) => (
            <IncidentRow key={inc.start} inc={inc} />
          ))}
        </ul>
      )}
    </div>
  );
}

function IncidentList({
  incidents,
  pending,
  window,
}: {
  incidents: HealthIncident[] | undefined;
  pending: boolean;
  window: HealthWindow;
}) {
  if (pending) {
    return (
      <div className="flex items-center justify-center gap-2 py-16">
        <Loader2 className="text-muted h-5 w-5 animate-spin" />
        <span className="text-muted text-sm">Loading…</span>
      </div>
    );
  }
  if (!incidents || incidents.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
        <CheckCircle2 className="h-12 w-12 text-emerald-500/70" />
        <p className="text-sm font-medium">No issues {WINDOW_NOUN[window]}</p>
        <p className="text-muted max-w-xs text-xs">
          Outages and slowdowns will be listed here, newest first, with how long each one lasted.
        </p>
      </div>
    );
  }
  return (
    <>
      <div className="text-muted px-4 pt-3 pb-1.5 text-[10px] font-medium tracking-wide uppercase">
        {incidents.length} {incidents.length === 1 ? "issue" : "issues"} {WINDOW_NOUN[window]}
      </div>
      <ul className="divide-border/60 divide-y">
        {incidents.map((inc) => (
          <IncidentRow key={inc.start} inc={inc} />
        ))}
      </ul>
    </>
  );
}

function IncidentRow({ inc }: { inc: HealthIncident }) {
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full", STATUS_DOT[inc.status])} aria-hidden="true" />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-2 text-sm font-medium">
          {inc.causeLabel}
          {inc.ongoing && (
            <span className="shrink-0 rounded-full bg-red-500/15 px-1.5 py-px text-[10px] font-medium text-red-300">
              Ongoing
            </span>
          )}
        </span>
        {inc.causeDetail && <span className="text-muted truncate text-xs">{inc.causeDetail}</span>}
        <span className="text-muted truncate text-xs tabular-nums">{incidentRange(inc.start, inc.end)}</span>
      </div>
      <span className="text-muted shrink-0 text-xs font-medium tabular-nums">{durationLabel(inc.durationS)}</span>
    </li>
  );
}
