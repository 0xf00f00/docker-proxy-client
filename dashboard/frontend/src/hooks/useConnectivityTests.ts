import { useCallback, useEffect, useRef, useState } from "react";
import { fetchConnectivityResults, openConnectivityStream } from "@/api/client";
import type { ConnectivityResult } from "@/types";

export interface ConnectivityState {
  results: Record<string, ConnectivityResult>;
  testing: Set<string>;
  running: boolean;
  start: () => void;
  recordResult: (result: ConnectivityResult) => void;
}

/**
 * Manages the dashboard's connectivity results
 */
export function useConnectivityTests(): ConnectivityState {
  const [results, setResults] = useState<Record<string, ConnectivityResult>>({});
  const [testing, setTesting] = useState<Set<string>>(new Set());
  const [running, setRunning] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);

  const recordResult = useCallback((result: ConnectivityResult) => {
    setResults((prev) => ({ ...prev, [result.service]: result }));
  }, []);

  // Open the streamed probe. `maxAgeS === 0` forces a full re-test; omitting it
  // lets the backend probe only stale/missing proxies and replay the fresh ones.
  const run = useCallback((maxAgeS?: number) => {
    sourceRef.current?.close();
    setRunning(true);
    sourceRef.current = openConnectivityStream(
      {
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
          sourceRef.current?.close();
          sourceRef.current = null;
          setTesting(new Set());
          setRunning(false);
        },
      },
      { maxAgeS },
    );
  }, []);

  // Manual "Test All": force a fresh probe of every proxy regardless of age.
  const start = useCallback(() => run(0), [run]);

  useEffect(() => {
    let cancelled = false;
    fetchConnectivityResults()
      .then(({ results: cached }) => {
        if (cancelled || cached.length === 0) return;
        setResults((prev) => {
          const next = { ...prev };
          for (const r of cached) next[r.service] = r;
          return next;
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, []);

  return { results, testing, running, start, recordResult };
}
