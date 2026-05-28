import { Loader2 } from "lucide-react";
import { cn } from "@/utils/cn";

interface Props {
  enabled: boolean;
  onToggle: () => void;
  disabled?: boolean;
  busy?: boolean;
}

export default function ModeToggle({ enabled, onToggle, disabled, busy }: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      onClick={onToggle}
      disabled={disabled}
      className={cn(
        "relative inline-flex h-7 w-12 shrink-0 cursor-pointer items-center rounded-full p-0.5 transition-colors disabled:cursor-not-allowed",
        enabled ? "bg-emerald-500" : "bg-zinc-700",
        disabled && "opacity-70",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "inline-flex h-6 w-6 items-center justify-center rounded-full bg-white shadow transition-transform duration-200",
          enabled ? "translate-x-5" : "translate-x-0",
        )}
      >
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-zinc-600" />}
      </span>
    </button>
  );
}
