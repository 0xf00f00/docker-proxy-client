import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Network, Globe, Wifi, Loader2, LogIn, LogOut, ArrowDown, ArrowUp, ChevronRight } from "lucide-react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { toast } from "sonner";
import { logout, fetchSystemHealth } from "@/api/client";
import { AUTH_STATUS_KEY, useAuth } from "@/hooks/useAuth";
import { useTraffic } from "@/hooks/useTraffic";
import { formatRate, formatRateShort } from "@/utils/format";
import Sparkline from "@/components/common/Sparkline";
import { ModalLoadingShell } from "@/components/common/Modal";
import { cn } from "@/utils/cn";

const ConnectionsModal = lazy(() => import("@/components/connections/ConnectionsModal"));

interface CheckResult {
  success: boolean;
  latency_ms: number;
}

type PillState = "good" | "slow" | "down" | "unknown";

// "Is my uplink up?"
const POLL_MS = 25_000;

const DNS_SLOW_MS = 250;
const NET_SLOW_MS = 600;

const DOWN_AFTER_FAILS = 2;

function classifyCheck(r: CheckResult, slowMs: number, prevFails: number): { state: PillState; fails: number } {
  if (r.success) return { state: r.latency_ms > slowMs ? "slow" : "good", fails: 0 };
  const fails = prevFails + 1;
  return { state: fails >= DOWN_AFTER_FAILS ? "down" : "slow", fails };
}

export default function Header({ pauseAutoRefresh = false }: { pauseAutoRefresh?: boolean }) {
  const [showConnections, setShowConnections] = useState(false);
  const health = useQuery({
    queryKey: ["system-health"],
    queryFn: fetchSystemHealth,
    refetchInterval: pauseAutoRefresh ? false : POLL_MS,
    retry: 0,
  });

  const fails = useRef({ dns: 0, net: 0 });
  const [pills, setPills] = useState<{ dns: PillState; net: PillState }>({ dns: "unknown", net: "unknown" });

  useEffect(() => {
    if (health.isError) {
      setPills({ dns: "unknown", net: "unknown" });
      return;
    }
    const data = health.data;
    if (!data) return;
    const dns = classifyCheck(data.dns, DNS_SLOW_MS, fails.current.dns);
    const net = classifyCheck(data.connectivity, NET_SLOW_MS, fails.current.net);
    fails.current = { dns: dns.fails, net: net.fails };
    setPills({ dns: dns.state, net: net.state });
  }, [health.data, health.isError]);

  const recheck = () => {
    health.refetch();
  };

  const openConnections = () => setShowConnections(true);

  return (
    <header className="border-border bg-card border-b" style={{ paddingTop: "env(safe-area-inset-top)" }}>
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-3 py-2.5 sm:px-4 sm:py-3">
        <div className="flex min-w-0 items-center gap-2 sm:gap-3">
          <Network className="text-primary h-5 w-5 shrink-0" />
          {/* The icon is the brand mark on phones; the wordmark only truncated to
              "Pr.." there, so show it from sm up where there's room. */}
          <h1 className="hidden truncate text-base font-bold sm:block sm:text-lg">Proxy Dashboard</h1>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
          <TrafficPill onOpen={openConnections} />
          <StatusPill
            icon={<Globe className="h-3.5 w-3.5" />}
            label="DNS"
            state={pills.dns}
            latencyMs={health.data?.dns.latency_ms}
            isFetching={health.isFetching}
            onClick={recheck}
          />
          <StatusPill
            icon={<Wifi className="h-3.5 w-3.5" />}
            label="Net"
            state={pills.net}
            latencyMs={health.data?.connectivity.latency_ms}
            isFetching={health.isFetching}
            onClick={recheck}
          />
          <AuthControl />
        </div>
      </div>
      <TrafficStrip onOpen={openConnections} />

      {showConnections && (
        <Suspense fallback={<ModalLoadingShell title="Connections" onClose={() => setShowConnections(false)} />}>
          <ConnectionsModal onClose={() => setShowConnections(false)} />
        </Suspense>
      )}
    </header>
  );
}

