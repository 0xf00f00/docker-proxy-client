import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, AlertCircle } from "lucide-react";
import { toast } from "sonner";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  TouchSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { arrayMove, SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import {
  fetchSystemProxyEgressIp,
  fetchSystemProxyState,
  setSystemProxyMode,
  switchSystemProxy,
  testSystemProxyLatencies,
  reorderSystemProxy,
} from "@/api/client";
import { cn } from "@/utils/cn";
import { getErrorMessage } from "@/utils/errors";
import IpFlag from "@/components/common/IpFlag";
import SortableProxy from "./SortableProxy";
import ModeToggle from "./ModeToggle";

export default function SystemProxyWidget() {
  const queryClient = useQueryClient();
  const [orderOverride, setOrderOverride] = useState<string[] | null>(null);
  const [pendingReorderProxy, setPendingReorderProxy] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
    useSensor(KeyboardSensor),
  );

  const {
    data: state,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["system-proxy-state"],
    queryFn: fetchSystemProxyState,
    refetchInterval: 15_000,
    retry: 1,
  });

  // Egress IP. Re-keyed on the active route so switching/reordering naturally
  // triggers a refetch; backend caches for 60s so refetch on focus is cheap.
  const { data: egressIp } = useQuery({
    queryKey: ["system-proxy-egress-ip", state?.active ?? null],
    queryFn: fetchSystemProxyEgressIp,
    enabled: !!state?.active,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: 1,
  });

  // Drop in-flight reorder override the moment the server confirms a new
  // canonical order. See https://react.dev/learn/you-might-not-need-an-effect.
  const serverOrderKey = state?.routes.map((r) => r.name).join(",") ?? "";
  const [prevServerOrderKey, setPrevServerOrderKey] = useState(serverOrderKey);
  if (serverOrderKey !== prevServerOrderKey) {
    setPrevServerOrderKey(serverOrderKey);
    setOrderOverride(null);
  }

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["system-proxy-state"] });

  const modeMutation = useMutation({
    mutationFn: setSystemProxyMode,
    onSuccess: invalidate,
    onError: (err) => toast.error(`Failed to switch mode: ${getErrorMessage(err)}`),
  });

  const switchMutation = useMutation({
    mutationFn: (name: string) => switchSystemProxy(name),
    onSuccess: (_, name) => {
      toast.success(`Switched to ${name}`);
      invalidate();
    },
    onError: (err) => toast.error(`Switch failed: ${getErrorMessage(err)}`),
  });

  const reorderMutation = useMutation({
    mutationFn: (newOrder: string[]) => reorderSystemProxy(newOrder),
    onSuccess: (result) => {
      toast.success(result.active ? `Priority updated. Now using ${result.active}.` : "Priority updated");
      invalidate();
    },
    onError: (err) => {
      toast.error(`Reorder failed: ${getErrorMessage(err)}`);
      setOrderOverride(null);
    },
    onSettled: () => setPendingReorderProxy(null),
  });

  const latenciesMutation = useMutation({
    mutationFn: testSystemProxyLatencies,
    onError: (err) => toast.error(`Test failed: ${getErrorMessage(err)}`),
  });
  const latencies = latenciesMutation.data ?? {};

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const baseline = orderOverride ?? state?.routes.map((r) => r.name) ?? [];
    const oldIndex = baseline.indexOf(String(active.id));
    const newIndex = baseline.indexOf(String(over.id));
    if (oldIndex === -1 || newIndex === -1) return;
    const next = arrayMove(baseline, oldIndex, newIndex);
    setOrderOverride(next);
    setPendingReorderProxy(String(active.id));
    reorderMutation.mutate(next);
  };

  if (isLoading) return <SystemProxyWidgetSkeleton />;
  // Keep showing the last-known state across transient refetch errors (e.g.
  // controller container restarting after a reorder). TanStack Query preserves
  // `state` from the previous success — only fall through to the error UI if
  // we never had data.
  if (!state) return <WidgetError message={error ? getErrorMessage(error) : "No data"} />;

  const isAuto = state.mode === "auto";
  const visibleRoutes = state.routes.map((r) => r.name);
  const routes = isAuto && orderOverride ? orderOverride : visibleRoutes;
  const current = state.active;
  const isLocked = reorderMutation.isPending || modeMutation.isPending;

  return (
    <div className={cn("border-border bg-card overflow-hidden rounded-xl border", isLocked && "select-none")}>
      {isLocked && (
        <div className="h-0.5 w-full overflow-hidden bg-zinc-800" aria-hidden="true">
          <div className="bg-primary h-full w-1/3 animate-[shimmer_1.4s_ease-in-out_infinite] rounded-full" />
        </div>
      )}

      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="font-semibold">System Proxy</h2>
            {egressIp && <IpFlag info={egressIp} />}
          </div>
          <p className="text-muted text-xs">
            {isAuto ? `Auto-selecting the best proxy. Currently: ${current}.` : `Manually using ${current}.`}
          </p>
        </div>
        <button
          type="button"
          onClick={() => latenciesMutation.mutate()}
          disabled={latenciesMutation.isPending}
          className="text-muted hover:text-foreground ml-3 inline-flex min-h-10 shrink-0 items-center gap-1.5 rounded-lg bg-zinc-800 px-3 text-xs font-medium active:bg-zinc-700 disabled:opacity-50"
        >
          {latenciesMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          {latenciesMutation.isPending ? "Testing…" : "Test Latency"}
        </button>
      </div>

      <div className="flex items-center justify-between border-b border-zinc-800/50 px-4 py-2.5">
        <div>
          <div className="text-sm font-medium">{isAuto ? "Auto mode" : "Manual mode"}</div>
          <div className="text-muted text-[11px]">
            {isAuto
              ? "Picks the highest-priority healthy proxy. Drag to reorder."
              : "Tap a proxy below to use it"}
          </div>
        </div>
        <ModeToggle
          enabled={isAuto}
          onToggle={() => modeMutation.mutate(isAuto ? "manual" : "auto")}
          disabled={isLocked}
          busy={modeMutation.isPending}
        />
      </div>

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={routes} strategy={verticalListSortingStrategy}>
          <div className="divide-border divide-y">
            {routes.map((name, index) => {
              const isSwitchPending = switchMutation.isPending && switchMutation.variables === name;
              const isReorderPending = reorderMutation.isPending && pendingReorderProxy === name;
              const pendingLabel = isReorderPending ? "Updating priority…" : isSwitchPending ? "Switching…" : null;
              return (
                <SortableProxy
                  key={name}
                  proxyName={name}
                  index={index}
                  isActive={name === current}
                  isPending={isSwitchPending || isReorderPending}
                  pendingLabel={pendingLabel}
                  isAuto={isAuto}
                  isLocked={isLocked}
                  delay={latencies[name]}
                  onSelect={() => !isAuto && name !== current && switchMutation.mutate(name)}
                />
              );
            })}
          </div>
        </SortableContext>
      </DndContext>
    </div>
  );
}

