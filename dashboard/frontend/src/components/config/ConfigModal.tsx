import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, Loader2 } from "lucide-react";
import { toast } from "sonner";
import Editor from "@monaco-editor/react";
import { fetchConfig, saveConfig } from "@/api/client";
import { getErrorMessage } from "@/utils/errors";
import { useUnsavedChanges } from "@/hooks/useUnsavedChanges";
import Modal from "@/components/common/Modal";
import UnsavedBadge from "@/components/common/UnsavedBadge";
import "@/utils/monaco";

interface Props {
  containerName: string;
  displayName: string;
  onClose: () => void;
}

export default function ConfigModal({ containerName, displayName, onClose }: Props) {
  const queryClient = useQueryClient();
  const configKey = ["config", containerName] as const;

  const { data, isLoading, error } = useQuery({
    queryKey: configKey,
    queryFn: () => fetchConfig(containerName),
    refetchOnWindowFocus: false,
  });

  // `null` means "follow the loaded content"; a string means the user is editing.
  const [draft, setDraft] = useState<string | null>(null);
  const content = draft ?? data?.content ?? "";
  const isDirty = data != null && draft !== null && draft !== data.content;

  const { confirmClose } = useUnsavedChanges(isDirty);
  const requestClose = useCallback(() => {
    if (confirmClose()) onClose();
  }, [confirmClose, onClose]);

  const saveMutation = useMutation({
    mutationFn: (next: string) => saveConfig(containerName, next),
    onSuccess: (result, next) => {
      queryClient.setQueryData(configKey, data ? { ...data, content: next } : undefined);
      setDraft(null);
      if (result.applied) {
        toast.success(`${data?.filename ?? "Config"} saved. ${displayName} restarted.`);
        onClose();
      } else {
        toast.warning(result.message, { duration: 8000 });
      }
    },
    onError: (err) => toast.error(`Save failed: ${getErrorMessage(err)}`),
  });

  return (
    <Modal
      open
      onOpenChange={(open) => !open && requestClose()}
      title={`${displayName} — Config`}
      subtitle={data?.filename}
      headerActions={
        <>
          {isDirty && <UnsavedBadge />}
          <button
            type="button"
            onClick={() => draft !== null && saveMutation.mutate(draft)}
            disabled={saveMutation.isPending || isLoading || !isDirty}
            className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex min-h-10 items-center gap-1.5 rounded-lg px-3 text-sm font-medium disabled:opacity-50"
          >
            {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            {saveMutation.isPending ? "Applying…" : "Save & Apply"}
          </button>
        </>
      }
    >
      {isLoading ? (
        <div className="flex h-full items-center justify-center">
          <Loader2 className="text-muted h-6 w-6 animate-spin" />
        </div>
      ) : error || !data ? (
        <div className="text-destructive flex h-full items-center justify-center p-4 text-center text-sm">
          Failed to load config: {error ? getErrorMessage(error) : "no data"}
        </div>
      ) : (
        <Editor
          height="100%"
          language={data.language}
          value={content}
          onChange={(val) => setDraft(val ?? "")}
          theme="vs-dark"
          options={{
            minimap: { enabled: false },
            fontSize: 14,
            lineNumbers: "on",
            scrollBeyondLastLine: false,
            wordWrap: "on",
            padding: { top: 12 },
          }}
        />
      )}
    </Modal>
  );
}
