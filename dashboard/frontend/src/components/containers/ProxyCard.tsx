import { lazy, Suspense, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCw, Copy, FileEdit, ChevronDown, ChevronUp, Wifi, WifiOff, Loader2, Power, PowerOff, Sliders } from "lucide-react";
import { toast } from "sonner";
import type { ContainerInfo, ConnectivityResult } from "@/types";
import { restartContainer, startContainer, stopContainer, testConnectivity } from "@/api/client";
import { cn } from "@/utils/cn";
import { getErrorMessage } from "@/utils/errors";
import IpFlag from "@/components/common/IpFlag";
import { ModalLoadingShell } from "@/components/common/Modal";

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

type ConfirmKind = "stop" | "restart" | null;

export default function ProxyCard({ container, connectivity, isTesting = false }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [openModal, setOpenModal] = useState<ModalKind>(null);
  const [confirm, setConfirm] = useState<ConfirmKind>(null);
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
  });

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
  const closeModal = () => setOpenModal(null);

  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text);
    toast.success(`${label} copied`);
  };

  const [host, port] = container.lan_address?.split(":") ?? [];

  // Keep the card un-dimmed while a transition is in flight, even if status
  // flips mid-mutation — the "Stopping…/Starting…" badge is the better cue.
  const dimmed = !isRunning && !isRestarting && !isStopping && !isStarting;

  return (
    <>
      <div className={cn("border-border bg-card overflow-hidden rounded-xl border", dimmed && "opacity-60")}>
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
          className="flex w-full cursor-pointer items-center gap-3 px-4 py-3.5 text-left select-none active:bg-zinc-800/40 sm:py-4"
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
          {canTest && isRunning && !isTransitioning && (
            <InlineTestButton
              testing={testing}
              hasResult={!!conn}
              onClick={() => testMutation.mutate()}
            />
          )}
          {conn?.success && conn.ip_info && (
            <IpFlag info={conn.ip_info} className="ml-1" />
          )}
          {expanded ? (
            <ChevronUp className="text-muted h-5 w-5 shrink-0" />
          ) : (
            <ChevronDown className="text-muted h-5 w-5 shrink-0" />
          )}
        </div>

        {expanded && (
          <div className="border-border border-t px-3 pt-3 pb-3.5 sm:px-4 sm:pb-4">
            {container.lan_address && (
              <div className="mb-3 grid grid-cols-2 gap-2">
                {host && <CopyField label="Host" value={host} onCopy={() => copyToClipboard(host, "Host")} />}
                {port && <CopyField label="Port" value={port} onCopy={() => copyToClipboard(port, "Port")} />}
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              {isRunning ? (
                <ActionButton
                  onClick={() => setConfirm("stop")}
                  disabled={isTransitioning}
                  variant="destructive"
                  icon={
                    isStopping ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <PowerOff className="h-3.5 w-3.5" />
                    )
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
                    isStarting ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Power className="h-3.5 w-3.5" />
                    )
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
              <ActionButton onClick={() => setOpenModal("logs")} icon={<span className="font-mono text-[10px]">LOG</span>}>
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
          <ConfigModal
            containerName={container.name}
            displayName={container.dashboard.name}
            onClose={closeModal}
          />
        )}
        {openModal === "env" && (
          <EnvModal
            containerName={container.name}
            displayName={container.dashboard.name}
            onClose={closeModal}
          />
        )}
        {openModal === "logs" && (
          <LogsModal
            containerName={container.name}
            displayName={container.dashboard.name}
            onClose={closeModal}
          />
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
      {testing ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <RotateCw className="h-3.5 w-3.5" />
      )}
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
  if (state === "stopped") return <p className="text-xs text-zinc-500">Not running</p>;
  if (state === "transient") return <p className="text-muted text-xs">Updating…</p>;
  if (state === "unhealthy") return <p className="text-xs text-red-300">Container reports unhealthy</p>;
  if (!canTest) return <p className="text-muted text-xs">Running</p>;
  if (!conn) return <p className="text-muted text-xs">Not tested yet</p>;
  if (conn.success) return <p className="text-xs text-emerald-400">Connected · {conn.latency_ms}ms</p>;
  return <p className="text-destructive truncate text-xs">Cannot connect{conn.error ? ` — ${conn.error}` : ""}</p>;
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
  destructive:
    "text-red-300 hover:text-red-200 bg-red-500/15 hover:bg-red-500/25 active:bg-red-500/30",
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
