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
 * Drives the stability probe for one proxy.
 *
 * The probe is heavy and DISRUPTS live users while it runs (it briefly saturates
 * the tunnel), so it's only ever started by an explicit user action. Every
 * terminal path — success, server error, or a dropped stream — clears `running`,
 * so the UI can't get stuck on a spinner. Unmount closes the stream (which
 * cancels the server-side probe).
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
