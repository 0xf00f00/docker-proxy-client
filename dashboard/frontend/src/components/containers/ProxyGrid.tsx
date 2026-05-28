import { useRef } from "react";
import type { ContainerInfo, ConnectivityResult } from "@/types";
import ProxyCard from "./ProxyCard";

interface Props {
  proxies: ContainerInfo[];
  connectivityResults: Record<string, ConnectivityResult>;
  testingSet?: Set<string>;
}

/**
 * Stable visual order for the proxy list.
 *
 * The first time a set of containers appears (and any time one is added or
 * removed), we split running-first / stopped-after and remember that order.
 * After that, we keep the same order even as individual containers transition
 * between running and stopped — so clicking Stop doesn't snap a card down to
 * the "off" section mid-interaction. Order resets on reload.
 */
function useStableOrder(proxies: ContainerInfo[]): ContainerInfo[] {
  const orderRef = useRef<string[]>([]);

  const byName = new Map(proxies.map((p) => [p.name, p]));
  const prevSet = new Set(orderRef.current);
  const setMatches =
    orderRef.current.length === proxies.length && proxies.every((p) => prevSet.has(p.name));

  if (!setMatches) {
    const running = proxies.filter((p) => p.status === "running").map((p) => p.name);
    const stopped = proxies.filter((p) => p.status !== "running").map((p) => p.name);
    orderRef.current = [...running, ...stopped];
  }

  // Filter out anything no longer present (defensive; should match after the recompute above).
  return orderRef.current.flatMap((name) => {
    const p = byName.get(name);
    return p ? [p] : [];
  });
}

export default function ProxyGrid({ proxies, connectivityResults, testingSet }: Props) {
  const ordered = useStableOrder(proxies);

  if (proxies.length === 0) {
    return <p className="text-muted py-8 text-center text-sm">No proxy services found.</p>;
  }

  return (
    <div className="space-y-2.5 sm:space-y-3">
      {ordered.map((p) => (
        <ProxyCard
          key={p.id}
          container={p}
          connectivity={connectivityResults[p.name] ?? null}
          isTesting={testingSet?.has(p.name) ?? false}
        />
      ))}
    </div>
  );
}
