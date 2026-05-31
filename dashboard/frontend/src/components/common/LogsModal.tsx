import { useState, useEffect, useRef, useCallback } from "react";
import { Loader2, Pause, Play, Trash2 } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { openLogStream } from "@/api/client";
import { cn } from "@/utils/cn";
import Modal from "@/components/common/Modal";

interface Props {
  containerName: string;
  displayName: string;
  onClose: () => void;
}

const INITIAL_TAIL = 200;
const RESUME_TAIL = 0;
const SCROLLBACK = 5000;
const MAX_BUFFER = 1_000_000;

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

  const esRef = useRef<EventSource | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const mountRef = useRef<HTMLDivElement>(null);
  const bufferRef = useRef("");

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
          termRef.current?.write(text);
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
    termRef.current?.reset();
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
