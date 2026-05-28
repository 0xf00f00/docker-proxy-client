import { useCallback, useEffect } from "react";

const CONFIRM_MESSAGE = "You have unsaved changes. Discard and close?";

/**
 * Guards an editor against losing unsaved work.
 *
 * - While `dirty` is true, the browser shows its built-in "Leave site?" prompt
 *   on tab close / reload. (Modern browsers ignore custom messages.)
 * - `confirmClose` returns true if it's safe to close now — either there's
 *   nothing to lose, or the user accepted a discard prompt.
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
