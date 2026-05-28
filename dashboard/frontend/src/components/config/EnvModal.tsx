import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { fetchServiceEnv, saveServiceEnv } from "@/api/client";
import { getErrorMessage } from "@/utils/errors";
import { useUnsavedChanges } from "@/hooks/useUnsavedChanges";
import Modal from "@/components/common/Modal";
import UnsavedBadge from "@/components/common/UnsavedBadge";

interface Props {
  containerName: string;
  displayName: string;
  onClose: () => void;
}

export default function EnvModal({ containerName, displayName, onClose }: Props) {
  const queryClient = useQueryClient();
  const envKey = ["env", containerName] as const;

  const { data, isLoading, error } = useQuery({
    queryKey: envKey,
    queryFn: () => fetchServiceEnv(containerName),
    refetchOnWindowFocus: false,
  });

  // Overrides for keys the user has edited. Unedited keys fall back to data.values.
  const [overrides, setOverrides] = useState<Record<string, string>>({});

  const values = useMemo(
    () => ({ ...(data?.values ?? {}), ...overrides }),
    [data?.values, overrides],
  );
  const isDirty = data != null && data.keys.some((k) => (overrides[k] ?? data.values[k] ?? "") !== (data.values[k] ?? ""));

  const { confirmClose } = useUnsavedChanges(isDirty);
  const requestClose = useCallback(() => {
    if (confirmClose()) onClose();
  }, [confirmClose, onClose]);

  const saveMutation = useMutation({
    mutationFn: (next: Record<string, string>) => saveServiceEnv(containerName, next),
    onSuccess: (result, next) => {
      queryClient.setQueryData(envKey, data ? { ...data, values: next } : undefined);
      setOverrides({});
      if (result.applied) {
        toast.success(`${displayName} updated and restarted`);
        onClose();
      } else {
        toast.warning(result.message, { duration: 8000 });
      }
    },
    onError: (err) => toast.error(`Save failed: ${getErrorMessage(err)}`),
  });

  const disabled = saveMutation.isPending || isLoading || !!error || !isDirty;

  return (
    <Modal
      open
      onOpenChange={(open) => !open && requestClose()}
      size="auto"
      title={`${displayName} — Settings`}
      subtitle="Changes restart the service."
      footer={
        <>
          <button
            type="button"
            onClick={requestClose}
            disabled={saveMutation.isPending}
            className="text-muted hover:text-foreground inline-flex min-h-10 items-center rounded-lg px-3 text-sm font-medium disabled:opacity-50"
          >
            Cancel
          </button>
          {isDirty && <UnsavedBadge />}
          <button
            type="button"
            onClick={() => saveMutation.mutate(values)}
            disabled={disabled}
            className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex min-h-10 items-center gap-1.5 rounded-lg px-4 text-sm font-medium disabled:opacity-50"
          >
            {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            {saveMutation.isPending ? "Applying…" : "Save & Apply"}
          </button>
        </>
      }
    >
      <div className="space-y-4 px-4 py-4">
        {isLoading && (
          <div className="flex h-32 items-center justify-center">
            <Loader2 className="text-muted h-6 w-6 animate-spin" />
          </div>
        )}

        {error && <p className="text-destructive text-sm">Failed to load: {getErrorMessage(error)}</p>}

        {!isLoading && !error && data && data.keys.length === 0 && (
          <p className="text-muted text-sm">No editable settings for this service.</p>
        )}

        {!isLoading &&
          !error &&
          data?.keys.map((key) => (
            <div key={key} className="space-y-1.5">
              <label htmlFor={`env-${key}`} className="block font-mono text-xs text-zinc-400">
                {key}
              </label>
              <input
                id={`env-${key}`}
                type="text"
                value={values[key] ?? ""}
                onChange={(e) =>
                  setOverrides((prev) => ({ ...prev, [key]: e.target.value }))
                }
                disabled={saveMutation.isPending}
                spellCheck={false}
                autoCapitalize="off"
                autoComplete="off"
                className="border-border bg-background w-full rounded-lg border px-3 py-2.5 font-mono text-sm focus:border-sky-500 focus:outline-none disabled:opacity-50"
              />
            </div>
          ))}
      </div>
    </Modal>
  );
}
