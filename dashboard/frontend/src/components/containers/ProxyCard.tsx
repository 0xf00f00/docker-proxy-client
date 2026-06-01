import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  RotateCw,
  Copy,
  FileEdit,
  ChevronDown,
  ChevronUp,
  Wifi,
  WifiOff,
  Loader2,
  Power,
  PowerOff,
  Sliders,
  Activity,
  ShieldCheck,
  ShieldAlert,
  ShieldX,
  HelpCircle,
} from "lucide-react";
import { toast } from "sonner";
import type { ContainerInfo, ConnectivityResult, StabilityResult, StabilityGrade, StabilityProgress } from "@/types";
import { restartContainer, startContainer, stopContainer, testConnectivity } from "@/api/client";
import { useStabilityCheck } from "@/hooks/useStabilityCheck";
import { cn } from "@/utils/cn";
import { getErrorMessage } from "@/utils/errors";
import IpFlag from "@/components/common/IpFlag";
import TelegramIcon from "@/components/common/TelegramIcon";
import { CardActivityGlow, CardThroughput } from "@/components/containers/CardActivity";
import { ModalLoadingShell } from "@/components/common/Modal";
import { isSocksCapable, telegramSocksLink } from "@/utils/telegram";

const loadConfigModal = () => import("@/components/config/ConfigModal");
const loadEnvModal = () => import("@/components/config/EnvModal");
const loadLogsModal = () => import("@/components/common/LogsModal");

const ConfigModal = lazy(loadConfigModal);
const EnvModal = lazy(loadEnvModal);
const LogsModal = lazy(loadLogsModal);
const ConfirmDialog = lazy(() => import("@/components/common/ConfirmDialog"));

// Kick off chunk downloads the moment the user expands a card. By the time
// they reach for Settings/Config/Logs, the chunk is usually already cached —
// so Suspense doesn't have to fall back to the loading shell at all.
function preloadModals() {
  void loadConfigModal();
  void loadEnvModal();
  void loadLogsModal();
}

interface Props {
  container: ContainerInfo;
  connectivity: ConnectivityResult | null;
  isTesting?: boolean;
  onTestResult?: (result: ConnectivityResult) => void;
}

type LifecycleState = "running" | "restarting" | "starting-health" | "unhealthy" | "stopped" | "transient";

function lifecycle(status: string, health: string | null): LifecycleState {
  if (status === "restarting") return "restarting";
  if (status === "running") {
    if (health === "starting") return "starting-health";
    if (health === "unhealthy") return "unhealthy";
    return "running";
  }
  if (status === "created" || status === "removing") return "transient";
  return "stopped";
}

type ModalKind = "config" | "env" | "logs" | null;

function modalLoadingTitle(kind: Exclude<ModalKind, null>, displayName: string): string {
  if (kind === "config") return `${displayName} — Config`;
  if (kind === "env") return `${displayName} — Settings`;
  return `${displayName} — Logs`;
}

type ConfirmKind = "stop" | "restart" | "stability" | null;

// Settle delay after a started proxy reports healthy, before auto-probing it.
const AUTO_TEST_AFTER_START_MS = 1500;

// The stability probe drives traffic through the proxy as a SOCKS/HTTP forward
// proxy. TLS-handshake proxies (sni-spoofing) and other non-forwarding services
// can't be exercised this way, so the button is hidden for them rather than
// shown only to return "unsupported protocol".
const STABILITY_PROTOCOLS = new Set(["socks5", "socks", "http", "mixed"]);
function supportsStability(protocol: string): boolean {
  return STABILITY_PROTOCOLS.has(protocol.split("+")[0] ?? "");
}

