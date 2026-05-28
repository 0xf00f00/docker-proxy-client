import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { Network, Globe, Wifi, Loader2 } from "lucide-react";
import { testSystemDns, testSystemConnectivity } from "@/api/client";
import { cn } from "@/utils/cn";

interface CheckResult {
  success: boolean;
  latency_ms: number;
}

const POLL_MS = 30_000;

export default function Header() {
  const dns = useQuery({
    queryKey: ["system-dns"],
    queryFn: testSystemDns,
    refetchInterval: POLL_MS,
    retry: 0,
  });
  const conn = useQuery({
    queryKey: ["system-connectivity"],
    queryFn: testSystemConnectivity,
    refetchInterval: POLL_MS,
    retry: 0,
  });

  const recheck = () => {
    dns.refetch();
    conn.refetch();
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
          <StatusPill icon={<Globe className="h-3.5 w-3.5" />} label="DNS" query={dns} onClick={recheck} />
          <StatusPill icon={<Wifi className="h-3.5 w-3.5" />} label="Net" query={conn} onClick={recheck} />
        </div>
      </div>
    </header>
  );
}

function StatusPill({
  icon,
  label,
  query,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  query: UseQueryResult<CheckResult>;
  onClick: () => void;
}) {
  const { data, isError, isFetching, isPending } = query;
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
