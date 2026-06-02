import { useEffect, useState } from "react";
import { Loader2, WifiOff, RotateCw } from "lucide-react";
import { useConnectionStatus } from "@/hooks/useConnectionStatus";
import { retryStreamConnection } from "@/hooks/useTraffic";
import { cn } from "@/utils/cn";

export default function ConnectionBanner() {
  const status = useConnectionStatus();
  const visible = status !== "live";

  // Defer the visible state one frame so the slide-down transition actually runs
  // on mount instead of snapping into place.
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    if (!visible) {
      setEntered(false);
      return;
    }
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, [visible]);

  if (!visible) return null;

  const offline = status === "offline";
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "sticky top-0 z-40 flex items-center justify-center gap-2 px-3 py-2 text-center text-xs font-medium transition-all duration-300 ease-out",
        entered ? "translate-y-0 opacity-100" : "-translate-y-full opacity-0",
        offline ? "bg-red-500/15 text-red-300" : "bg-amber-500/15 text-amber-300",
      )}
      style={{ paddingTop: "calc(env(safe-area-inset-top) + 0.5rem)" }}
    >
      {offline ? (
        <WifiOff className="h-3.5 w-3.5 shrink-0" />
      ) : (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
      )}
      <span>{offline ? "Can't reach the dashboard" : "Reconnecting…"}</span>
      {offline && (
        <button
          type="button"
          onClick={retryStreamConnection}
          className="ml-1 inline-flex min-h-7 items-center gap-1 rounded-full bg-red-500/20 px-2.5 font-semibold text-red-200 hover:bg-red-500/30"
        >
          <RotateCw className="h-3 w-3 shrink-0" />
          Retry
        </button>
      )}
    </div>
  );
}