export default function ProxyCard({ container, connectivity, isTesting = false, onTestResult }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [openModal, setOpenModal] = useState<ModalKind>(null);
  const [confirm, setConfirm] = useState<ConfirmKind>(null);
  const [autoTestPending, setAutoTestPending] = useState(false);
  const queryClient = useQueryClient();

  const restartMutation = useMutation({
    mutationFn: () => restartContainer(container.name),
    onSuccess: () => {
      toast.success(`${container.dashboard.name} restart requested`);
      queryClient.invalidateQueries({ queryKey: ["containers"] });
      setConfirm(null);
    },
    onError: (err) => toast.error(`Restart failed: ${getErrorMessage(err)}`),
  });

  const startMutation = useMutation({
    mutationFn: () => startContainer(container.name),
    onSuccess: () => {
      toast.success(`${container.dashboard.name} starting`);
      setAutoTestPending(true);
      queryClient.invalidateQueries({ queryKey: ["containers"] });
    },
    onError: (err) => toast.error(`Start failed: ${getErrorMessage(err)}`),
  });

  const stopMutation = useMutation({
    mutationFn: () => stopContainer(container.name),
    onSuccess: () => {
      toast.success(`${container.dashboard.name} stopping`);
      queryClient.invalidateQueries({ queryKey: ["containers"] });
      setConfirm(null);
    },
    onError: (err) => toast.error(`Stop failed: ${getErrorMessage(err)}`),
  });

  const testMutation = useMutation({
    mutationFn: () => testConnectivity(container.name),
    onSuccess: (result) => onTestResult?.(result),
  });

  const stabilityCheck = useStabilityCheck(container.name);

  const conn = testMutation.data ?? connectivity;
  const state = lifecycle(container.status, container.health);
  const isRunning = state === "running" || state === "unhealthy" || state === "starting-health";
  // Treat the brief window between clicking Restart and Docker emitting the
  // first state event as "restarting" so the card flips immediately.
  const isRestarting = state === "restarting" || restartMutation.isPending;
  const isStarting = startMutation.isPending;
  const isStopping = stopMutation.isPending;
  const isTransitioning = isRestarting || isStarting || isStopping;
  const testing = (isTesting || testMutation.isPending) && !isTransitioning;
  const canTest = container.dashboard.testable && !!container.lan_address;
  const showStability = canTest && isRunning && supportsStability(container.dashboard.protocol);
  const closeModal = () => setOpenModal(null);

  // Auto-probe once a just-started proxy is healthy.
  const runAutoTestRef = useRef<() => void>(() => {});
  runAutoTestRef.current = () => testMutation.mutate();
  useEffect(() => {
    if (!autoTestPending || state !== "running" || !canTest) return;
    const timer = setTimeout(() => {
      runAutoTestRef.current();
      setAutoTestPending(false);
    }, AUTO_TEST_AFTER_START_MS);
    return () => clearTimeout(timer);
  }, [autoTestPending, state, canTest]);

  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text);
    toast.success(`${label} copied`);
  };

  const [host, port] = container.lan_address?.split(":") ?? [];

  // One-tap "Add to Telegram"
  const telegramLink =
    host && port && isRunning && isSocksCapable(container.dashboard.protocol) ? telegramSocksLink(host, port) : null;

  // Keep the card un-dimmed while a transition is in flight, even if status
  // flips mid-mutation — the "Stopping…/Starting…" badge is the better cue.
  const dimmed = !isRunning && !isRestarting && !isStopping && !isStarting;

  return (
    <>
      <div className={cn("border-border bg-card relative overflow-hidden rounded-xl border", dimmed && "opacity-60")}>
        {isRunning && <CardActivityGlow name={container.name} />}
        <div
          role="button"
          tabIndex={0}
          onClick={() => {
            if (!expanded) preloadModals();
            setExpanded(!expanded);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              if (!expanded) preloadModals();
              setExpanded(!expanded);
            }
          }}
          aria-expanded={expanded}
          className="relative z-[1] flex w-full cursor-pointer items-center gap-3 px-4 py-3.5 text-left select-none active:bg-zinc-800/40 sm:py-4"
        >
          <ConnectionIndicator
            conn={conn}
            state={state}
            isRestarting={isRestarting}
            isStarting={isStarting}
            isStopping={isStopping}
            testing={testing}
            canTest={canTest}
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-medium sm:text-base">{container.dashboard.name}</span>
              <LifecycleBadge
                state={state}
                isRestarting={isRestarting}
                isStarting={isStarting}
                isStopping={isStopping}
              />
            </div>
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0 flex-1">
                <StatusText
                  conn={conn}
                  state={state}
                  isRestarting={isRestarting}
                  isStarting={isStarting}
                  isStopping={isStopping}
                  testing={testing}
                  canTest={canTest}
                />
              </div>
              <CardThroughput name={container.name} running={isRunning} />
            </div>
          </div>
          {state === "stopped" && !expanded ? (
            <InlineStartButton starting={isStarting} onClick={() => startMutation.mutate()} />
          ) : canTest && isRunning && !isTransitioning ? (
            <InlineTestButton testing={testing} hasResult={!!conn} onClick={() => testMutation.mutate()} />
          ) : null}
          {conn?.success && conn.ip_info && <IpFlag info={conn.ip_info} className="ml-1" />}
          {expanded ? (
            <ChevronUp className="text-muted h-5 w-5 shrink-0" />
          ) : (
            <ChevronDown className="text-muted h-5 w-5 shrink-0" />
          )}
        </div>

        {expanded && (
          <div className="border-border border-t px-3 pt-3 pb-3.5 sm:px-4 sm:pb-4">
            {isRunning && container.lan_address && (
              <div className="mb-3 grid grid-cols-2 gap-2">
                {host && <CopyField label="Host" value={host} onCopy={() => copyToClipboard(host, "Host")} />}
                {port && <CopyField label="Port" value={port} onCopy={() => copyToClipboard(port, "Port")} />}
              </div>
            )}

            {showStability && (stabilityCheck.running || stabilityCheck.result || stabilityCheck.error) && (
              <StabilityPanel
                result={stabilityCheck.result}
                progress={stabilityCheck.progress}
                running={stabilityCheck.running}
                error={stabilityCheck.error}
              />
            )}

            {telegramLink && (
              <a
                href={telegramLink}
                target="_blank"
                rel="noopener noreferrer"
                className="mb-3 flex min-h-12 w-full items-center justify-center gap-2 rounded-lg bg-[#229ED9] px-4 text-sm font-semibold text-white transition-colors hover:bg-[#1e8fc4] active:bg-[#1b82b3]"
              >
                <TelegramIcon className="h-5 w-5" />
                Add to Telegram
              </a>
            )}

            <div className="flex flex-wrap gap-2">
              {isRunning ? (
                <ActionButton
                  onClick={() => setConfirm("stop")}
                  disabled={isTransitioning}
                  variant="destructive"
                  icon={
                    isStopping ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PowerOff className="h-3.5 w-3.5" />
                  }
                >
                  {isStopping ? "Stopping…" : "Stop"}
                </ActionButton>
              ) : (
                <ActionButton
                  onClick={() => startMutation.mutate()}
                  disabled={isTransitioning}
                  variant="positive"
                  icon={
                    isStarting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Power className="h-3.5 w-3.5" />
                  }
                >
                  {isStarting ? "Starting…" : "Start"}
                </ActionButton>
              )}
              {isRunning && (
                <ActionButton
                  onClick={() => setConfirm("restart")}
                  disabled={isTransitioning}
                  icon={
                    isRestarting ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RotateCw className="h-3.5 w-3.5" />
                    )
                  }
                >
                  {isRestarting ? "Restarting…" : "Restart"}
                </ActionButton>
              )}
              {showStability && (
                <ActionButton
                  onClick={() => setConfirm("stability")}
                  disabled={isTransitioning || stabilityCheck.running}
                  icon={
                    stabilityCheck.running ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Activity className="h-3.5 w-3.5" />
                    )
                  }
                >
                  {stabilityCheck.running ? "Checking…" : "Check stability"}
                </ActionButton>
              )}
              {container.dashboard.env && container.dashboard.env.length > 0 && (
                <ActionButton onClick={() => setOpenModal("env")} icon={<Sliders className="h-3.5 w-3.5" />}>
                  Settings
                </ActionButton>
              )}
              {container.dashboard.config && (
                <ActionButton onClick={() => setOpenModal("config")} icon={<FileEdit className="h-3.5 w-3.5" />}>
                  Config
                </ActionButton>
              )}
              <ActionButton
                onClick={() => setOpenModal("logs")}
                icon={<span className="font-mono text-[10px]">LOG</span>}
              >
                Logs
              </ActionButton>
            </div>
          </div>
        )}
      </div>

      <Suspense
        fallback={
          openModal ? (
            <ModalLoadingShell title={modalLoadingTitle(openModal, container.dashboard.name)} onClose={closeModal} />
          ) : null
        }
      >
        {openModal === "config" && container.dashboard.config && (
          <ConfigModal containerName={container.name} displayName={container.dashboard.name} onClose={closeModal} />
        )}
        {openModal === "env" && (
          <EnvModal containerName={container.name} displayName={container.dashboard.name} onClose={closeModal} />
        )}
        {openModal === "logs" && (
          <LogsModal containerName={container.name} displayName={container.dashboard.name} onClose={closeModal} />
        )}
        {confirm === "stop" && (
          <ConfirmDialog
            open
            onOpenChange={(o) => !o && setConfirm(null)}
            title={`Stop ${container.dashboard.name}?`}
            message={`Apps connected through ${container.dashboard.name} will lose their connection until you start it again.`}
            confirmLabel="Stop"
            variant="destructive"
            busy={stopMutation.isPending}
            onConfirm={() => stopMutation.mutate()}
          />
        )}
        {confirm === "restart" && (
          <ConfirmDialog
            open
            onOpenChange={(o) => !o && setConfirm(null)}
            title={`Restart ${container.dashboard.name}?`}
            message={`Active connections through ${container.dashboard.name} will be briefly interrupted while it restarts.`}
            confirmLabel="Restart"
            busy={restartMutation.isPending}
            onConfirm={() => restartMutation.mutate()}
          />
        )}
        {confirm === "stability" && (
          <ConfirmDialog
            open
            onOpenChange={(o) => !o && setConfirm(null)}
            title="Check this proxy's stability?"
            message="This briefly pushes traffic through the proxy (about 30 MB of data) to reveal download drops and call-quality problems. While it runs, anyone using the proxies right now — calls, downloads — will be slower. Best run when no one is online."
            confirmLabel="Run check"
            onConfirm={() => {
              setConfirm(null);
              stabilityCheck.start();
            }}
          />
        )}
      </Suspense>
    </>
  );
}

