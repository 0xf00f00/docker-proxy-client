import { Check, Loader2, Zap, GripVertical } from "lucide-react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { cn } from "@/utils/cn";

interface Props {
  proxyName: string;
  index: number;
  isActive: boolean;
  isPending: boolean;
  pendingLabel: string | null;
  isAuto: boolean;
  isLocked: boolean;
  delay: number | undefined;
  onSelect: () => void;
}

function delayColor(delay: number): string {
  if (delay < 0) return "text-destructive";
  if (delay < 300) return "text-emerald-400";
  if (delay < 800) return "text-warning";
  return "text-destructive";
}

export default function SortableProxy({
  proxyName,
  index,
  isActive,
  isPending,
  pendingLabel,
  isAuto,
  isLocked,
  delay,
  onSelect,
}: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: proxyName,
    disabled: !isAuto || isLocked,
  });

  const rowContent = (
    <>
      {!isAuto && (
        <span
          className={cn(
            "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2",
            isActive ? "border-primary bg-primary" : "border-zinc-600",
          )}
        >
          {isPending ? (
            <Loader2 className="h-3 w-3 animate-spin text-white" />
          ) : isActive ? (
            <Check className="h-3 w-3 text-white" />
          ) : null}
        </span>
      )}

      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className={cn("text-sm font-medium", isActive && "text-primary")}>{proxyName}</span>
        {isAuto && !pendingLabel && <span className="text-muted text-[10px]">Priority {index + 1}</span>}
      </div>

      {pendingLabel ? (
        <span className="text-primary inline-flex items-center gap-1.5 text-xs">
          {isAuto && <Loader2 className="h-3 w-3 animate-spin" />}
          {pendingLabel}
        </span>
      ) : delay !== undefined ? (
        <span className={cn("flex items-center gap-1 text-xs", delayColor(delay))}>
          <Zap className="h-3 w-3" />
          {delay < 0 ? "timeout" : `${delay}ms`}
        </span>
      ) : isActive ? (
        <span className="text-primary inline-flex items-center gap-1 text-xs font-medium">
          <Check className="h-3 w-3" />
          Active
        </span>
      ) : null}
    </>
  );

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "group flex items-center gap-1 transition-colors",
        isActive && "bg-primary/5",
        !isAuto && !isActive && !isLocked && "hover:bg-zinc-800/50",
        isDragging && "z-10 bg-zinc-800 shadow-lg",
      )}
    >
      {isAuto && (
        <button
          type="button"
          {...attributes}
          {...listeners}
          disabled={isLocked}
          aria-label="Drag to reorder"
          className={cn(
            "flex h-12 shrink-0 touch-none items-center px-2 text-zinc-600 transition-colors",
            !isLocked && "group-hover:text-zinc-100",
            isLocked ? "cursor-not-allowed opacity-40" : "cursor-grab active:cursor-grabbing",
          )}
        >
          <GripVertical className="h-5 w-5" />
        </button>
      )}

      {isAuto ? (
        <div className="flex flex-1 items-center gap-3 py-3 pr-4">{rowContent}</div>
      ) : (
        <button
          type="button"
          onClick={onSelect}
          disabled={isActive || isLocked}
          className={cn(
            "flex flex-1 items-center gap-3 py-3 pr-4 pl-4 text-left",
            (isActive || isLocked) && "cursor-default",
          )}
        >
          {rowContent}
        </button>
      )}
    </div>
  );
}
