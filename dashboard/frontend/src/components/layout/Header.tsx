import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Network, Globe, Wifi, Loader2, LogIn, LogOut, ArrowDown, ArrowUp } from "lucide-react";
import { toast } from "sonner";
import { logout, fetchSystemHealth } from "@/api/client";
import { AUTH_STATUS_KEY, useAuth } from "@/hooks/useAuth";
import { useTraffic } from "@/hooks/useTraffic";
import { formatRate, formatRateShort } from "@/utils/format";
import Sparkline from "@/components/common/Sparkline";
import { cn } from "@/utils/cn";

interface CheckResult {
  success: boolean;
  latency_ms: number;
}

// "Is my uplink up?" — a periodic active measurement (one DNS lookup + one HEAD
// to gstatic), now a single round trip. Polling pauses while a speed test or
// "Test All" is in flight so we don't contend with the user's active
// measurement.
const POLL_MS = 25_000;

export default function Header({ pauseAutoRefresh = false }: { pauseAutoRefresh?: boolean }) {
  const health = useQuery({
    queryKey: ["system-health"],
    queryFn: fetchSystemHealth,
    refetchInterval: pauseAutoRefresh ? false : POLL_MS,
    retry: 0,
  });

  // One fetch backs both pills. On error, blank both so they fail together
  // rather than showing stale halves; `isFetching`/`isPending` are shared.
  const recheck = () => {
    health.refetch();
  };

  return (
    <header
      className="border-border bg-card border-b"
      style={{ paddingTop: "env(safe-area-inset-top)" }}
    >
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-3 py-2.5 sm:px-4 sm:py-3">
        <div className="flex min-w-0 items-center gap-2 sm:gap-3">
          <Network className="text-primary h-5 w-5 shrink-0" />
          {/* The icon is the brand mark on phones; the wordmark only truncated to
              "Pr.." there, so show it from sm up where there's room. */}
          <h1 className="hidden truncate text-base font-bold sm:block sm:text-lg">Proxy Dashboard</h1>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
          <TrafficPill />
          <StatusPill
            icon={<Globe className="h-3.5 w-3.5" />}
            label="DNS"
            data={health.isError ? undefined : health.data?.dns}
            isError={health.isError}
            isFetching={health.isFetching}
            isPending={health.isPending}
            onClick={recheck}
          />
          <StatusPill
            icon={<Wifi className="h-3.5 w-3.5" />}
            label="Net"
            data={health.isError ? undefined : health.data?.connectivity}
            isError={health.isError}
            isFetching={health.isFetching}
            isPending={health.isPending}
            onClick={recheck}
          />
          <AuthButton />
        </div>
      </div>
      <TrafficStrip />
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
function TrafficPill() {
  const { system, systemHistory, status } = useTraffic();
  const loading = status === "connecting";
  const idle = !loading && system.down < 1 && system.up < 1;

  return (
    <div
      className={cn(
        "hidden min-h-9 items-center gap-1.5 rounded-full bg-zinc-800 px-2.5 sm:inline-flex",
        (loading || idle) && "opacity-80",
      )}
      aria-label={loading ? "Network activity — connecting" : `Network activity — download ${formatRate(system.down)}, upload ${formatRate(system.up)}`}
      title="Live network activity (system proxy)"
    >
      <Sparkline down={systemHistory.map((s) => s.down)} up={systemHistory.map((s) => s.up)} />
      <div className="flex flex-col gap-0.5 text-[10px] leading-none font-medium tabular-nums">
        <span className={cn("inline-flex items-center gap-0.5", loading || idle ? "text-zinc-500" : "text-sky-400")}>
          <ArrowDown className="h-2.5 w-2.5 shrink-0" />
          {loading ? <SkeletonBar className="h-2 w-7" /> : <span className="min-w-[2.25rem]">{idle ? "0" : formatRateShort(system.down)}</span>}
        </span>
        <span className={cn("inline-flex items-center gap-0.5", loading || idle ? "text-zinc-500" : "text-emerald-400")}>
          <ArrowUp className="h-2.5 w-2.5 shrink-0" />
          {loading ? <SkeletonBar className="h-2 w-7" /> : <span className="min-w-[2.25rem]">{idle ? "0" : formatRateShort(system.up)}</span>}
        </span>
      </div>
    </div>
  );
}

/**
 * Mobile counterpart to TrafficPill: a slim full-width strip below the top
 * header row (sm:hidden). Phones have no spare width in the top row, but plenty
 * across a dedicated line — so this gets a wider sparkline and full-precision
 * rates ("1.2 MB/s"), plus an explicit "0 B/s" idle state and a loading skeleton.
 */
function TrafficStrip() {
  const { system, systemHistory, status } = useTraffic();
  const loading = status === "connecting";
  const idle = !loading && system.down < 1 && system.up < 1;

  return (
    <div
      className="border-border/60 mx-auto flex max-w-3xl items-center gap-3 border-t px-3 py-1.5 sm:hidden"
      aria-label={loading ? "Network activity — connecting" : `Network activity — download ${formatRate(system.down)}, upload ${formatRate(system.up)}`}
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
    </div>
  );
}

function AuthButton() {
  const { enabled, authenticated, showLogin } = useAuth();
  const qc = useQueryClient();

  if (!enabled) return null;

  const handleLogout = async () => {
    try {
      await logout();
      toast.success("Signed out");
    } finally {
      qc.invalidateQueries({ queryKey: AUTH_STATUS_KEY });
    }
  };

  if (authenticated) {
    return (
      <button
        type="button"
        onClick={handleLogout}
        className="inline-flex min-h-9 items-center gap-1.5 rounded-full bg-zinc-800 px-2.5 text-xs font-medium text-zinc-300 hover:bg-zinc-700"
      >
        <LogOut className="h-3.5 w-3.5" />
        <span>Sign out</span>
      </button>
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
  data,
  isError,
  isFetching,
  isPending,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  data: CheckResult | undefined;
  isError: boolean;
  isFetching: boolean;
  isPending: boolean;
  onClick: () => void;
}) {
  // Treat the latest attempt as authoritative: if it errored, ignore stale `data`
  // so the pill collapses to a clean failure state instead of mixing red + a
  // ghost latency from the previous success.
  const showError = isError;
  const success = !showError && data?.success === true;
  const failed = showError || (data != null && !data.success);

  // Reserve a fixed slot for the latency text so the pill width stays stable
  // through loading, success, and failure transitions.
  const latencyText = showError ? "—" : data ? `${data.latency_ms}ms` : isPending ? "" : "—";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isFetching}
      aria-label={`${label} status — tap to recheck`}
      aria-busy={isFetching}
      className={cn(
        "relative inline-flex min-h-9 min-w-[5.25rem] items-center gap-1.5 rounded-full px-2.5 text-xs font-medium transition-colors duration-300",
        success && "bg-emerald-500/10 text-emerald-400",
        failed && "bg-red-500/10 text-red-400",
        !success && !failed && "bg-zinc-800 text-zinc-400",
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
      <span className="ml-auto min-w-[2.5rem] text-right text-[10px] tabular-nums opacity-70">{latencyText}</span>
    </button>
  );
}
