import { useCallback, useEffect, useRef, useState } from "react";
import { openStabilityStream } from "@/api/client";
import type { StabilityProgress, StabilityResult } from "@/types";

export interface StabilityCheckState {
  result: StabilityResult | null;
  progress: StabilityProgress | null;
  running: boolean;
  error: string | null;
  start: () => void;
}

/**
 * Drives a streamed stability probe for one proxy.
 *
 * The probe is long-running (~1 min: many spread-out connections + a sized
 * download), so we stream it and surface live progress. Every terminal path —
 * success, server error, or a dropped connection — flips `running` back to
 * false, so the UI can never get stuck on a spinner. Unmounting closes the
 * stream (which cancels the server-side probe).
 */
export function useStabilityCheck(name: string): StabilityCheckState {
  const [result, setResult] = useState<StabilityResult | null>(null);
  const [progress, setProgress] = useState<StabilityProgress | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  const start = useCallback(() => {
    sourceRef.current?.close();
    setRunning(true);
    setError(null);
    setResult(null);
    setProgress(null);
    sourceRef.current = openStabilityStream(name, {
      onProgress: setProgress,
      onResult: (r) => setResult(r),
      onError: (detail) => {
        setError(detail ?? "Connection lost — please try again.");
        setRunning(false);
        sourceRef.current = null;
      },
      onDone: () => {
        setRunning(false);
        sourceRef.current = null;
      },
    });
  }, [name]);

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, []);

  return { result, progress, running, error, start };
}
