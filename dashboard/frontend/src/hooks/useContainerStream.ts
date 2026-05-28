import { useEffect, useState } from "react";
import { openContainerStream } from "@/api/client";
import type { ContainerListResponse } from "@/types";

export type ContainerStreamState =
  | { kind: "initial" }
  | { kind: "ready"; data: ContainerListResponse }
  | { kind: "error"; message: string };

/**
 * Subscribe to the container SSE stream. The server pushes a snapshot
 * immediately on connect, so a separate one-shot fetch is unnecessary.
 */
export function useContainerStream(): ContainerStreamState {
  const [state, setState] = useState<ContainerStreamState>({ kind: "initial" });

  useEffect(() => {
    const es = openContainerStream({
      onSnapshot: (data) => setState({ kind: "ready", data }),
      onError: (detail) => setState((prev) => (prev.kind === "ready" ? prev : { kind: "error", message: detail })),
    });
    return () => es.close();
  }, []);

  return state;
}
