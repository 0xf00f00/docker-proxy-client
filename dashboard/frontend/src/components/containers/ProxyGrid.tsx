import type { ContainerInfo, ConnectivityResult } from "@/types";
import ProxyCard from "./ProxyCard";

interface Props {
  proxies: ContainerInfo[];
  connectivityResults: Record<string, ConnectivityResult>;
  testingSet?: Set<string>;
}

export default function ProxyGrid({ proxies, connectivityResults, testingSet }: Props) {
  if (proxies.length === 0) {
    return <p className="text-muted py-8 text-center text-sm">No proxy services found.</p>;
  }

  const running = proxies.filter((p) => p.status === "running");
  const stopped = proxies.filter((p) => p.status !== "running");

  return (
    <div className="space-y-2.5 sm:space-y-3">
      {running.map((p) => (
        <ProxyCard
          key={p.id}
          container={p}
          connectivity={connectivityResults[p.name] ?? null}
          isTesting={testingSet?.has(p.name) ?? false}
        />
      ))}
      {stopped.map((p) => (
        <ProxyCard key={p.id} container={p} connectivity={null} isTesting={false} />
      ))}
    </div>
  );
}