export function SystemProxyWidgetSkeleton() {
  return (
    <div className="border-border bg-card overflow-hidden rounded-xl border" aria-busy="true" aria-label="Loading system proxy">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="h-4 w-28 animate-pulse rounded bg-zinc-800" />
          <div className="h-3 w-52 animate-pulse rounded bg-zinc-800/70" />
        </div>
        <div className="ml-3 h-10 w-24 shrink-0 animate-pulse rounded-lg bg-zinc-800" />
      </div>

      <div className="flex items-center justify-between border-b border-zinc-800/50 px-4 py-2.5">
        <div className="space-y-1.5">
          <div className="h-3.5 w-20 animate-pulse rounded bg-zinc-800" />
          <div className="h-2.5 w-56 animate-pulse rounded bg-zinc-800/70" />
        </div>
        <div className="h-7 w-12 shrink-0 animate-pulse rounded-full bg-zinc-800" />
      </div>

      <div className="divide-border divide-y">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-1 px-2 py-3">
            <div className="h-5 w-5 shrink-0 opacity-40" />
            <div className="ml-1 h-3.5 flex-1 animate-pulse rounded bg-zinc-800/70" style={{ maxWidth: `${55 + i * 8}%` }} />
            <div className="mr-4 h-3 w-14 shrink-0 animate-pulse rounded bg-zinc-800/70" />
          </div>
        ))}
      </div>
    </div>
  );
}

function WidgetError({ message }: { message: string }) {
  return (
    <div className="border-border bg-card flex items-start gap-3 rounded-xl border p-4 text-sm text-zinc-400">
      <AlertCircle className="text-warning mt-0.5 h-5 w-5 shrink-0" />
      <div>
        <p>System proxy information is unavailable.</p>
        <p className="text-muted mt-1 text-xs">{message}</p>
      </div>
    </div>
  );
}