function SkeletonBar({ className }: { className?: string }) {
  return <span className={cn("inline-block animate-pulse rounded bg-zinc-700/70", className)} aria-hidden="true" />;
}

/**
 * Always-on "how busy is the network right now" readout: the system-proxy
 * total up/down with a 60s sparkline. Three states, so it never reads as broken:
 * a skeleton until the first sample arrives, an explicit "0" when genuinely idle
 * (with a flat line), and live numbers when active. Desktop only — the mobile
 * readout lives in TrafficStrip. Per-proxy detail lives on the cards.
 */
function TrafficPill({ onOpen }: { onOpen: () => void }) {
  const { system, systemHistory, status } = useTraffic();
  const loading = status === "connecting";
  // Stream dropped after being live: keep the last numbers but dim them so they
  // don't read as live. The connection strip carries the actual message.
  const stale = status === "reconnecting";
  const idle = !loading && system.down < 1 && system.up < 1;

  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "hidden min-h-9 items-center gap-1.5 rounded-full bg-zinc-800 px-2.5 hover:bg-zinc-700 sm:inline-flex",
        (loading || idle || stale) && "opacity-80",
      )}
      aria-label={
        loading
          ? "Network activity — connecting. Tap to view connections"
          : `Network activity — download ${formatRate(system.down)}, upload ${formatRate(system.up)}. Tap to view connections`
      }
      title="View live connections"
    >
      <Sparkline down={systemHistory.map((s) => s.down)} up={systemHistory.map((s) => s.up)} />
      <div className="flex flex-col gap-0.5 text-[10px] leading-none font-medium tabular-nums">
        <span className={cn("inline-flex items-center gap-0.5", loading || idle ? "text-zinc-500" : "text-sky-400")}>
          <ArrowDown className="h-2.5 w-2.5 shrink-0" />
          {loading ? (
            <SkeletonBar className="h-2 w-7" />
          ) : (
            <span className="min-w-[2.25rem]">{idle ? "0" : formatRateShort(system.down)}</span>
          )}
        </span>
        <span
          className={cn("inline-flex items-center gap-0.5", loading || idle ? "text-zinc-500" : "text-emerald-400")}
        >
          <ArrowUp className="h-2.5 w-2.5 shrink-0" />
          {loading ? (
            <SkeletonBar className="h-2 w-7" />
          ) : (
            <span className="min-w-[2.25rem]">{idle ? "0" : formatRateShort(system.up)}</span>
          )}
        </span>
      </div>
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-zinc-500" aria-hidden="true" />
    </button>
  );
}

/**
 * Mobile counterpart to TrafficPill: a slim full-width strip below the top
 * header row (sm:hidden). Phones have no spare width in the top row, but plenty
 * across a dedicated line — so this gets a wider sparkline and full-precision
 * rates ("1.2 MB/s"), plus an explicit "0 B/s" idle state and a loading skeleton.
 */
function TrafficStrip({ onOpen }: { onOpen: () => void }) {
  const { system, systemHistory, status } = useTraffic();
  const loading = status === "connecting";
  // Reconnecting: keep the last numbers, dimmed — the strip up top says why.
  const stale = status === "reconnecting";
  const idle = !loading && system.down < 1 && system.up < 1;

  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "border-border/60 mx-auto flex min-h-11 w-full max-w-3xl items-center gap-3 border-t px-3 py-1.5 text-left active:bg-zinc-800/50 sm:hidden",
        stale && "opacity-80",
      )}
      aria-label={
        loading
          ? "Network activity — connecting. Tap to view connections"
          : `Network activity — download ${formatRate(system.down)}, upload ${formatRate(system.up)}. Tap to view connections`
      }
    >
      <span className="text-muted text-[10px] font-medium tracking-wide uppercase">Network</span>
      <Sparkline down={systemHistory.map((s) => s.down)} up={systemHistory.map((s) => s.up)} width={72} height={18} />
      <div className="ml-auto flex items-center gap-3 text-xs tabular-nums">
        <span className={cn("inline-flex items-center gap-1", loading || idle ? "text-zinc-500" : "text-sky-400")}>
          <ArrowDown className="h-3 w-3 shrink-0" />
          {loading ? <SkeletonBar className="h-3 w-16" /> : idle ? "0 B/s" : formatRate(system.down)}
        </span>
        <span className={cn("inline-flex items-center gap-1", loading || idle ? "text-zinc-500" : "text-emerald-400")}>
          <ArrowUp className="h-3 w-3 shrink-0" />
          {loading ? <SkeletonBar className="h-3 w-16" /> : idle ? "0 B/s" : formatRate(system.up)}
        </span>
      </div>
      <ChevronRight className="h-4 w-4 shrink-0 text-zinc-500" aria-hidden="true" />
    </button>
  );
}

