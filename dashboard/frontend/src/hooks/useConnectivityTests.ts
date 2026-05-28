import { useCallback, useEffect, useRef, useState } from "react";
import { openConnectivityStream } from "@/api/client";
import type { ConnectivityResult } from "@/types";

const REFRESH_INTERVAL_MS = 60_000;

export interface ConnectivityState {
  results: Record<string, ConnectivityResult>;
  testing: Set<string>;
  running: boolean;
  start: () => void;
}

/**
 * Runs connectivity tests automatically whenever the set of tracked proxy
 * names (passed as a stable string key) changes, plus on a periodic refresh.
 * Exposes a `start` callback so callers can also trigger tests manually.
 */
export function useConnectivityTests(proxyKey: string): ConnectivityState {
  const [results, setResults] = useState<Record<string, ConnectivityResult>>({});
  const [testing, setTesting] = useState<Set<string>>(new Set());
  const [running, setRunning] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);

  const start = useCallback(() => {
    sourceRef.current?.close();
    setRunning(true);
    sourceRef.current = openConnectivityStream({
      onServices: (services) => setTesting(new Set(services)),
      onResult: (result) => {
        setResults((prev) => ({ ...prev, [result.service]: result }));
        setTesting((prev) => {
          if (!prev.has(result.service)) return prev;
          const next = new Set(prev);
          next.delete(result.service);
          return next;
        });
      },
      onDone: () => {
        setTesting(new Set());
        setRunning(false);
      },
      onError: () => {
        setTesting(new Set());
        setRunning(false);
      },
    });
  }, []);

  // Single subscription effect: kick off immediately, refresh periodically, and
  // close any in-flight stream when the proxy set changes or the consumer unmounts.
  useEffect(() => {
    if (!proxyKey) return;
    start();
    const interval = setInterval(start, REFRESH_INTERVAL_MS);
    return () => {
      clearInterval(interval);
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, [proxyKey, start]);

  return { results, testing, running, start };
}
