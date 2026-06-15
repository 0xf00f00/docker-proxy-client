import { useState, useEffect, useRef, useCallback } from "react";
import { Loader2, Pause, Play, Trash2 } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { openLogStream, fetchLogHistory } from "@/api/client";
import { cn } from "@/utils/cn";
import Modal from "@/components/common/Modal";

interface Props {
  containerName: string;
  displayName: string;
  onClose: () => void;
}

const INITIAL_TAIL = 200;
const RESUME_TAIL = 0;
const SCROLLBACK = 50_000;
const MAX_BUFFER = 16_000_000;
const HISTORY_CHUNK = 500; // lines fetched per scroll-up page
const NEAR_TOP_ROWS = 2; // trigger a history fetch within this many rows of the top
const BOTTOM_ROWS = 2; // within this many rows of the bottom counts as "following live"

// Docker `timestamps=True` prefixes every line with an RFC3339 timestamp.
const TS_RE = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) /;

function splitLines(text: string): string[] {
  const lines = text.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

function isoToEpoch(iso: string): number {
  return Date.parse(iso.replace(/(\.\d{3})\d+Z$/, "$1Z")) / 1000;
}

// Oldest timestamp held in the buffer — the cursor for paging further back.
// RFC3339 is fixed-width so the raw string sorts chronologically; epoch (ms
// precision, enough for the `until` window) is what the backend wants.
function oldestTimestamp(buffer: string): { iso: string; epoch: number } | null {
  for (const line of splitLines(buffer)) {
    const iso = TS_RE.exec(line)?.[1];
    if (!iso) continue;
    const epoch = isoToEpoch(iso);
    return Number.isNaN(epoch) ? null : { iso, epoch };
  }
  return null;
}

// Newest timestamp held — the cursor for resuming the live stream with `since`
// so the gap accrued while browsing history is backfilled, not lost.
function newestTimestamp(buffer: string): { iso: string; epoch: number } | null {
  const lines = splitLines(buffer);
  for (let i = lines.length - 1; i >= 0; i--) {
    const iso = TS_RE.exec(lines[i] ?? "")?.[1];
    if (!iso) continue;
    const epoch = isoToEpoch(iso);
    return Number.isNaN(epoch) ? null : { iso, epoch };
  }
  return null;
}

// Keep only lines strictly older than `boundaryIso`, dropping the overlap the
// `until` window re-includes. Untimestamped continuation lines ride along.
function olderThan(text: string, boundaryIso: string): string {
  const kept = splitLines(text).filter((line) => {
    const iso = TS_RE.exec(line)?.[1];
    return !(iso && iso >= boundaryIso);
  });
  return kept.length ? kept.join("\n") + "\n" : "";
}

const TERMINAL_THEME = {
  background: "#09090b", // zinc-950
  foreground: "#d4d4d8", // zinc-300
  cursor: "#09090b", // hide the cursor in a read-only viewer (= background)
  selectionBackground: "#3f3f46", // zinc-700
};

export default function LogsModal({ containerName, displayName, onClose }: Props) {
  const [streaming, setStreaming] = useState(true);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [hasOutput, setHasOutput] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [historyExhausted, setHistoryExhausted] = useState(false);
  // User explicitly hit Pause: stay paused even back at the bottom (vs. the
  // automatic pause that scrolling up triggers, which resumes at the bottom).
  const [manualPaused, setManualPaused] = useState(false);

  const esRef = useRef<EventSource | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const mountRef = useRef<HTMLDivElement>(null);
  const bufferRef = useRef("");
  const loadingOlderRef = useRef(false);
  const exhaustedRef = useRef(false);
  const loadOlderRef = useRef<() => void>(() => {});
  const handleScrollRef = useRef<(viewportY: number) => void>(() => {});
  const manualPausedRef = useRef(false);
  // During a history rewrite, live lines are held here instead of being written
  // to the terminal, so the bottom doesn't grow mid-rewrite and throw off the
  // viewport anchor. Flushed once the rewrite settles.
  const rewritingRef = useRef(false);
  const pendingLiveRef = useRef("");
  // After resuming with `since`, Docker re-sends the boundary second; drop lines
  // we already hold (compared by RFC3339 string, which sorts chronologically).
  const dedupeRef = useRef<{ boundary: string; remainder: string } | null>(null);

  useEffect(() => {
    let term: Terminal | null = null;
    let fit: FitAddon | null = null;
    let raf = 0;
    let ro: ResizeObserver | null = null;
    let attempts = 0;

    const tick = () => {
      const mount = mountRef.current;
      const w = mount?.clientWidth ?? 0;
      const h = mount?.clientHeight ?? 0;

      if (mount && w > 0 && h > 0) {
        if (!term) {
          term = new Terminal({
            convertEol: true, // docker logs are \n-only; render \n as a full CR+LF
            scrollback: SCROLLBACK,
            disableStdin: true,
            cursorBlink: false,
            fontSize: 12,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
            theme: TERMINAL_THEME,
          });
          fit = new FitAddon();
          term.loadAddon(fit);
          term.open(mount);
          termRef.current = term;
          if (bufferRef.current) term.write(bufferRef.current); // replay backlog

          term.onScroll((viewportY) => handleScrollRef.current(viewportY));

          // Once open, keep the terminal fitted to later size changes too.
          ro = new ResizeObserver(() => {
            try {
              fit?.fit();
            } catch {
              /* corrected on the next resize */
            }
          });
          ro.observe(mount);
        }
        try {
          fit?.fit();
        } catch {
          /* measurement not ready yet; the next frame retries */
        }
        if (term.cols !== 80 || term.rows !== 24) return; // settled — stop polling
      }
      if (++attempts < 180) raf = requestAnimationFrame(tick); // ~3s safety cap
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
      term?.dispose();
      term = null;
      termRef.current = null;
    };
  }, []);

  // Filter the freshly-resumed stream against what we already hold. Operates
  // line-wise (buffering the trailing partial line) until the first genuinely
  // newer line, then disables itself so live throughput isn't taxed.
  const applyDedupe = useCallback((text: string): string => {
    const st = dedupeRef.current;
    if (!st) return text;
    const data = st.remainder + text;
    const nl = data.lastIndexOf("\n");
    if (nl === -1) {
      st.remainder = data;
      return "";
    }
    st.remainder = data.slice(nl + 1);
    const out: string[] = [];
    for (const line of data.slice(0, nl + 1).split("\n")) {
      if (line === "") continue;
      if (!dedupeRef.current) {
        out.push(line);
        continue;
      }
      const iso = TS_RE.exec(line)?.[1];
      if (iso && iso <= st.boundary) continue; // already shown — drop the dupe
      dedupeRef.current = null; // first new line: stop filtering from here on
      out.push(line);
    }
    let result = out.length ? out.join("\n") + "\n" : "";
    if (!dedupeRef.current && st.remainder) {
      result += st.remainder; // filtering done — let the partial line through
      st.remainder = "";
    }
    return result;
  }, []);

  const open = useCallback(
    (params: { tail?: number; since?: number }) => {
      setStreamError(null);
      setStreaming(true);
      if (params.since) {
        const newest = newestTimestamp(bufferRef.current);
        dedupeRef.current = newest ? { boundary: newest.iso, remainder: "" } : null;
      } else {
        dedupeRef.current = null;
      }
      esRef.current = openLogStream(containerName, params, {
        onChunk: (text) => {
          const incoming = applyDedupe(text);
          if (!incoming) return;
          setHasOutput(true);
          const next = bufferRef.current + incoming;
          bufferRef.current = next.length > MAX_BUFFER ? next.slice(-MAX_BUFFER) : next;
          // Mid-rewrite: stash for the post-rewrite flush instead of writing now
          // (writing would move the bottom and skew the scroll anchor).
          if (rewritingRef.current) pendingLiveRef.current += incoming;
          else termRef.current?.write(incoming);
        },
        onOpen: () => {
          // Fires on initial connect AND every successful reconnect. After
          // a 401, the SSE wrapper pops the login modal but leaves the
          // EventSource alive; once the new cookie lands the next retry
          // lands here and the stream is back without any extra plumbing.
          setStreaming(true);
          setStreamError(null);
        },
        onStreamError: (detail) => setStreamError(detail),
        onEnd: () => setStreaming(false),
        onError: () => {
          // EventSource auto-reconnects; don't close. onOpen will clear
          // this error if/when the connection recovers.
          setStreamError("Connection lost");
          setStreaming(false);
        },
      });
    },
    [containerName, applyDedupe],
  );

  const close = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    dedupeRef.current = null;
    setStreaming(false);
  }, []);

  // Resume live from the newest line we hold so nothing emitted while browsing
  // is lost, and jump to the bottom to actually follow it.
  const resumeLive = useCallback(() => {
    if (esRef.current) return;
    const newest = newestTimestamp(bufferRef.current);
    open(newest ? { since: newest.epoch } : { tail: RESUME_TAIL });
    termRef.current?.scrollToBottom();
  }, [open]);

  // Fetch the page of logs preceding the oldest line we hold and prepend it,
  // keeping the user anchored on the same content. xterm has no prepend, so we
  // rewrite the full buffer and shift the viewport down by the rows we grew by.
  const loadOlder = useCallback(async () => {
    const term = termRef.current;
    if (!term || loadingOlderRef.current || exhaustedRef.current) return;
    const oldest = oldestTimestamp(bufferRef.current);
    if (!oldest) return;

    loadingOlderRef.current = true;
    setLoadingOlder(true);
    try {
      // +1ms so the window covers everything strictly before `oldest`;
      // olderThan() trims the boundary line the window re-includes.
      const text = await fetchLogHistory(containerName, oldest.epoch + 0.001, HISTORY_CHUNK);
      const older = olderThan(text, oldest.iso);
      if (!older) {
        exhaustedRef.current = true;
        setHistoryExhausted(true);
        return;
      }

      // Freeze live terminal writes: from here until the rewrite settles, new
      // lines accumulate in pendingLiveRef so `grew` reflects only the prepend.
      rewritingRef.current = true;
      const oldViewportY = term.buffer.active.viewportY;
      const oldLength = term.buffer.active.length;
      bufferRef.current = older + bufferRef.current;
      if (bufferRef.current.length > MAX_BUFFER) {
        // In-memory ceiling reached; keep newest, stop paging further back.
        bufferRef.current = bufferRef.current.slice(-MAX_BUFFER);
        exhaustedRef.current = true;
        setHistoryExhausted(true);
      }

      const snapshot = bufferRef.current;
      term.reset();
      term.write(snapshot, () => {
        // newLength reflects only the prepend (live writes were held), so the
        // grow is purely the rows added above the user's position.
        const grew = term.buffer.active.length - oldLength;
        term.scrollToLine(Math.max(0, oldViewportY + grew));
        // xterm's scrollback cap can swallow the prepended lines (grew===0)
        // once total history fills it — nothing more to surface.
        if (grew === 0) {
          exhaustedRef.current = true;
          setHistoryExhausted(true);
        }
        // Unfreeze and flush the live lines that arrived during the rewrite;
        // they land at the bottom, below the user's anchored view.
        rewritingRef.current = false;
        const pending = pendingLiveRef.current;
        pendingLiveRef.current = "";
        if (pending) term.write(pending);
      });
    } catch {
      // Transient failure — leave it retryable on the next scroll.
      rewritingRef.current = false;
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, [containerName]);

  // Scroll position drives live-follow: scrolling up off the bottom drops the
  // live connection (stops the jumping AND frees a connection slot for history
  // under the browser's per-host cap); returning to the bottom resumes it.
  const handleScroll = useCallback(
    (viewportY: number) => {
      const term = termRef.current;
      if (!term) return;
      // Ignore the programmatic scrolls our own history rewrite produces.
      if (rewritingRef.current || loadingOlderRef.current) return;

      const atBottom = viewportY >= term.buffer.active.baseY - BOTTOM_ROWS;
      if (atBottom) {
        if (!esRef.current && !manualPausedRef.current) resumeLive();
        return;
      }
      if (esRef.current) close(); // scrolled up → auto-pause live
      if (viewportY <= NEAR_TOP_ROWS) loadOlderRef.current();
    },
    [resumeLive, close],
  );

  useEffect(() => {
    loadOlderRef.current = loadOlder;
    handleScrollRef.current = handleScroll;
  }, [loadOlder, handleScroll]);

  useEffect(() => {
    open({ tail: INITIAL_TAIL });
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, [open]);

  const handlePauseResume = () => {
    if (streaming) {
      manualPausedRef.current = true;
      setManualPaused(true);
      close();
    } else {
      manualPausedRef.current = false;
      setManualPaused(false);
      resumeLive();
    }
  };

  const handleClear = () => {
    bufferRef.current = "";
    pendingLiveRef.current = "";
    rewritingRef.current = false;
    dedupeRef.current = null;
    termRef.current?.reset();
    exhaustedRef.current = false;
    setHistoryExhausted(false);
    setHasOutput(false);
  };

  const mode: "live" | "history" | "paused" = streaming
    ? "live"
    : manualPaused
      ? "paused"
      : "history";

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${displayName} — Logs`}
      subtitle={
        <div className="flex items-center gap-2">
          <StatusBadge mode={mode} error={streamError} />
        </div>
      }
      headerActions={
        <>
          <IconButton
            onClick={handlePauseResume}
            label={streaming ? "Pause streaming" : "Resume live"}
            icon={streaming ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          />
          <IconButton
            onClick={handleClear}
            label="Clear logs"
            icon={<Trash2 className="h-4 w-4" />}
            disabled={!hasOutput}
          />
        </>
      }
    >
      <div className="absolute inset-0 bg-[#09090b]">
        <div ref={mountRef} className="absolute inset-0 p-2 sm:p-3" />
        {(loadingOlder || historyExhausted) && hasOutput && (
          <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-center p-1.5">
            <span className="text-muted inline-flex items-center gap-1.5 rounded-full bg-zinc-900/90 px-2 py-0.5 text-[10px] font-medium">
              {loadingOlder ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Loading earlier logs…
                </>
              ) : (
                "Start of available logs"
              )}
            </span>
          </div>
        )}
        {!hasOutput && (
          <div className="text-muted pointer-events-none absolute inset-0 flex items-center justify-center gap-2 text-sm">
            {streaming && !streamError ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                <span>Connecting to log stream…</span>
              </>
            ) : (
              <span>{streamError ?? "No logs."}</span>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

function StatusBadge({ mode, error }: { mode: "live" | "history" | "paused"; error: string | null }) {
  if (error) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-400">
        <span className="h-1.5 w-1.5 rounded-full bg-red-400" />
        {error}
      </span>
    );
  }
  if (mode === "live") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
        LIVE
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">
      <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
      {mode === "history" ? "HISTORY · scroll down for live" : "PAUSED"}
    </span>
  );
}

function IconButton({
  onClick,
  label,
  icon,
  disabled = false,
}: {
  onClick: () => void;
  label: string;
  icon: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={cn(
        "text-muted hover:text-foreground inline-flex h-10 w-10 items-center justify-center rounded-lg active:bg-zinc-800",
        disabled && "opacity-40",
      )}
    >
      {icon}
    </button>
  );
}
