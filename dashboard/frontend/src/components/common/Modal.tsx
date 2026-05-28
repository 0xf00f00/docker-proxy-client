import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/utils/cn";

interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  subtitle?: ReactNode;
  headerActions?: ReactNode;
  /** Bottom bar (e.g. Save/Cancel). Modals without one omit this. */
  footer?: ReactNode;
  /** "auto" sizes to content; "full" fills the viewport (mobile-first). */
  size?: "auto" | "full";
  children: ReactNode;
}

/**
 * Standard modal shell built on Radix Dialog: focus trap, scroll lock, Escape
 * handler, ARIA wiring, and safe-area insets are handled here so callers can
 * focus on the body content.
 */
export default function Modal({
  open,
  onOpenChange,
  title,
  subtitle,
  headerActions,
  footer,
  size = "full",
  children,
}: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          aria-describedby={undefined}
          className={cn(
            "border-border bg-card fixed inset-0 z-50 flex flex-col border focus:outline-none sm:inset-auto sm:top-1/2 sm:left-1/2 sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-xl",
            size === "full" && "sm:h-[80vh] sm:w-[calc(100%-2rem)] sm:max-w-4xl",
            size === "auto" && "sm:w-[calc(100%-2rem)] sm:max-w-lg",
          )}
          style={{
            paddingTop: "env(safe-area-inset-top)",
            paddingBottom: "env(safe-area-inset-bottom)",
          }}
        >
          <div className="border-border flex items-center gap-3 border-b px-3 py-2.5 sm:px-4 sm:py-3">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate text-sm font-semibold sm:text-base">{title}</Dialog.Title>
              {subtitle && <div className="text-muted mt-0.5 truncate text-xs">{subtitle}</div>}
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              {headerActions}
              <Dialog.Close
                aria-label="Close"
                className="text-muted hover:text-foreground inline-flex h-10 w-10 items-center justify-center rounded-lg active:bg-zinc-800"
              >
                <X className="h-5 w-5" />
              </Dialog.Close>
            </div>
          </div>

          <div className="flex-1 overflow-hidden">{children}</div>

          {footer && (
            <div className="border-border flex items-center justify-end gap-2 border-t px-4 py-3">{footer}</div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/**
 * Rendered as the Suspense fallback for lazy-loaded modals so the click feels
 * instant — the modal frame appears with the right title, then the real body
 * swaps in once the chunk arrives.
 */
export function ModalLoadingShell({ title, onClose }: { title: string; onClose: () => void }) {
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={title} size="auto">
      <div className="flex items-center justify-center gap-2 py-12">
        <Loader2 className="text-muted h-5 w-5 animate-spin" />
        <span className="text-muted text-sm">Loading…</span>
      </div>
    </Modal>
  );
}
