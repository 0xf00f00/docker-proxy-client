import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Pause, Play, Power, RotateCw, Settings, Square } from "lucide-react";
import { toast } from "sonner";
import type { DnsFunnel, DnsResolver, DnsScannerStatus } from "@/types";
import {
  openDnsScannerStream,
  pauseDnsScan,
  resumeDnsScan,
  runDnsScan,
  startContainer,
  stopDnsScan,
} from "@/api/client";
import { getErrorMessage } from "@/utils/errors";
import { formatAgo, formatDuration, formatUntil } from "@/utils/format";
import { cn } from "@/utils/cn";
import { ModalLoadingShell } from "@/components/common/Modal";
import { Btn, LOG, SPIN } from "@/components/scanner/ControlButton";

const loadLogsModal = () => import("@/components/common/LogsModal");
const LogsModal = lazy(loadLogsModal);
const loadConfirmDialog = () => import("@/components/common/ConfirmDialog");
const ConfirmDialog = lazy(loadConfirmDialog);
const loadEnvModal = () => import("@/components/config/EnvModal");
const EnvModal = lazy(loadEnvModal);

const NAME = "DNS Scanner";
const NOOP = () => {};

type TransitionAction = "starting" | "searching" | "pausing" | "resuming" | "stopping" | null;

export default function DnsScannerSection() {
  const [data, setData] = useState<DnsScannerStatus | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [showEnv, setShowEnv] = useState(false);
  const [showResolvers, setShowResolvers] = useState(false);
  const [action, setAction] = useState<TransitionAction>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const baseline = useRef<string | null>(null);

  useEffect(() => {
    const es = openDnsScannerStream({ onStatus: setData });
    return () => es.close();
  }, []);

  useEffect(() => {
    void loadLogsModal();
    void loadConfirmDialog();
  }, []);

  // Hold the requested action until the snapshot reaches its target state, then
  // release the transitional UI. A per-action safety timeout prevents a missed
  // event from stranding the card in a spinner forever.
  useEffect(() => {
    if (!action) return;
    const running = data?.scanner_running ?? false;
    const reachable = data?.api_reachable ?? false;
    const scanning = data?.scanning ?? false;
    const paused = data?.paused ?? false;
    const ranFresh = !!data?.last_run && data.last_run !== baseline.current;
    const confirmed =
      action === "starting"
        ? running && reachable
        : action === "searching"
          ? scanning || paused || ranFresh
          : action === "pausing"
            ? paused
            : action === "resuming"
              ? scanning && !paused
              : /* stopping */ !scanning && !paused;
    if (confirmed) {
      setAction(null);
      return;
    }
    const ms = action === "searching" ? 120_000 : action === "stopping" || action === "starting" ? 60_000 : 15_000;
    const t = setTimeout(() => setAction(null), ms);
    return () => clearTimeout(t);
  }, [action, data?.scanner_running, data?.api_reachable, data?.scanning, data?.paused, data?.last_run]);

  const scan = useMutation({
    mutationFn: runDnsScan,
    onSuccess: () => {
      toast.success("Searching for resolvers");
      baseline.current = data?.last_run ?? null;
      setAction("searching");
    },
    onError: (e) => toast.error(`Couldn't start: ${getErrorMessage(e)}`),
  });
  const pause = useMutation({
    mutationFn: pauseDnsScan,
    onSuccess: () => {
      toast.success("Pausing search");
      setAction("pausing");
    },
    onError: (e) => toast.error(`Pause failed: ${getErrorMessage(e)}`),
  });
  const resume = useMutation({
    mutationFn: resumeDnsScan,
    onSuccess: () => {
      toast.success("Resuming search");
      setAction("resuming");
    },
    onError: (e) => toast.error(`Resume failed: ${getErrorMessage(e)}`),
  });
  const stop = useMutation({
    mutationFn: stopDnsScan,
    onSuccess: () => {
      toast.success("Stopping search");
      setAction("stopping");
      setConfirmStop(false);
    },
    onError: (e) => toast.error(`Stop failed: ${getErrorMessage(e)}`),
  });
  const start = useMutation({
    mutationFn: (name: string) => startContainer(name),
    onSuccess: () => {
      toast.success("Starting scanner");
      setAction("starting");
    },
    onError: (e) => toast.error(`Start failed: ${getErrorMessage(e)}`),
  });

  const running = data?.scanner_running ?? false;
  const reachable = data?.api_reachable ?? false;
  const container = data?.container ?? "";

  const starting = start.isPending || action === "starting";
  const searching = scan.isPending || action === "searching";
  const pausing = pause.isPending || action === "pausing";
  const resuming = resume.isPending || action === "resuming";
  const stopping = stop.isPending || action === "stopping";

  const paused = data?.paused ?? false;
  const scanning = (((data?.scanning ?? false) || searching) && !paused) || pausing;
  const count = data?.working_count ?? 0;
  // Any in-flight transition locks every action button (short, conflict-free).
  const busy = action !== null || starting || searching || pausing || resuming || stopping;

  const view = deriveView({ running, reachable, scanning, paused, count, data, starting, stopping, pausing, resuming });

  return (
    <div>
      <div className="mb-3">
        <h2 className="text-muted text-xs font-medium tracking-wide uppercase">DNS Resolvers</h2>
        <p className="text-muted mt-0.5 text-[11px] normal-case">Emergency DNS for when the internet is blocked</p>
      </div>
      <div className="border-border bg-card overflow-hidden rounded-xl border">
        <div className="flex min-h-12 items-center gap-3 px-4 py-3">
          <span className={cn("inline-block h-2.5 w-2.5 shrink-0 rounded-full", view.dot)} aria-label={view.label} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm">{view.label}</p>
            {view.sub && <p className="text-muted truncate text-xs">{view.sub}</p>}
          </div>
        </div>

        {(scanning || paused) && (
          <div className="border-border border-t px-4 py-3">
            <Progress
              probed={data?.probed ?? 0}
              candidates={data?.candidates ?? 0}
              phase={data?.phase ?? ""}
              started={data?.run_started ?? null}
              paused={paused && !scanning}
            />
          </div>
        )}

        {data && hasDetails(data) && (
          <div className="border-border border-t">
            <button
              type="button"
              onClick={() => setShowResolvers((v) => !v)}
              className="text-muted hover:text-foreground flex min-h-11 w-full items-center justify-between px-4 text-xs"
            >
              <span>{count > 0 ? `Show details (${count})` : "Show details"}</span>
              <span>{showResolvers ? "Hide" : "Show"}</span>
            </button>
            {showResolvers && <Details data={data} />}
          </div>
        )}

        <div className="border-border flex flex-wrap gap-2 border-t px-4 py-3">
          {starting ? (
            <Btn onClick={NOOP} disabled variant="positive" icon={SPIN}>
              Starting…
            </Btn>
          ) : stopping ? (
            <Btn onClick={NOOP} disabled variant="destructive" icon={SPIN}>
              Stopping…
            </Btn>
          ) : !running ? (
            <Btn
              onClick={() => start.mutate(container)}
              disabled={busy || !container}
              variant="positive"
              icon={<Power className="h-3.5 w-3.5" />}
            >
              Start
            </Btn>
          ) : scanning ? (
            <>
              <Btn
                onClick={() => pause.mutate()}
                disabled={busy}
                icon={pausing ? SPIN : <Pause className="h-3.5 w-3.5" />}
              >
                {pausing ? "Pausing…" : "Pause"}
              </Btn>
              <Btn
                onClick={() => setConfirmStop(true)}
                disabled={busy}
                variant="destructive"
                icon={<Square className="h-3.5 w-3.5" />}
              >
                Stop
              </Btn>
            </>
          ) : paused ? (
            <>
              <Btn
                onClick={() => resume.mutate()}
                disabled={busy}
                variant="positive"
                icon={resuming ? SPIN : <Play className="h-3.5 w-3.5" />}
              >
                {resuming ? "Resuming…" : "Resume"}
              </Btn>
              <Btn
                onClick={() => setConfirmStop(true)}
                disabled={busy}
                variant="destructive"
                icon={<Square className="h-3.5 w-3.5" />}
              >
                Stop
              </Btn>
            </>
          ) : (
            <Btn
              onClick={() => scan.mutate()}
              disabled={busy || !reachable}
              // Draw the eye to "Search now" when there's nothing to fall back on.
              variant={count === 0 ? "positive" : "default"}
              icon={<RotateCw className="h-3.5 w-3.5" />}
            >
              Search now
            </Btn>
          )}
          <Btn onClick={() => setShowLogs(true)} disabled={!container} icon={LOG}>
            Logs
          </Btn>
          <Btn onClick={() => setShowEnv(true)} disabled={!container} icon={<Settings className="h-3.5 w-3.5" />}>
            Settings
          </Btn>
        </div>
      </div>

      <Suspense
        fallback={showLogs ? <ModalLoadingShell title={`${NAME} — Logs`} onClose={() => setShowLogs(false)} /> : null}
      >
        {showLogs && <LogsModal containerName={container} displayName={NAME} onClose={() => setShowLogs(false)} />}
      </Suspense>

      <Suspense
        fallback={showEnv ? <ModalLoadingShell title={`${NAME} — Settings`} onClose={() => setShowEnv(false)} /> : null}
      >
        {showEnv && <EnvModal containerName={container} displayName={NAME} onClose={() => setShowEnv(false)} />}
      </Suspense>

      <Suspense fallback={null}>
        {confirmStop && (
          <ConfirmDialog
            open
            onOpenChange={(o) => !o && setConfirmStop(false)}
            title="Stop the search?"
            message="This ends the current search for resolvers. Resolvers already found stay active — you can search again anytime."
            confirmLabel="Stop"
            variant="destructive"
            busy={stop.isPending}
            onConfirm={() => stop.mutate()}
          />
        )}
      </Suspense>
    </div>
  );
}

