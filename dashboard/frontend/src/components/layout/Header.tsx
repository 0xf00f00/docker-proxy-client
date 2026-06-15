import { lazy, Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Network, Globe, Wifi, WifiOff, Loader2, LogIn, LogOut, ArrowDown, ArrowUp, ChevronRight } from "lucide-react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { toast } from "sonner";
import { logout, fetchHealthCheck, fetchHealthCurrent } from "@/api/client";
import type { HealthCurrent } from "@/types";
import { AUTH_STATUS_KEY, useAuth } from "@/hooks/useAuth";
import { useTraffic } from "@/hooks/useTraffic";
import { formatRate, formatRateShort } from "@/utils/format";
import Sparkline from "@/components/common/Sparkline";
import { ModalLoadingShell } from "@/components/common/Modal";
import { cn } from "@/utils/cn";

const ConnectionsModal = lazy(() => import("@/components/connections/ConnectionsModal"));

type PillState = "good" | "slow" | "down" | "unknown";

// How often the header re-reads the shared health snapshot while the page is open.
const POLL_MS = 25_000;
const NET_SLOW_MS = 600;

type BadgeIcon = "up" | "down" | "dns";

const BADGE_ICON: Record<BadgeIcon, React.ReactNode> = {
  up: <Wifi className="h-3.5 w-3.5" />,
  down: <WifiOff className="h-3.5 w-3.5" />,
  dns: <Globe className="h-3.5 w-3.5" />,
};

interface Badge {
  state: PillState;
  label: string;
  icon: BadgeIcon;
  latencyMs?: number;
}

function netBadge(cur: HealthCurrent | null): Badge {
  if (!cur) return { state: "unknown", label: "Checking", icon: "up" };
  if (cur.reachable) {
    const slow = (cur.latencyMs ?? 0) > NET_SLOW_MS;
    return { state: slow ? "slow" : "good", label: slow ? "Slow" : "Online", icon: "up", latencyMs: cur.latencyMs ?? undefined };
  }
  if (cur.dns && !cur.dns.success) return { state: "down", label: "DNS", icon: "dns" };
  return { state: "down", label: "Offline", icon: "down" };
}

export default function Header({ pauseAutoRefresh = false }: { pauseAutoRefresh?: boolean }) {
  const [showConnections, setShowConnections] = useState(false);
  const qc = useQueryClient();
  const health = useQuery({
    queryKey: ["health-current"],
    queryFn: fetchHealthCurrent,
    refetchInterval: pauseAutoRefresh ? false : POLL_MS,
    retry: 0,
  });
  // Tap-to-recheck forces an immediate probe; the result is shared with the
  // health card via the same query cache key.
  const recheck = useMutation({
    mutationFn: fetchHealthCheck,
    onSuccess: (data) => qc.setQueryData(["health-current"], data),
  });

  const cur = health.data?.current ?? null;
  const badge = netBadge(cur);
  const busy = health.isFetching || recheck.isPending;
  const doRecheck = () => recheck.mutate();

  const trackingEnabled = health.data?.connectionTracking ?? false;
  const openConnections = trackingEnabled ? () => setShowConnections(true) : undefined;

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
            icon={BADGE_ICON[badge.icon]}
            label={badge.label}
            state={badge.state}
            latencyMs={badge.latencyMs}
            isFetching={busy}
            onClick={doRecheck}
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
function TrafficPill({ onOpen }: { onOpen?: () => void }) {
  const { system, systemHistory, status } = useTraffic();
  const loading = status === "connecting";
  // Stream dropped after being live: keep the last numbers but dim them so they
  // don't read as live. The connection strip carries the actual message.
  const stale = status === "reconnecting";
  const idle = !loading && system.down < 1 && system.up < 1;
  const interactive = !!onOpen;
  const tap = interactive ? ". Tap to view connections" : "";

  const className = cn(
    "hidden min-h-9 items-center gap-1.5 rounded-full bg-zinc-800 px-2.5 sm:inline-flex",
    interactive && "hover:bg-zinc-700",
    (loading || idle || stale) && "opacity-80",
  );
  const label = loading
    ? `Network activity — connecting${tap}`
    : `Network activity — download ${formatRate(system.down)}, upload ${formatRate(system.up)}${tap}`;

  const inner = (
    <>
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
      {interactive && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-zinc-500" aria-hidden="true" />}
    </>
  );

  if (!interactive) {
    return (
      <div className={className} role="img" aria-label={label}>
        {inner}
      </div>
    );
  }
  return (
    <button type="button" onClick={onOpen} className={className} aria-label={label} title="View live connections">
      {inner}
    </button>
  );
}

/**
 * Mobile counterpart to TrafficPill: a slim full-width strip below the top
 * header row (sm:hidden). Phones have no spare width in the top row, but plenty
 * across a dedicated line — so this gets a wider sparkline and full-precision
 * rates ("1.2 MB/s"), plus an explicit "0 B/s" idle state and a loading skeleton.
 */
function TrafficStrip({ onOpen }: { onOpen?: () => void }) {
  const { system, systemHistory, status } = useTraffic();
  const loading = status === "connecting";
  // Reconnecting: keep the last numbers, dimmed — the strip up top says why.
  const stale = status === "reconnecting";
  const idle = !loading && system.down < 1 && system.up < 1;
  const interactive = !!onOpen;
  const tap = interactive ? ". Tap to view connections" : "";

  const className = cn(
    "border-border/60 mx-auto flex min-h-11 w-full max-w-3xl items-center gap-3 border-t px-3 py-1.5 text-left sm:hidden",
    interactive && "active:bg-zinc-800/50",
    stale && "opacity-80",
  );
  const label = loading
    ? `Network activity — connecting${tap}`
    : `Network activity — download ${formatRate(system.down)}, upload ${formatRate(system.up)}${tap}`;

  const inner = (
    <>
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
      {interactive && <ChevronRight className="h-4 w-4 shrink-0 text-zinc-500" aria-hidden="true" />}
    </>
  );

  if (!interactive) {
    return (
      <div className={className} role="img" aria-label={label}>
        {inner}
      </div>
    );
  }
  return (
    <button type="button" onClick={onOpen} className={className} aria-label={label}>
      {inner}
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
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isFetching}
      aria-label={`Network status: ${label}. Tap to recheck.`}
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
      {latencyMs != null && (
        <span className="ml-auto text-right text-[10px] tabular-nums opacity-70">{latencyMs}ms</span>
      )}
    </button>
  );
}