function InlineTestButton({
  testing,
  hasResult,
  onClick,
}: {
  testing: boolean;
  hasResult: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      disabled={testing}
      aria-label={hasResult ? "Re-test connection" : "Test connection"}
      title={hasResult ? "Re-test" : "Test"}
      className="text-muted hover:text-foreground flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-zinc-800/70 hover:bg-zinc-700/80 active:bg-zinc-700 disabled:opacity-60"
    >
      {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCw className="h-3.5 w-3.5" />}
    </button>
  );
}

function InlineStartButton({ starting, onClick }: { starting: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      disabled={starting}
      aria-label="Start"
      className="flex min-h-10 shrink-0 items-center gap-1.5 rounded-full bg-emerald-500/15 px-3.5 text-xs font-semibold text-emerald-300 hover:bg-emerald-500/25 active:bg-emerald-500/30 disabled:opacity-60"
    >
      {starting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Power className="h-3.5 w-3.5" />}
      {starting ? "Starting…" : "Start"}
    </button>
  );
}

function LifecycleBadge({
  state,
  isRestarting,
  isStarting,
  isStopping,
}: {
  state: LifecycleState;
  isRestarting: boolean;
  isStarting: boolean;
  isStopping: boolean;
}) {
  if (isStopping) {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-medium text-red-300 uppercase">
        Stopping
      </span>
    );
  }
  if (isStarting) {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300 uppercase">
        Starting
      </span>
    );
  }
  if (isRestarting) {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-300 uppercase">
        Restarting
      </span>
    );
  }
  if (state === "starting-health") {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-medium text-sky-300 uppercase">
        Starting
      </span>
    );
  }
  if (state === "unhealthy") {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-medium text-red-300 uppercase">
        Unhealthy
      </span>
    );
  }
  if (state === "stopped") {
    return (
      <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500 uppercase">Off</span>
    );
  }
  if (state === "transient") {
    return <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium text-zinc-400 uppercase">…</span>;
  }
  return null;
}

