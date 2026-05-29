import { useState, useEffect, useRef, useCallback, useLayoutEffect } from "react";
import { Pause, Play, Trash2 } from "lucide-react";
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
const MAX_LINES = 5000;

export default function LogsModal({ containerName, displayName, onClose }: Props) {
  const [lines, setLines] = useState<string[]>([]);
  const [streaming, setStreaming] = useState(true);
  const [streamError, setStreamError] = useState<string | null>(null);

  const esRef = useRef<EventSource | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // While live, pin to the bottom on every render that changes line count.
  // useLayoutEffect runs after DOM mutation but before paint, so we never show
  // a flash of "not-at-bottom" in between new lines arriving.
  useLayoutEffect(() => {
    if (!streaming) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines, streaming]);

  const open = useCallback(
    (tail: number) => {
      setStreamError(null);
      setStreaming(true);
      esRef.current = openLogStream(containerName, tail, {
        onLine: (text) =>
          setLines((prev) => (prev.length < MAX_LINES ? [...prev, text] : [...prev.slice(-MAX_LINES + 1), text])),
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

  const hasContent = lines.length > 0;
  const isLiveBadge = streaming && !streamError;

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${displayName} — Logs`}
      subtitle={
        <div className="flex items-center gap-2">
          <StatusBadge live={isLiveBadge} error={streamError} />
          <span className="text-muted text-[11px]">
            {lines.length} {lines.length === 1 ? "line" : "lines"}
            {lines.length >= MAX_LINES && " (capped)"}
          </span>
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
            onClick={() => setLines([])}
            label="Clear logs"
            icon={<Trash2 className="h-4 w-4" />}
            disabled={!hasContent}
          />
        </>
      }
    >
      <div ref={scrollRef} className="h-full overflow-auto">
        {!hasContent ? (
          <div className="text-muted flex h-full items-center justify-center text-sm">
            {streaming ? "Waiting for log output…" : "No logs."}
          </div>
        ) : (
          <pre className="p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-zinc-300 sm:p-4 sm:text-xs">
            {lines.join("\n")}
          </pre>
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
