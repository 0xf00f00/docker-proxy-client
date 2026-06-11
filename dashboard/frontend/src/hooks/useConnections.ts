import { useEffect, useRef, useState } from "react";
import { openConnectionsStream } from "@/api/client";
import type { ConnectionSite, ConnectionsSnapshot } from "@/types";

const HISTORY_LEN = 60; // ~60s of samples for the summary sparkline.
// Ended sites stay in the list until the modal closes; cap how many we keep.
const ENDED_CAP = 60;

// "connecting"   = no snapshot yet (show a skeleton, not an empty state).
// "live"         = receiving snapshots (an empty `sites` legitimately means idle).
// "reconnecting" = stream dropped after being live; keep showing the last data.
export type ConnectionsStatus = "connecting" | "live" | "reconnecting";

/** A site as rendered: the raw group plus where it is in its lifecycle. */
export interface DisplaySite extends ConnectionSite {
  // "live" = currently open; "ended" = its last connection closed (kept, dimmed).
  phase: "live" | "ended";
}

export interface ConnectionsState {
  status: ConnectionsStatus;
  snapshot: ConnectionsSnapshot | null;
  /**
   * Sites to render: live ones merged with recently-closed ones still in their
   * linger window, held in stable *arrival* order so live rate swings never
   * reorder the list under the user's eyes.
   */
  sites: DisplaySite[];
  /** Rolling combined-rate history for the summary sparkline. */
  rateHistory: { down: number; up: number }[];
}

interface Tracked {
  site: ConnectionSite;
  phase: "live" | "ended";
  /** Arrival sequence — pins a row's position so it never jumps under live rates. */
  seq: number;
  /** performance.now() when the site ended; 0 while live (used to evict oldest). */
  closedAt: number;
}

/**
 * Live connections feed
 */
export function useConnections(): ConnectionsState {
  // Per-host lifecycle state, persisted across snapshots for this modal session.
  const tracked = useRef(new Map<string, Tracked>());
  const seqRef = useRef(0);
  const [state, setState] = useState<ConnectionsState>({
    status: "connecting",
    snapshot: null,
    sites: [],
    rateHistory: [],
  });

  useEffect(() => {
    let es: EventSource | null = null;

    const ingest = (snap: ConnectionsSnapshot) => {
      const map = tracked.current;
      const now = performance.now();
      const liveHosts = new Set<string>();

      // Upsert everything currently open, keeping each host's arrival seq.
      for (const site of snap.sites) {
        liveHosts.add(site.host);
        const ex = map.get(site.host);
        map.set(site.host, { site, phase: "live", seq: ex?.seq ?? seqRef.current++, closedAt: 0 });
      }

      // Anything no longer present: mark it ended and keep it (don't remove).
      for (const [host, t] of map) {
        if (liveHosts.has(host) || t.phase === "ended") continue;
        map.set(host, {
          ...t,
          phase: "ended",
          closedAt: now,
          // Keep cumulative bytes (what it transferred) but zero live rates.
          site: {
            ...t.site,
            downRate: 0,
            upRate: 0,
            connections: t.site.connections.map((c) => ({ ...c, downRate: 0, upRate: 0 })),
          },
        });
      }

      const ended = [...map.entries()].filter(([, t]) => t.phase === "ended");
      if (ended.length > ENDED_CAP) {
        ended
          .sort((a, b) => a[1].closedAt - b[1].closedAt)
          .slice(0, ended.length - ENDED_CAP)
          .forEach(([host]) => map.delete(host));
      }

      const sites: DisplaySite[] = [...map.values()]
        // Live on top (newest first), ended below (most-recently-ended first).
        .sort((a, b) => {
          if ((a.phase === "ended") !== (b.phase === "ended")) return a.phase === "ended" ? 1 : -1;
          return a.phase === "ended" ? b.closedAt - a.closedAt : b.seq - a.seq;
        })
        .map((t) => ({ ...t.site, phase: t.phase }));

      setState((prev) => ({
        status: "live",
        snapshot: snap,
        sites,
        rateHistory: [...prev.rateHistory, { down: snap.totals.downRate, up: snap.totals.upRate }].slice(-HISTORY_LEN),
      }));
    };

    const open = () => {
      if (es || document.hidden) return;
      es = openConnectionsStream({
        onSnapshot: ingest,
        // EventSource reconnects on its own; just reflect the gap once we were live.
        onError: () => setState((prev) => (prev.status === "live" ? { ...prev, status: "reconnecting" } : prev)),
      });
    };
    const close = () => {
      es?.close();
      es = null;
    };
    const onVisibility = () => {
      if (document.hidden) {
        close();
        setState((prev) => (prev.status === "live" ? { ...prev, status: "reconnecting" } : prev));
      } else {
        open();
      }
    };

    open();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      close();
    };
  }, []);

  return state;
}
