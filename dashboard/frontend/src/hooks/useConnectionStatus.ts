import { useEffect, useRef, useState } from "react";
import { useStreamConnection } from "@/hooks/useTraffic";

export type ConnectionStatus = "live" | "reconnecting" | "offline";

// A reconnect must persist this long before we say anything
const RECONNECT_GRACE_MS = 2_000;

/** App-wide "is the dashboard talking to its server" signal for the UI. */
export function useConnectionStatus(): ConnectionStatus {
  const raw = useStreamConnection();
  const [status, setStatus] = useState<ConnectionStatus>("live");
  // Lets the grace logic see what's currently on screen without re-subscribing.
  const shownRef = useRef<ConnectionStatus>("live");
  shownRef.current = status;

  useEffect(() => {
    // "connecting" is the brief initial window before the first snapshot
    if (raw === "live" || raw === "connecting") {
      setStatus("live");
      return;
    }
    if (raw === "offline") {
      setStatus("offline");
      return;
    }
    if (shownRef.current !== "live") {
      setStatus("reconnecting");
      return;
    }
    const t = setTimeout(() => setStatus("reconnecting"), RECONNECT_GRACE_MS);
    return () => clearTimeout(t);
  }, [raw]);

  return status;
}
