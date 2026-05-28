import * as Dialog from "@radix-ui/react-dialog";
import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/utils/cn";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  message: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  variant?: "destructive" | "default";
  busy?: boolean;
  onConfirm: () => void;
}

/**
 * Centered confirm prompt for actions that disrupt the user's setup (Stop,
 * Restart). Built on Radix Dialog so focus/escape/scroll-lock are handled
 * without reusing Modal — Modal is a full-screen shell with a header bar,
 * which is overkill for a single yes/no question.
 */
export default function ConfirmDialog({
  open,
  onOpenChange,
  title,
  message,
  confirmLabel,
  cancelLabel = "Cancel",
  variant = "default",
  busy = false,
  onConfirm,
}: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          aria-describedby="confirm-message"
          className="border-border bg-card fixed top-1/2 left-1/2 z-50 flex w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 flex-col gap-4 rounded-xl border p-5 focus:outline-none"
        >
          <Dialog.Title className="text-base font-semibold">{title}</Dialog.Title>
          <div id="confirm-message" className="text-muted text-sm leading-relaxed">
            {message}
          </div>
          <div className="mt-1 flex justify-end gap-2">
            <Dialog.Close
              className="text-muted hover:text-foreground inline-flex min-h-10 items-center rounded-lg bg-zinc-800 px-4 text-sm font-medium active:bg-zinc-700"
              disabled={busy}
            >
              {cancelLabel}
            </Dialog.Close>
            <button
              type="button"
              onClick={onConfirm}
              disabled={busy}
              className={cn(
                "inline-flex min-h-10 items-center gap-1.5 rounded-lg px-4 text-sm font-medium disabled:opacity-60",
                variant === "destructive"
                  ? "bg-red-500/90 text-white hover:bg-red-500 active:bg-red-600"
                  : "bg-primary text-primary-foreground hover:bg-primary/90",
              )}
            >
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
