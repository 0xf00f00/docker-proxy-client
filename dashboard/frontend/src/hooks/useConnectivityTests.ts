import { useCallback, useEffect, useRef, useState } from "react";
import { openConnectivityStream } from "@/api/client";
import type { ConnectivityResult } from "@/types";

export interface ConnectivityState {
  results: Record<string, ConnectivityResult>;
  testing: Set<string>;
  running: boolean;
  start: () => void;
}

/**
 * Runs connectivity tests when the consumer calls `start()`.
 *
 * If `initialDelayMs > 0`, fires once on mount after that delay so the user
 * gets a baseline result without clicking — but never on a recurring schedule,
 * since periodic probes add network load that can interfere with active speed
 * tests and produce false negatives on limited uplinks.
 */
export function useConnectivityTests({ initialDelayMs = 0 }: { initialDelayMs?: number } = {}): ConnectivityState {
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
        sourceRef.current?.close();
        sourceRef.current = null;
        setTesting(new Set());
        setRunning(false);
      },
    });
  }, []);

  useEffect(() => {
    const timer = initialDelayMs > 0 ? setTimeout(start, initialDelayMs) : null;
    return () => {
      if (timer) clearTimeout(timer);
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, [initialDelayMs, start]);

  return { results, testing, running, start };
}