function AuthControl() {
  const { enabled, authenticated, showLogin } = useAuth();
  const qc = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!enabled) return null;

  const handleLogout = async () => {
    setBusy(true);
    try {
      await logout();
      toast.success("Signed out");
      setConfirmOpen(false);
    } finally {
      setBusy(false);
      qc.invalidateQueries({ queryKey: AUTH_STATUS_KEY });
    }
  };

  if (authenticated) {
    return (
      <>
        <button
          type="button"
          onClick={() => setConfirmOpen(true)}
          aria-haspopup="dialog"
          aria-label="Sign out"
          title="Sign out"
          className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
        >
          <LogOut className="h-4 w-4" />
        </button>
        <ConfirmDialog
          open={confirmOpen}
          onOpenChange={setConfirmOpen}
          title="Sign out?"
          message="You'll need to sign in again to make changes."
          confirmLabel="Sign out"
          variant="destructive"
          busy={busy}
          onConfirm={handleLogout}
        />
      </>
    );
  }

  return (
    <button
      type="button"
      onClick={showLogin}
      className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex min-h-9 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium"
    >
      <LogIn className="h-3.5 w-3.5" />
      <span>Sign in</span>
    </button>
  );
}

const STATE_WORD: Record<PillState, string> = {
  good: "online",
  slow: "slow",
  down: "down",
  unknown: "unknown",
};

function StatusPill({
  icon,
  label,
  state,
  latencyMs,
  isFetching,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  state: PillState;
  latencyMs: number | undefined;
  isFetching: boolean;
  onClick: () => void;
}) {
  const latencyText = latencyMs != null ? `${latencyMs}ms` : "—";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isFetching}
      aria-label={`${label} ${STATE_WORD[state]} — tap to recheck`}
      aria-busy={isFetching}
      className={cn(
        "relative inline-flex min-h-9 min-w-[5.25rem] items-center gap-1.5 rounded-full px-2.5 text-xs font-medium transition-colors duration-300",
        state === "good" && "bg-emerald-500/10 text-emerald-400",
        state === "slow" && "bg-amber-500/10 text-amber-400",
        state === "down" && "bg-red-500/10 text-red-400",
        state === "unknown" && "bg-zinc-800 text-zinc-400",
        isFetching && "opacity-90",
      )}
    >
      <span className="relative inline-flex h-3.5 w-3.5 items-center justify-center">
        <span
          className={cn(
            "absolute inset-0 inline-flex items-center justify-center transition-opacity duration-200",
            isFetching ? "opacity-0" : "opacity-100",
          )}
        >
          {icon}
        </span>
        <Loader2
          className={cn(
            "absolute inset-0 h-3.5 w-3.5 animate-spin transition-opacity duration-200",
            isFetching ? "opacity-100" : "opacity-0",
          )}
        />
      </span>
      <span>{label}</span>
      <span
        className={cn(
          "ml-auto min-w-[2.5rem] text-right text-[10px] tabular-nums",
          state === "unknown" ? "opacity-40" : "opacity-70",
        )}
      >
        {latencyText}
      </span>
    </button>
  );
}
