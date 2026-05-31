import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Network, Globe, Wifi, Loader2, LogIn, LogOut } from "lucide-react";
import { toast } from "sonner";
import { logout, fetchSystemHealth } from "@/api/client";
import { AUTH_STATUS_KEY, useAuth } from "@/hooks/useAuth";
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
      className="border-border bg-card/95 supports-[backdrop-filter]:bg-card/80 sticky top-0 z-30 border-b backdrop-blur"
      style={{ paddingTop: "env(safe-area-inset-top)" }}
    >
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-3 py-2.5 sm:px-4 sm:py-3">
        <div className="flex min-w-0 items-center gap-2 sm:gap-3">
          <Network className="text-primary h-5 w-5 shrink-0" />
          <h1 className="truncate text-base font-bold sm:text-lg">Proxy Dashboard</h1>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
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
    </header>
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