type View = { dot: string; label: string; sub: string };

function deriveView(s: {
  running: boolean;
  reachable: boolean;
  scanning: boolean;
  paused: boolean;
  count: number;
  data: DnsScannerStatus | null;
  starting: boolean;
  stopping: boolean;
  pausing: boolean;
  resuming: boolean;
}): View {
  const { running, reachable, scanning, paused, count, data, starting, stopping, pausing, resuming } = s;
  // Transitional states first — a pulsing dot + verb so the wait is legible.
  if (starting) return { dot: "animate-pulse bg-zinc-400", label: "Starting…", sub: "Turning on the scanner" };
  if (stopping) return { dot: "animate-pulse bg-amber-500", label: "Stopping…", sub: "Finishing the current checks" };
  if (pausing) return { dot: "animate-pulse bg-amber-500", label: "Pausing…", sub: progressLine(data) };
  if (resuming) return { dot: "animate-pulse bg-emerald-500", label: "Resuming…", sub: progressLine(data) };
  if (!running) return { dot: "bg-zinc-600", label: "Off", sub: "Tap Start to turn on" };
  if (scanning) {
    return { dot: "animate-pulse bg-emerald-500", label: "Searching for resolvers…", sub: progressLine(data) };
  }
  if (paused) {
    return { dot: "bg-amber-500", label: "Paused", sub: `${progressLine(data)} · resume to continue` };
  }
  if (!reachable) {
    return {
      dot: "bg-zinc-600",
      label: count > 0 ? `${count} known` : "Starting…",
      sub: count > 0 ? "Can't reach scanner — showing last known" : "Waiting for the scanner",
    };
  }
  if (count > 0) {
    const target = data?.target_n ?? 0;
    // Green once we have a full backup set; amber while still thin (below target).
    const dot = target > 0 && count < target ? "bg-amber-500" : "bg-emerald-500";
    const noun = count === 1 ? "resolver" : "resolvers";
    return { dot, label: `${count} backup ${noun} ready`, sub: scheduleLine(data) };
  }
  // Zero is "none yet", never a failure (RED is reserved for down). During an
  // outage finding nothing is expected — stay neutral grey, not alarming red.
  return { dot: "bg-zinc-500", label: "No backup resolvers yet", sub: scheduleLine(data) };
}

