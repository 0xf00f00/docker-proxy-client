import { useState, useRef, useCallback } from "react";
import { ArrowDown, ArrowUp, X, Play, Gauge, Loader2 } from "lucide-react";
import { cancelSpeedTest, openSpeedTestStream } from "@/api/client";
import type { SpeedTestProgress } from "@/types";
import { cn } from "@/utils/cn";

const PHASE_LABELS: Record<string, string> = {
  init: "Preparing...",
  server: "Finding best server...",
  download: "Testing download speed...",
  upload: "Testing upload speed...",
  done: "Complete",
  cancelled: "Cancelled",
  error: "Failed",
};

export default function SpeedTest() {
  const [state, setState] = useState<SpeedTestProgress | null>(null);
  const [running, setRunning] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  const start = useCallback(() => {
    if (running) return;
    setRunning(true);
    setState({
      phase: "init",
      download_mbps: null,
      upload_mbps: null,
      ping_ms: null,
      server: null,
      error: null,
    });

    eventSourceRef.current = openSpeedTestStream({
      onProgress: (data) => {
        setState(data);
        if (data.phase === "done" || data.phase === "cancelled" || data.phase === "error") {
          setRunning(false);
        }
      },
      onError: () => {
        setRunning(false);
        setState((prev) =>
          prev && prev.phase !== "done" ? { ...prev, phase: "error", error: "Connection lost" } : prev,
        );
      },
    });
  }, [running]);

  const cancel = useCallback(async () => {
    eventSourceRef.current?.close();
    await cancelSpeedTest();
    setRunning(false);
    setState((prev) => (prev ? { ...prev, phase: "cancelled" } : null));
  }, []);

  const isDone = state?.phase === "done";
  const isActive = running && state != null;
  const showDownload = state?.download_mbps != null;
  const showUpload = state?.upload_mbps != null;

  // No test run yet
  if (!state) {
    return (
      <button
        type="button"
        onClick={start}
        className="border-border bg-card flex w-full flex-col items-center gap-2 rounded-xl border p-5 transition-colors hover:bg-zinc-800/50 active:bg-zinc-800"
      >
        <Gauge className="text-muted h-6 w-6" />
        <span className="text-sm font-medium">Speed Test</span>
        <span className="text-muted text-xs">Tap to run</span>
      </button>
    );
  }

  return (
    <div className="border-border bg-card rounded-xl border p-5">
      {/* Results area */}
      <div className="mb-4 flex items-center justify-center gap-8">
        <ResultBlock
          icon={<ArrowDown className="h-4 w-4" />}
          label="Download"
          value={showDownload ? state.download_mbps : null}
          active={state.phase === "download"}
          color="text-sky-400"
        />
        <ResultBlock
          icon={<ArrowUp className="h-4 w-4" />}
          label="Upload"
          value={showUpload ? state.upload_mbps : null}
          active={state.phase === "upload"}
          color="text-violet-400"
        />
        {state.ping_ms != null && (
          <div className="flex flex-col items-center">
            <span className="text-muted text-lg font-bold tabular-nums">{state.ping_ms}</span>
            <span className="text-muted text-[10px]">Ping (ms)</span>
          </div>
        )}
      </div>

      {/* Status line */}
      <p className={cn("mb-4 text-center text-xs", state.phase === "error" ? "text-destructive" : "text-muted")}>
        {state.phase === "error" ? (state.error ?? "Test failed") : PHASE_LABELS[state.phase]}
        {state.server && !["server", "init"].includes(state.phase) && (
          <span className="text-zinc-600"> · {state.server}</span>
        )}
      </p>

      {/* Indeterminate progress during active phases */}
      {isActive && (
        <div className="mb-4 h-1 w-full overflow-hidden rounded-full bg-zinc-800">
          <div
            className={cn(
              "h-full w-1/3 animate-[shimmer_1.5s_ease-in-out_infinite] rounded-full",
              state.phase === "download" ? "bg-sky-400" : state.phase === "upload" ? "bg-violet-400" : "bg-zinc-600",
            )}
          />
        </div>
      )}

      {/* Action button */}
      <div className="flex justify-center">
        {running ? (
          <button
            type="button"
            onClick={cancel}
            className="text-muted hover:text-foreground flex items-center gap-1.5 rounded-lg bg-zinc-800 px-4 py-2 text-sm font-medium"
          >
            <X className="h-4 w-4" />
            Cancel
          </button>
        ) : (
          <button
            type="button"
            onClick={start}
            className="bg-primary text-primary-foreground hover:bg-primary/90 flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium"
          >
            <Play className="h-4 w-4" />
            {isDone ? "Test Again" : "Run Speed Test"}
          </button>
        )}
      </div>
    </div>
  );
}

function ResultBlock({
  icon,
  label,
  value,
  active,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | null;
  active: boolean;
  color: string;
}) {
  const showSpinner = active && value == null;
  return (
    <div className="flex flex-col items-center">
      {showSpinner ? (
        <Loader2 className={cn("mb-1 h-6 w-6 animate-spin", color)} />
      ) : (
        <span
          className={cn(
            "text-2xl font-bold tabular-nums",
            value != null ? color : "text-zinc-700",
            active && "animate-pulse",
          )}
        >
          {value != null ? value : "—"}
        </span>
      )}
      <div className={cn("flex items-center gap-1 text-[10px]", value != null ? "text-muted" : "text-zinc-700")}>
        {icon}
        <span>{label}</span>
      </div>
    </div>
  );
}
