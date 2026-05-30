import { useRef } from "react";
import type { ContainerInfo, ConnectivityResult } from "@/types";
import ProxyCard from "./ProxyCard";

interface Props {
  proxies: ContainerInfo[];
  connectivityResults: Record<string, ConnectivityResult>;
  testingSet?: Set<string>;
  running?: boolean;
  onResult?: (result: ConnectivityResult) => void;
}

function rankKey(p: ContainerInfo, conn: ConnectivityResult | undefined): [number, number] {
  if (p.status !== "running") return [4, Infinity];
  if (conn?.success) return [0, conn.latency_ms ?? Infinity];
  if (conn) return [3, Infinity]; // probed and failed
  if (p.health === "unhealthy") return [2, Infinity];
  return [1, Infinity];
}

function rankedNames(proxies: ContainerInfo[], results: Record<string, ConnectivityResult>): string[] {
  return [...proxies]
    .sort((a, b) => {
      const [ta, la] = rankKey(a, results[a.name]);
      const [tb, lb] = rankKey(b, results[b.name]);
      if (ta !== tb) return ta - tb;
      if (la !== lb) return la - lb;
      return a.dashboard.name.localeCompare(b.dashboard.name);
    })
    .map((p) => p.name);
}

/**
 * Order the proxy list by health and connection latency
 */
function useHealthOrder(
  proxies: ContainerInfo[],
  results: Record<string, ConnectivityResult>,
  running: boolean,
): ContainerInfo[] {
  const orderRef = useRef<string[]>([]);
  const prevRunningRef = useRef(false);

  const byName = new Map(proxies.map((p) => [p.name, p]));
  const prevSet = new Set(orderRef.current);
  const setMatches = orderRef.current.length === proxies.length && proxies.every((p) => prevSet.has(p.name));

  const justSettled = prevRunningRef.current && !running;
  prevRunningRef.current = running;

  if (orderRef.current.length === 0 || !setMatches || justSettled) {
    orderRef.current = rankedNames(proxies, results);
  }

  // Filter out anything no longer present (defensive; should match after the recompute above).
  return orderRef.current.flatMap((name) => {
    const p = byName.get(name);
    return p ? [p] : [];
  });
}

export default function ProxyGrid({ proxies, connectivityResults, testingSet, running = false, onResult }: Props) {
  const ordered = useHealthOrder(proxies, connectivityResults, running);

  if (proxies.length === 0) {
    return <p className="text-muted py-8 text-center text-sm">No proxy services found.</p>;
  }

  return (
    <div className="space-y-2.5 sm:space-y-3">
      {ordered.map((p) => (
        <ProxyCard
          key={p.name}
          container={p}
          connectivity={connectivityResults[p.name] ?? null}
          isTesting={testingSet?.has(p.name) ?? false}
          onTestResult={onResult}
        />
      ))}
    </div>
  );
}
