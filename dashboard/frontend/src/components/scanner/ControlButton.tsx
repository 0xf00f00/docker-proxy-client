import { Loader2 } from "lucide-react";
import { cn } from "@/utils/cn";

export type Variant = "default" | "positive" | "destructive";

export const SPIN = <Loader2 className="h-3.5 w-3.5 animate-spin" />;
export const LOG = <span className="font-mono text-[10px]">LOG</span>;

const VARIANT: Record<Variant, string> = {
  default: "text-muted hover:text-foreground bg-zinc-800 active:bg-zinc-700",
  positive:
    "text-emerald-300 hover:text-emerald-200 bg-emerald-500/15 hover:bg-emerald-500/25 active:bg-emerald-500/30",
  destructive: "text-red-300 hover:text-red-200 bg-red-500/15 hover:bg-red-500/25 active:bg-red-500/30",
};

export function Btn({
  onClick,
  disabled,
  icon,
  variant = "default",
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  icon: React.ReactNode;
  variant?: Variant;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex min-h-10 items-center gap-1.5 rounded-lg px-3.5 text-xs font-medium disabled:opacity-50",
        VARIANT[variant],
      )}
    >
      {icon}
      {children}
    </button>
  );
}