function ConnectionIndicator({
  conn,
  state,
  isRestarting,
  isStarting,
  isStopping,
  testing,
  canTest,
}: {
  conn: ConnectivityResult | null;
  state: LifecycleState;
  isRestarting: boolean;
  isStarting: boolean;
  isStopping: boolean;
  testing: boolean;
  canTest: boolean;
}) {
  if (isStopping) return <Loader2 className="h-5 w-5 shrink-0 animate-spin text-red-400" />;
  if (isStarting) return <Loader2 className="h-5 w-5 shrink-0 animate-spin text-emerald-400" />;
  if (isRestarting) return <Loader2 className="h-5 w-5 shrink-0 animate-spin text-amber-400" />;
  if (state === "starting-health") return <Loader2 className="h-5 w-5 shrink-0 animate-spin text-sky-400" />;
  if (testing) return <Loader2 className="text-primary h-5 w-5 shrink-0 animate-spin" />;
  if (state === "stopped" || state === "transient") return <WifiOff className="h-5 w-5 shrink-0 text-zinc-600" />;
  if (state === "unhealthy") return <WifiOff className="h-5 w-5 shrink-0 text-red-400" />;
  // Running but we never probe this service — neutral icon, no "pending result" dot.
  if (!canTest) return <Wifi className="h-5 w-5 shrink-0 text-zinc-500" />;
  if (!conn) return <div className="h-5 w-5 shrink-0 rounded-full bg-zinc-700" />;
  if (conn.success) return <Wifi className="h-5 w-5 shrink-0 text-emerald-400" />;
  return <WifiOff className="text-destructive h-5 w-5 shrink-0" />;
}

