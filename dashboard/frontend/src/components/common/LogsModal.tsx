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

// Docker `timestamps=True` prefixes every line with an RFC3339 timestamp.
const TS_RE = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) /;

function splitLines(text: string): string[] {
  const lines = text.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

// Oldest timestamp held in the buffer — the cursor for paging further back.
// RFC3339 is fixed-width so the raw string sorts chronologically; epoch (ms
// precision, enough for the `until` window) is what the backend wants.
function oldestTimestamp(buffer: string): { iso: string; epoch: number } | null {
  for (const line of splitLines(buffer)) {
    const iso = TS_RE.exec(line)?.[1];
    if (!iso) continue;
    const epoch = Date.parse(iso.replace(/(\.\d{3})\d+Z$/, "$1Z")) / 1000;
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

  const esRef = useRef<EventSource | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const mountRef = useRef<HTMLDivElement>(null);
  const bufferRef = useRef("");
  const loadingOlderRef = useRef(false);
  const exhaustedRef = useRef(false);
  const loadOlderRef = useRef<() => void>(() => {});
  // During a history rewrite, live lines are held here instead of being written
  // to the terminal, so the bottom doesn't grow mid-rewrite and throw off the
  // viewport anchor. Flushed once the rewrite settles.
  const rewritingRef = useRef(false);
  const pendingLiveRef = useRef("");

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

          // Scroll near the top → pull the previous page of history.
          term.onScroll((viewportY) => {
            if (viewportY <= NEAR_TOP_ROWS) loadOlderRef.current();
          });

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

  const open = useCallback(
    (tail: number) => {
      setStreamError(null);
      setStreaming(true);
      esRef.current = openLogStream(containerName, tail, {
        onChunk: (text) => {
          setHasOutput(true);
          const next = bufferRef.current + text;
          bufferRef.current = next.length > MAX_BUFFER ? next.slice(-MAX_BUFFER) : next;
          // Mid-rewrite: stash for the post-rewrite flush instead of writing now
          // (writing would move the bottom and skew the scroll anchor).
          if (rewritingRef.current) pendingLiveRef.current += text;
          else termRef.current?.write(text);
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
    [containerName],
  );

  const close = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    setStreaming(false);
  }, []);

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

  useEffect(() => {
    loadOlderRef.current = loadOlder;
  }, [loadOlder]);

  useEffect(() => {
    open(INITIAL_TAIL);
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, [open]);

  const handlePauseResume = () => (streaming ? close() : open(RESUME_TAIL));

  const handleClear = () => {
    bufferRef.current = "";
    pendingLiveRef.current = "";
    rewritingRef.current = false;
    termRef.current?.reset();
    exhaustedRef.current = false;
    setHistoryExhausted(false);
    setHasOutput(false);
  };

  const isLiveBadge = streaming && !streamError;

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${displayName} — Logs`}
      subtitle={
        <div className="flex items-center gap-2">
          <StatusBadge live={isLiveBadge} error={streamError} />
        </div>
      }
      headerActions={
        <>
          <IconButton
            onClick={handlePauseResume}
            label={streaming ? "Pause streaming" : "Resume streaming"}
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

function StatusBadge({ live, error }: { live: boolean; error: string | null }) {
  if (error) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-400">
        <span className="h-1.5 w-1.5 rounded-full bg-red-400" />
        {error}
      </span>
    );
  }
  if (live) {
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
      PAUSED · scroll to browse
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