// Header sub-line: just the result the user cares about. The activity detail
// (phase, progress, time) lives in the Progress panel below, so we don't echo it.
function progressLine(data: DnsScannerStatus | null): string {
  const accepted = data?.accepted ?? 0;
  const target = data?.target_n ?? 0;
  return target > 0 ? `${accepted} of ${target} found` : `${accepted} found`;
}

// Human-readable name for the active scan leg (non-technical audience).
function phaseLabel(phase: string | undefined): string {
  switch (phase) {
    case "verify":
      return "Re-checking known resolvers";
    case "sweep":
      return "Searching for new resolvers";
    default:
      return "Searching";
  }
}

function checkedLabel(probed: number, candidates: number): string {
  if (probed <= 0) return "";
  return candidates > 0
    ? `${probed.toLocaleString()} of ${candidates.toLocaleString()} checked`
    : `${probed.toLocaleString()} checked`;
}

function scheduleLine(data: DnsScannerStatus | null): string {
  const parts: string[] = [];
  if (data?.last_run) parts.push(`Checked ${formatAgo(data.last_run)} ago`);
  else parts.push("Not checked yet");
  if (data?.next_scan) parts.push(`next check ${formatUntil(data.next_scan)}`);
  return parts.join(" · ");
}

function Progress({
  probed,
  candidates,
  phase,
  started,
  paused,
}: {
  probed: number;
  candidates: number;
  phase: string;
  started: string | null;
  paused: boolean;
}) {
  const now = useTicker(!paused);
  const bar = paused ? "bg-amber-500" : "bg-emerald-500";
  const pct = candidates > 0 ? Math.min(99, Math.round((probed / candidates) * 100)) : null;

  const startedMs = started ? Date.parse(started) : NaN;
  const elapsedS = Number.isNaN(startedMs) ? null : Math.max(0, Math.round((now - startedMs) / 1000));
  const remainingS =
    elapsedS != null && pct !== null && probed >= 20 && candidates > probed
      ? Math.round(elapsedS * ((candidates - probed) / probed))
      : null;

  const timeLine = paused
    ? elapsedS != null
      ? `${formatDuration(elapsedS)} elapsed · paused`
      : "paused"
    : [
        elapsedS != null ? `${formatDuration(elapsedS)} elapsed` : null,
        remainingS != null ? `~${formatDuration(remainingS)} left` : null,
      ]
        .filter(Boolean)
        .join(" · ");

  return (
    <div>
      <div className="text-muted mb-1.5 flex items-center justify-between gap-3 text-[10px] tabular-nums">
        <span className="truncate normal-case">{phaseLabel(phase)}</span>
        {probed > 0 && <span className="shrink-0">{checkedLabel(probed, candidates)}</span>}
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
        {pct === null ? (
          // Total unknown yet: a segment sweeping across, not a fixed-width fill.
          <div className={cn("animate-indeterminate h-full w-1/3 rounded-full", bar)} />
        ) : (
          <div
            className={cn("h-full rounded-full transition-[width] duration-500", bar)}
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
      {timeLine && <div className="text-muted mt-1.5 text-[10px] tabular-nums">{timeLine}</div>}
    </div>
  );
}

// Re-render every second while active so the elapsed/remaining clock ticks even
// when no scanner event arrives. Returns the current epoch-ms.
function useTicker(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

// hasDetails decides whether the "Show details" panel has anything to show: the
// resolver list or the last sweep's funnel.
function hasDetails(data: DnsScannerStatus): boolean {
  return data.working.length > 0 || (data.funnel?.probed ?? 0) > 0;
}

function Details({ data }: { data: DnsScannerStatus }) {
  const backupCount = data.backup_count ?? 0;
  return (
    <div className="space-y-3 px-4 pb-3">
      {data.funnel && data.funnel.probed > 0 && (
        <FunnelView funnel={data.funnel} working={data.working_count} live={data.state !== "idle"} />
      )}

      {backupCount > 0 && (
        <p className="text-muted text-[11px]">
          {backupCount} found via backup test — worked despite failing a quick check.
        </p>
      )}

      {data.working.length > 0 && (
        <ul className="space-y-1.5">
          {data.working.map((r) => (
            <li key={r.ip} className="flex items-center gap-2">
              <span className="flex-1 truncate font-mono text-xs text-zinc-400">{r.ip}</span>
              {r.backup && (
                <span className="rounded bg-zinc-700 px-1 py-0.5 text-[9px] tracking-wide text-zinc-300 uppercase">
                  backup
                </span>
              )}
              <Health resolver={r} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// FunnelView shows where the last sweep narrowed, in plain words — a collapse at
// one stage (e.g. EDNS) makes a hidden problem obvious instead of silent.
function FunnelView({ funnel, working, live }: { funnel: DnsFunnel; working: number; live: boolean }) {
  const rows: Array<[string, number]> = [
    ["Checked", funnel.probed],
    ["Reachable", funnel.alive],
    ["Real DNS server", funnel.forward],
    ["Supports EDNS", funnel.edns],
    ["Carried the tunnel", funnel.cert],
    ["Working", working],
  ];
  const max = Math.max(1, funnel.probed);
  return (
    <div className="space-y-1">
      <p className="text-muted text-[10px] tracking-wide uppercase">{live ? "This search so far" : "Last search"}</p>
      {rows.map(([label, n]) => (
        <div key={label} className="flex items-center gap-2">
          <span className="text-muted w-28 shrink-0 truncate text-[11px]">{label}</span>
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-zinc-800">
            <div
              className="h-full rounded-full bg-zinc-500"
              style={{ width: `${Math.max(2, Math.round((n / max) * 100))}%` }}
            />
          </div>
          <span className="text-muted w-14 shrink-0 text-right text-[11px] tabular-nums">{n.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

function Health({ resolver }: { resolver: DnsResolver }) {
  const loss = resolver.loss_pct;
  const cls = loss === 0 ? "text-emerald-400" : loss <= 20 ? "text-amber-400" : "text-red-400";
  const label = loss === 0 ? "stable" : `${loss}% loss`;
  return (
    <span
      className={cn("inline-flex items-center gap-1 text-[10px] tabular-nums", cls)}
      title={resolver.down_mtu ? `download MTU ${resolver.down_mtu}B` : undefined}
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}
