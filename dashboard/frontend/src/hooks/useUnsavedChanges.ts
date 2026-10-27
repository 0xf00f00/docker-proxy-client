import { useCallback, useEffect } from "react";

const CONFIRM_MESSAGE = "You have unsaved changes. Discard and close?";

/**
 * Browser "Leave site?" prompt while `dirty`; `confirmClose` is true when closing is safe (nothing to lose,
 * or the user accepted a discard prompt).
 */
export function useUnsavedChanges(dirty: boolean): { confirmClose: () => boolean } {
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const confirmClose = useCallback(() => !dirty || window.confirm(CONFIRM_MESSAGE), [dirty]);
  return { confirmClose };
}
