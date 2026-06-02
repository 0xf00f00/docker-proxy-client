import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Power, RotateCw, Square } from "lucide-react";
import { toast } from "sonner";
import type { EdgeTest, ScannerStatus } from "@/types";
import { cancelScan, openScannerStream, runScan, startContainer, testEdge } from "@/api/client";
import { getErrorMessage } from "@/utils/errors";
import { cn } from "@/utils/cn";
import { ModalLoadingShell } from "@/components/common/Modal";

const loadLogsModal = () => import("@/components/common/LogsModal");
const LogsModal = lazy(loadLogsModal);

const SPIN = <Loader2 className="h-3.5 w-3.5 animate-spin" />;
const LOG = <span className="font-mono text-[10px]">LOG</span>;

type LogTarget = { container: string; name: string } | null;
type Variant = "default" | "positive" | "destructive";

// In-progress phases of a per-IP test, in the order they occur.
type TestPhase = "testing" | "queued-scan" | "queued" | "starting";
const PHASE_LABEL: Record<TestPhase, string> = {
  testing: "testing…",
  "queued-scan": "queued · scan running",
  queued: "queued…",
  starting: "starting…",
};

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

  useEffect(() => {
    void loadLogsModal();
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
  const cancel = useMutation({
    mutationFn: cancelScan,
    onSuccess: () => {
      toast.success("Stopping scan");
      setPending(false);
    },
    onError: (e) => toast.error(`Stop failed: ${getErrorMessage(e)}`),
  });
  const start = useMutation({
    mutationFn: () => startContainer("cf-edge-manager"),
    onSuccess: () => toast.success("Manager starting"),
    onError: (e) => toast.error(`Start failed: ${getErrorMessage(e)}`),
  });

  // Per-IP reliability test.
  const [pendingTest, setPendingTest] = useState<string | null>(null);
  const testBaseline = useRef<string | undefined>(undefined);
  const sawInflight = useRef(false);
  const testMut = useMutation({
    mutationFn: (ip: string) => testEdge(ip),
    onSuccess: (res, ip) => {
      // Cooldown hit
      if (!res.pending) {
        toast.info(`Skipped — ${ip} was tested recently`);
        setPendingTest((cur) => (cur === ip ? null : cur));
      }
    },
    onError: (e) => toast.error(`Test failed: ${getErrorMessage(e)}`),
  });
  useEffect(() => {
    if (!pendingTest) return;
    const ip = pendingTest;
    const t = data?.tests?.[ip];
    if (t && t.ts !== testBaseline.current) {
      setPendingTest(null);
      return;
    }
    const running = data?.scanner_running ?? false;
    const inflight = running && (!!data?.test_pending || data?.testing_ip === ip || !!data?.scanning);
    if (inflight) {
      sawInflight.current = true;
      const timer = setTimeout(() => setPendingTest(null), 15 * 60_000); // hard backstop
      return () => clearTimeout(timer);
    }
    if (sawInflight.current) {
      const timer = setTimeout(() => {
        toast.error("Test produced no result — check scanner logs");
        setPendingTest(null);
      }, 8_000);
      return () => clearTimeout(timer);
    }
    // Never observed in flight: scanner didn't pick it up. Give it a window, then
    // give up rather than spin.
    const timer = setTimeout(() => {
      if (!(data?.scanner_running ?? false)) toast.error("Scanner isn't running — start it, then test");
      setPendingTest(null);
    }, 30_000);
    return () => clearTimeout(timer);
  }, [pendingTest, data?.tests, data?.test_pending, data?.testing_ip, data?.scanning, data?.scanner_running]);
  const onTest = (ip: string) => {
    testBaseline.current = data?.tests?.[ip]?.ts;
    sawInflight.current = false;
    setPendingTest(ip);
    testMut.mutate(ip);
  };

  // The in-progress phase to show for an IP, or null if it isn't being tested.
  const testPhase = (ip: string): TestPhase | null => {
    if (data?.testing_ip === ip) return "testing";
    if (pendingTest !== ip) return null;
    if (data?.scanning) return "queued-scan";
    if (data?.test_pending) return "queued";
    return "starting";
  };
  // Block new tests while one is pending/running (avoid piling up requests).
  const testBusy = pendingTest !== null || data?.testing_ip != null;

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
          <Pick label="Primary edge" ip={data?.byedpi_ip} tests={data?.tests} phaseFor={testPhase} busy={testBusy} running={running} onTest={onTest} />
          <Pick label="Backup edge" ip={data?.snispoof_ip} tests={data?.tests} phaseFor={testPhase} busy={testBusy} running={running} onTest={onTest} />
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
                      <EdgeResult test={data.tests?.[ip]} phase={testPhase(ip)} />
                      <TestButton onClick={() => onTest(ip)} disabled={testBusy || !running} busy={testPhase(ip) !== null} />
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        <div className="border-border flex flex-wrap gap-2 border-t px-4 py-3">
          {scanning ? (
            <Btn
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
              variant="destructive"
              icon={cancel.isPending ? SPIN : <Square className="h-3.5 w-3.5" />}
            >
              {cancel.isPending ? "Stopping…" : "Stop scan"}
            </Btn>
          ) : (
            <Btn
              onClick={() => scan.mutate()}
              disabled={busy || !running}
              icon={scan.isPending ? SPIN : <RotateCw className="h-3.5 w-3.5" />}
            >
              Scan now
            </Btn>
          )}
          {!running && (
            <Btn onClick={() => start.mutate()} disabled={busy} variant="positive" icon={start.isPending ? SPIN : <Power className="h-3.5 w-3.5" />}>
              Start
            </Btn>
          )}
          <Btn onClick={() => setLogs({ container: "cf-edge-manager", name: "Manager" })} icon={LOG}>
            Manager logs
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
  phaseFor,
  busy,
  running,
  onTest,
}: {
  label: string;
  ip: string | null | undefined;
  tests: Record<string, EdgeTest> | undefined;
  phaseFor: (ip: string) => TestPhase | null;
  busy: boolean;
  running: boolean;
  onTest: (ip: string) => void;
}) {
  return (
    <div className="px-4 py-3">
      <p className="text-muted text-[10px] uppercase">{label}</p>
      <p className="truncate font-mono text-sm">{ip ?? "—"}</p>
      {ip && (
        <div className="mt-1.5 flex items-center gap-2">
          <EdgeResult test={tests?.[ip]} phase={phaseFor(ip)} />
          <TestButton onClick={() => onTest(ip)} disabled={busy || !running} busy={phaseFor(ip) !== null} />
        </div>
      )}
    </div>
  );
}

function EdgeResult({ test, phase }: { test?: EdgeTest; phase: TestPhase | null }) {
  if (phase) return <span className="text-muted inline-flex items-center gap-1 text-[10px]">{SPIN} {PHASE_LABEL[phase]}</span>;
  if (!test) return null;
  const loss = Math.round(test.loss * 100);
  const cls = loss === 0 ? "text-emerald-400" : loss <= 20 ? "text-amber-400" : "text-red-400";
  return (
    <span className={cn("text-[10px] tabular-nums", cls)} title={`${test.received}/${test.sent} reachable`}>
      {loss === 0 ? "stable" : `${loss}% loss`} · {Math.round(test.latency_ms)}ms
    </span>
  );
}

function TestButton({ onClick, disabled, busy }: { onClick: () => void; disabled: boolean; busy: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="text-muted hover:text-foreground ml-auto shrink-0 rounded bg-zinc-800 px-2.5 py-1.5 text-[10px] font-medium active:bg-zinc-700 disabled:opacity-50"
    >
      {busy ? "…" : "Test"}
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