function StatusText({
  conn,
  state,
  isRestarting,
  isStarting,
  isStopping,
  testing,
  canTest,
}: {
  conn: ConnectivityResult | null;
  state: LifecycleState;
  isRestarting: boolean;
  isStarting: boolean;
  isStopping: boolean;
  testing: boolean;
  canTest: boolean;
}) {
  if (isStopping) return <p className="text-xs text-red-300">Stopping container…</p>;
  if (isStarting) return <p className="text-xs text-emerald-300">Starting container…</p>;
  if (isRestarting) return <p className="text-xs text-amber-300">Restarting container…</p>;
  if (state === "starting-health")
    return <p className="text-xs text-sky-300">Starting up — waiting for healthcheck…</p>;
  if (testing) return <p className="text-primary text-xs">Testing connection…</p>;
  if (state === "stopped") return null;
  if (state === "transient") return <p className="text-muted text-xs">Updating…</p>;
  if (state === "unhealthy") return <p className="text-xs text-red-300">Container reports unhealthy</p>;
  if (!canTest) return <p className="text-muted text-xs">Running</p>;
  if (!conn) return <p className="text-muted text-xs">Not tested yet</p>;
  const age = formatAge(conn.tested_at);
  if (conn.success)
    return (
      <p className="text-xs text-emerald-400">
        Connected · {conn.latency_ms}ms{age && <span className="text-muted"> · {age}</span>}
      </p>
    );
  return (
    <p className="text-destructive truncate text-xs">
      Cannot connect{conn.error ? ` — ${conn.error}` : ""}
      {age && <span className="text-muted"> · {age}</span>}
    </p>
  );
}

function formatAge(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 45) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

const GRADE_META: Record<StabilityGrade, { label: string; chip: string; Icon: typeof ShieldCheck }> = {
  good: { label: "Good", chip: "bg-emerald-500/15 text-emerald-300", Icon: ShieldCheck },
  degraded: { label: "Shaky", chip: "bg-amber-500/15 text-amber-300", Icon: ShieldAlert },
  bad: { label: "Poor", chip: "bg-red-500/15 text-red-300", Icon: ShieldX },
  inconclusive: { label: "Can't tell", chip: "bg-zinc-700/60 text-zinc-300", Icon: HelpCircle },
};

const PROGRESS_TEXT: Record<StabilityProgress["phase"], string> = {
  regime: "Checking your internet…",
  idle: "Measuring normal speed…",
  load: "Stress-testing downloads & calls…",
  longlived: "Testing long connections…",
};

function useLonglivedCountdown(progress: StabilityProgress | null, running: boolean): number | null {
  const [remaining, setRemaining] = useState<number | null>(null);
  const total = progress?.phase === "longlived" ? (progress.total_s ?? null) : null;
  useEffect(() => {
    if (!running || total == null) {
      setRemaining(null);
      return;
    }
    setRemaining(total);
    const id = setInterval(() => setRemaining((r) => (r == null ? r : Math.max(0, r - 1))), 1000);
    return () => clearInterval(id);
  }, [running, total]);
  return remaining;
}

function progressText(progress: StabilityProgress | null, countdown: number | null): string {
  const phase = progress?.phase ?? "regime";
  if (phase === "longlived" && countdown != null) {
    return `Testing long connections… ${countdown}s left`;
  }
  return PROGRESS_TEXT[phase];
}

// Abnormal regimes are about the whole internet link, not this proxy — say so in
// plain language so a non-technical user doesn't blame the proxy.
function regimeBanner(result: StabilityResult): string | null {
  if (result.regime.regime === "iran_only")
    return "Your internet is in Iran-only mode right now — international sites are blocked for everyone, so no proxy can be judged. This isn't a problem with this proxy.";
  if (result.regime.regime === "total_outage")
    return "Your internet appears to be down right now — nothing is reachable. Try again once it's back.";
  return null;
}

