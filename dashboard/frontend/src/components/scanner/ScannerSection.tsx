import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Power, RotateCw } from "lucide-react";
import { toast } from "sonner";
import type { EdgeTest, ScannerStatus } from "@/types";
import { openScannerStream, runScan, startContainer, testEdge } from "@/api/client";
import { getErrorMessage } from "@/utils/errors";
import { cn } from "@/utils/cn";
import { ModalLoadingShell } from "@/components/common/Modal";

const LogsModal = lazy(() => import("@/components/common/LogsModal"));

const SPIN = <Loader2 className="h-3.5 w-3.5 animate-spin" />;
const LOG = <span className="font-mono text-[10px]">LOG</span>;

type LogTarget = { container: string; name: string } | null;
type Variant = "default" | "positive" | "destructive";

const VARIANT: Record<Variant, string> = {
  default: "text-muted hover:text-foreground bg-zinc-800 active:bg-zinc-700",
  positive: "text-emerald-300 hover:text-emerald-200 bg-emerald-500/15 hover:bg-emerald-500/25 active:bg-emerald-500/30",
  destructive: "text-red-300 hover:text-red-200 bg-red-500/15 hover:bg-red-500/25 active:bg-red-500/30",
};

export default function ScannerSection() {
  const [data, setData] = useState<ScannerStatus | null>(null);
  const [logs, setLogs] = useState<LogTarget>(null);
  const [showPool, setShowPool] = useState(false);
  const [pending, setPending] = useState(false);
  const baseline = useRef<string | null>(null);

  useEffect(() => {
    const es = openScannerStream({ onStatus: setData });
    return () => es.close();
  }, []);

  // Optimistic "Scanning…" from click until the scan demonstrably finishes
  // (last_scan advances) or a safety timeout — independent of the live marker.
  useEffect(() => {
    if (!pending) return;
    if (data?.last_scan && data.last_scan !== baseline.current) {
      setPending(false);
      return;
    }
    const t = setTimeout(() => setPending(false), 120_000);
    return () => clearTimeout(t);
  }, [pending, data?.last_scan]);

  const scan = useMutation({
    mutationFn: runScan,
    onSuccess: () => {
      toast.success("Scan started");
      baseline.current = data?.last_scan ?? null;
      setPending(true);
    },
    onError: (e) => toast.error(`Scan failed: ${getErrorMessage(e)}`),
  });
  const start = useMutation({
    mutationFn: () => startContainer("cf-edge-scanner"),
    onSuccess: () => toast.success("Scanner starting"),
    onError: (e) => toast.error(`Start failed: ${getErrorMessage(e)}`),
  });

  // Per-IP reliability test. Optimistic "testing…" from click until that IP's
  // result timestamp advances (or a timeout), independent of the live marker.
  const [pendingTest, setPendingTest] = useState<string | null>(null);
  const testBaseline = useRef<string | undefined>(undefined);
  const testMut = useMutation({
    mutationFn: (ip: string) => testEdge(ip),
    onError: (e) => toast.error(`Test failed: ${getErrorMessage(e)}`),
  });
  useEffect(() => {
    if (!pendingTest) return;
    const t = data?.tests?.[pendingTest];
    if (t && t.ts !== testBaseline.current) {
      setPendingTest(null);
      return;
    }
    const timer = setTimeout(() => setPendingTest(null), 60_000);
    return () => clearTimeout(timer);
  }, [pendingTest, data?.tests]);
  const onTest = (ip: string) => {
    testBaseline.current = data?.tests?.[ip]?.ts;
    setPendingTest(ip);
    testMut.mutate(ip);
  };
  const testingIp = pendingTest ?? data?.testing_ip ?? null;

  const running = data?.scanner_running ?? false;
  const scanning = (data?.scanning ?? false) || pending;
  const busy = scan.isPending || start.isPending;
  const stateLabel = scanning ? "Scanning…" : running ? "Idle" : "Off";

  return (
    <div>
      <h2 className="text-muted mb-3 text-xs font-medium tracking-wide uppercase">Clean-IP Scanner</h2>
      <div className="border-border bg-card overflow-hidden rounded-xl border">
        <div className="flex min-h-12 items-center gap-3 px-4 py-3">
          <span
            className={cn(
              "inline-block h-2.5 w-2.5 shrink-0 rounded-full",
              scanning ? "animate-pulse bg-emerald-500" : running ? "bg-sky-500" : "bg-zinc-600",
            )}
            aria-label={stateLabel}
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm">{stateLabel}</p>
            <p className="text-muted text-xs">
              Last scan {formatTime(data?.last_scan)} · Picker {data?.picker_running ? "on" : "off"}
            </p>
          </div>
        </div>

        <div className="border-border divide-border grid grid-cols-2 divide-x border-t">
          <Pick label="Primary edge" ip={data?.byedpi_ip} tests={data?.tests} testingIp={testingIp} onTest={onTest} />
          <Pick label="Backup edge" ip={data?.snispoof_ip} tests={data?.tests} testingIp={testingIp} onTest={onTest} />
        </div>

        {data && data.pool.length > 0 && (
          <div className="border-border border-t">
            <button
              type="button"
              onClick={() => setShowPool((v) => !v)}
              className="text-muted hover:text-foreground flex min-h-11 w-full items-center justify-between px-4 text-xs"
            >
              <span>{data.pool.length} candidate edges</span>
              <span>{showPool ? "Hide" : "Show"}</span>
            </button>
            {showPool && (
              <ul className="space-y-1.5 px-4 pb-3">
                {data.pool.map((ip) => {
                  const role = ip === data.byedpi_ip ? "primary" : ip === data.snispoof_ip ? "backup" : null;
                  return (
                    <li key={ip} className="flex items-center gap-2">
                      <span className={cn("flex-1 truncate font-mono text-xs", role ? "text-emerald-400" : "text-zinc-400")}>
                        {ip}
                        {role && <span className="text-muted"> · {role}</span>}
                      </span>
                      <EdgeResult test={data.tests?.[ip]} testing={testingIp === ip} />
                      <TestButton onClick={() => onTest(ip)} disabled={testingIp !== null} testing={testingIp === ip} />
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        <div className="border-border flex flex-wrap gap-2 border-t px-4 py-3">
          <Btn
            onClick={() => scan.mutate()}
            disabled={busy || scanning || !running}
            icon={scan.isPending || scanning ? SPIN : <RotateCw className="h-3.5 w-3.5" />}
          >
            {scanning ? "Scanning…" : "Scan now"}
          </Btn>
          {!running && (
            <Btn onClick={() => start.mutate()} disabled={busy} variant="positive" icon={start.isPending ? SPIN : <Power className="h-3.5 w-3.5" />}>
              Start
            </Btn>
          )}
          <Btn onClick={() => setLogs({ container: "cf-edge-scanner", name: "Scanner" })} icon={LOG}>
            Scanner logs
          </Btn>
          <Btn onClick={() => setLogs({ container: "cf-edge-picker", name: "Picker" })} icon={LOG}>
            Picker logs
          </Btn>
        </div>
      </div>

      <Suspense fallback={logs ? <ModalLoadingShell title={`${logs.name} — Logs`} onClose={() => setLogs(null)} /> : null}>
        {logs && <LogsModal containerName={logs.container} displayName={logs.name} onClose={() => setLogs(null)} />}
      </Suspense>
    </div>
  );
}

function Pick({
  label,
  ip,
  tests,
  testingIp,
  onTest,
}: {
  label: string;
  ip: string | null | undefined;
  tests: Record<string, EdgeTest> | undefined;
  testingIp: string | null;
  onTest: (ip: string) => void;
}) {
  return (
    <div className="px-4 py-3">
      <p className="text-muted text-[10px] uppercase">{label}</p>
      <p className="truncate font-mono text-sm">{ip ?? "—"}</p>
      {ip && (
        <div className="mt-1.5 flex items-center gap-2">
          <EdgeResult test={tests?.[ip]} testing={testingIp === ip} />
          <TestButton onClick={() => onTest(ip)} disabled={testingIp !== null} testing={testingIp === ip} />
        </div>
      )}
    </div>
  );
}

function EdgeResult({ test, testing }: { test?: EdgeTest; testing: boolean }) {
  if (testing) return <span className="text-muted inline-flex items-center gap-1 text-[10px]">{SPIN} testing…</span>;
  if (!test) return null;
  const loss = Math.round(test.loss * 100);
  const cls = loss === 0 ? "text-emerald-400" : loss <= 20 ? "text-amber-400" : "text-red-400";
  return (
    <span className={cn("text-[10px] tabular-nums", cls)} title={`${test.received}/${test.sent} reachable`}>
      {loss === 0 ? "stable" : `${loss}% loss`} · {Math.round(test.latency_ms)}ms
    </span>
  );
}

function TestButton({ onClick, disabled, testing }: { onClick: () => void; disabled: boolean; testing: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="text-muted hover:text-foreground ml-auto shrink-0 rounded bg-zinc-800 px-2.5 py-1.5 text-[10px] font-medium active:bg-zinc-700 disabled:opacity-50"
    >
      {testing ? "…" : "Test"}
    </button>
  );
}

function Btn({
  onClick,
  disabled,
  icon,
  variant = "default",
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  icon: React.ReactNode;
  variant?: Variant;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex min-h-10 items-center gap-1.5 rounded-lg px-3.5 text-xs font-medium disabled:opacity-50",
        VARIANT[variant],
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  return new Date(iso).toLocaleString();
}
