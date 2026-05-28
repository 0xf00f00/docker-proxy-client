import { useState, type FormEvent } from "react";
import { Loader2, Lock } from "lucide-react";
import Modal from "@/components/common/Modal";
import { login } from "@/api/client";
import { getErrorMessage } from "@/utils/errors";

interface Props {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export default function LoginModal({ open, onClose, onSuccess }: Props) {
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(password);
      setPassword("");
      onSuccess();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          setPassword("");
          setError(null);
          onClose();
        }
      }}
      title="Sign in"
      size="auto"
    >
      <form
        onSubmit={handleSubmit}
        className="flex h-full flex-col justify-center gap-4 p-5 sm:h-auto sm:gap-3 sm:p-5"
        autoComplete="on"
      >
        <div className="mb-1 flex items-center justify-center gap-2 text-zinc-400 sm:hidden">
          <Lock className="h-4 w-4" />
          <span className="text-xs">Required to manage proxies</span>
        </div>
        <div>
          <label className="text-muted mb-1.5 block text-xs font-medium" htmlFor="dashboard-password">
            Password
          </label>
          <input
            id="dashboard-password"
            type="password"
            className="border-border focus:border-primary w-full rounded-lg border bg-zinc-900 px-3 py-3 text-base focus:outline-none disabled:opacity-50"
            autoComplete="current-password"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
          />
          {error && <p className="text-destructive mt-2 text-xs">{error}</p>}
        </div>
        <button
          type="submit"
          disabled={submitting || !password}
          className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex min-h-12 w-full items-center justify-center gap-1.5 rounded-lg px-3.5 text-base font-medium disabled:opacity-50"
        >
          {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
          Sign in
        </button>
      </form>
    </Modal>
  );
}