function GradeChip({ label, grade }: { label: string; grade: StabilityGrade }) {
  const meta = GRADE_META[grade];
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold", meta.chip)}>
      <meta.Icon className="h-3.5 w-3.5" />
      {label}: {meta.label}
    </span>
  );
}

function StabilityPanel({
  result,
  progress,
  running,
  error,
}: {
  result: StabilityResult | null;
  progress: StabilityProgress | null;
  running: boolean;
  error: string | null;
}) {
  const countdown = useLonglivedCountdown(progress, running);

  if (running && !result) {
    return (
      <div className="border-border bg-muted/20 mb-3 flex items-center gap-2 rounded-lg border p-3">
        <Loader2 className="text-muted h-4 w-4 shrink-0 animate-spin" />
        <p className="text-muted text-xs">{progressText(progress, countdown)}</p>
      </div>
    );
  }

  if (error && !result) {
    return (
      <div className="border-border bg-muted/20 mb-3 rounded-lg border p-3">
        <p className="text-destructive text-xs">{error}</p>
        <p className="text-muted mt-1 text-[11px]">Tap "Check stability" to try again.</p>
      </div>
    );
  }

  if (!result) return null;

  const banner = regimeBanner(result);

  return (
    <div className="border-border bg-muted/20 mb-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <GradeChip label="Downloads" grade={result.bulk_grade} />
        <GradeChip label="Calls" grade={result.call_grade} />
      </div>

      {banner && <p className="mt-2 rounded-md bg-zinc-800/60 p-2 text-xs text-zinc-300">{banner}</p>}
      {result.error && !banner && <p className="text-destructive mt-2 text-xs">{result.error}</p>}

      {result.bulk_grade !== "inconclusive" && (
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Metric label="Downloads OK" value={`${result.completed}/${result.streams}`} />
          {result.resets > 0 && <Metric label="Dropped" value={String(result.resets)} warn />}
          {result.stalls > 0 && <Metric label="Stalled" value={String(result.stalls)} warn />}
          {result.loaded_max_ms !== null && (
            <Metric
              label="Worst lag (loaded)"
              value={`${Math.round(result.loaded_max_ms)}ms`}
              warn={result.loaded_max_ms > 1000}
            />
          )}
          {result.loaded_spike_pct !== null && result.loaded_spike_pct > 0 && (
            <Metric label="Froze (>1s)" value={`${result.loaded_spike_pct}%`} warn={result.loaded_spike_pct > 0.5} />
          )}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-muted">{label}</span>
      <span className={cn("font-mono", warn ? "text-amber-300" : "text-foreground/80")}>{value}</span>
    </div>
  );
}

function CopyField({ label, value, onCopy }: { label: string; value: string; onCopy: () => void }) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onCopy();
      }}
      className="flex min-h-12 items-center justify-between gap-2 rounded-lg bg-zinc-800/80 px-3 py-2 text-left transition-colors hover:bg-zinc-700/80 active:bg-zinc-700"
    >
      <div className="min-w-0">
        <span className="text-muted block text-[10px] uppercase">{label}</span>
        <span className="block truncate font-mono text-sm">{value}</span>
      </div>
      <Copy className="text-muted h-4 w-4 shrink-0" />
    </button>
  );
}

type ActionVariant = "default" | "positive" | "destructive";

const ACTION_VARIANT_CLASS: Record<ActionVariant, string> = {
  default: "text-muted hover:text-foreground bg-zinc-800 active:bg-zinc-700",
  positive:
    "text-emerald-300 hover:text-emerald-200 bg-emerald-500/15 hover:bg-emerald-500/25 active:bg-emerald-500/30",
  destructive: "text-red-300 hover:text-red-200 bg-red-500/15 hover:bg-red-500/25 active:bg-red-500/30",
};

function ActionButton({
  onClick,
  disabled,
  icon,
  variant = "default",
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  icon: React.ReactNode;
  variant?: ActionVariant;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      disabled={disabled}
      className={cn(
        "flex min-h-10 items-center gap-1.5 rounded-lg px-3.5 text-xs font-medium disabled:opacity-50",
        ACTION_VARIANT_CLASS[variant],
      )}
    >
      {icon}
      {children}
    </button>
  );
}
