import { lazy, Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { fetchHealthCurrent, fetchHealthTimeline } from "@/api/client";
import type { HealthStatus } from "@/types";
import { cn } from "@/utils/cn";
import { ModalLoadingShell } from "@/components/common/Modal";
import HealthBar from "./HealthBar";
import { STATUS_DOT, STATUS_WORD, uptimeLabel } from "./status";

const HealthModal = lazy(() => import("./HealthModal"));

// The headline status: prefer the live cause label, fall back to the plain word.
function headline(status: HealthStatus, causeLabel: string | undefined): string {
  return causeLabel || STATUS_WORD[status];
}

function subline(uptimePct: number | null, incidents: number, status: HealthStatus): string {
  if (uptimePct == null) return "Gathering data…";
  const up = `Up ${uptimeLabel(uptimePct)} today`;
  if (incidents === 0) return status === "good" ? `${up} · no issues` : up;
  return `${up} · ${incidents} ${incidents === 1 ? "incident" : "incidents"}`;
}

export default function HealthSection() {
  const [open, setOpen] = useState(false);

  const current = useQuery({
    queryKey: ["health-current"],
    queryFn: fetchHealthCurrent,
    refetchInterval: 30_000,
  });
  const timeline = useQuery({
    queryKey: ["health-timeline", "24h"],
    queryFn: () => fetchHealthTimeline("24h"),
    refetchInterval: 60_000,
  });

  // Endpoint is always present (health monitoring is on by default); a hard error
  // means something's wrong with the dashboard itself, so just hide the card.
  if (current.isError) return null;

  const status: HealthStatus = current.data?.current?.status ?? "unknown";
  const today = current.data?.today;
  const buckets = timeline.data?.buckets ?? [];

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="View network health over time"
        className="border-border bg-card hover:bg-card/80 active:bg-card/70 w-full rounded-xl border p-4 py-3.5 text-left transition-colors sm:py-4"
      >
        <div className="flex items-center gap-3">
          <span className="relative flex h-3 w-3 shrink-0 items-center justify-center">
            {status !== "good" && status !== "unknown" && (
              <span
                className={cn(
                  "absolute inline-flex h-full w-full animate-ping rounded-full opacity-60",
                  STATUS_DOT[status],
                )}
              />
            )}
            <span className={cn("relative inline-flex h-3 w-3 rounded-full", STATUS_DOT[status])} aria-hidden="true" />
          </span>

          <div className="flex min-w-0 flex-1 flex-col">
            <span className="truncate text-sm font-semibold sm:text-base">
              {headline(status, current.data?.current?.causeLabel)}
            </span>
            <span className="text-muted truncate text-xs">
              {subline(today?.uptimePct ?? null, today?.incidentCount ?? 0, status)}
            </span>
          </div>

          <ChevronRight className="h-4 w-4 shrink-0 text-zinc-500" aria-hidden="true" />
        </div>

        {buckets.length > 0 && (
          <div className="mt-3">
            <HealthBar buckets={buckets} className="h-5" />
            <div className="text-muted mt-1 flex justify-between text-[10px] tabular-nums">
              <span>24h ago</span>
              <span>now</span>
            </div>
          </div>
        )}
      </button>

      {open && (
        <Suspense fallback={<ModalLoadingShell title="Network health" onClose={() => setOpen(false)} />}>
          <HealthModal onClose={() => setOpen(false)} />
        </Suspense>
      )}
    </>
  );
}
