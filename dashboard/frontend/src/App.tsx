import { Suspense, useEffect, useRef, useState } from "react";
import { AlertCircle, Loader2 } from "lucide-react";
import Header from "@/components/layout/Header";
import SpeedTest from "@/components/system/SpeedTest";
import ProxyGrid from "@/components/containers/ProxyGrid";
import DnsSection from "@/components/containers/DnsSection";
import ScannerSection from "@/components/scanner/ScannerSection";
import { getWidget } from "@/components/widgets/registry";
import { useContainerStream } from "@/hooks/useContainerStream";
import { useConnectivityTests } from "@/hooks/useConnectivityTests";

// The system-proxy container is restarted when its priority list is reordered;
// it briefly leaves "running" state. Keep the widget mounted across that gap.
const SYSTEM_PROXY_GRACE_MS = 15_000;

export default function App() {
  const state = useContainerStream();
  const containers = state.kind === "ready" ? state.data.containers : [];
  // Delay the first auto-probe a few seconds after mount so DNS/Net pills can
  // settle and the user has time to start a speed test before we add load.
  const connectivity = useConnectivityTests({ initialDelayMs: 4_000 });
  const [speedRunning, setSpeedRunning] = useState(false);

  const proxies = containers.filter((c) => c.dashboard.category === "proxy");
  const dnsServices = containers.filter((c) => c.dashboard.category === "dns");

  // Pick the first running container whose `dashboard.widget` resolves to a
  // registered widget. The registry decides whether a widget exists at all;
  // App.tsx doesn't know which controllers are installed.
  const widgetContainer = containers.find((c) => getWidget(c.dashboard.widget) !== null);
  const hasLiveWidget = !!widgetContainer && widgetContainer.status === "running";
  const showWidget = useStickyTrue(hasLiveWidget, SYSTEM_PROXY_GRACE_MS);
  const widgetEntry = widgetContainer ? getWidget(widgetContainer.dashboard.widget) : null;

  const hasContainers = containers.length > 0;

  return (
    <div className="min-h-screen bg-zinc-950 pb-[calc(env(safe-area-inset-bottom)+3rem)]">
      <Header pauseAutoRefresh={connectivity.running || speedRunning} />
      <main className="mx-auto max-w-3xl px-3 py-4 sm:px-4 sm:py-6">
        <SpeedTest onRunningChange={setSpeedRunning} />

        {state.kind === "initial" && widgetEntry?.Skeleton && (
          <section className="mt-6 sm:mt-8">
            <widgetEntry.Skeleton />
          </section>
        )}
        {showWidget && widgetEntry && (
          <section className="mt-6 sm:mt-8">
            <Suspense fallback={widgetEntry.Skeleton ? <widgetEntry.Skeleton /> : null}>
              <widgetEntry.Component />
            </Suspense>
          </section>
        )}

        <section className="mt-6 sm:mt-8">
          <div className="mb-3 flex items-center justify-between gap-3 sm:mb-4">
            <h2 className="text-base font-semibold sm:text-lg">Available Proxies</h2>
            <button
              type="button"
              onClick={connectivity.start}
              disabled={connectivity.running || !hasContainers}
              className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex min-h-11 items-center gap-1.5 rounded-lg px-3.5 text-sm font-medium disabled:opacity-50"
            >
              {connectivity.running && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {connectivity.running ? `Testing ${connectivity.testing.size}…` : "Test All"}
            </button>
          </div>

          {state.kind === "initial" && <SkeletonList count={3} />}

          {state.kind === "error" && (
            <div className="border-border bg-card flex items-start gap-3 rounded-xl border p-4 text-sm">
              <AlertCircle className="text-destructive mt-0.5 h-5 w-5 shrink-0" />
              <div className="min-w-0">
                <p className="text-zinc-300">Cannot load services</p>
                <p className="text-muted mt-1 text-xs break-words">{state.message}</p>
              </div>
            </div>
          )}

          {state.kind === "ready" && (
            <ProxyGrid
              proxies={proxies}
              connectivityResults={connectivity.results}
              testingSet={connectivity.testing}
              running={connectivity.running}
              onResult={connectivity.recordResult}
            />
          )}
        </section>

        {dnsServices.length > 0 && (
          <section className="mt-6 sm:mt-8">
            <DnsSection services={dnsServices} />
          </section>
        )}

        <section className="mt-6 sm:mt-8">
          <ScannerSection />
        </section>
      </main>
    </div>
  );
}

/**
 * Hold `true` for `gracePeriodMs` after `value` flips back to `false`. Lets transient
 * container outages (e.g. controller restart on reorder) pass without unmounting downstream UI.
 */
function useStickyTrue(value: boolean, gracePeriodMs: number): boolean {
  const [sticky, setSticky] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (value) {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
      setSticky(true);
      return;
    }
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      setSticky(false);
      timerRef.current = null;
    }, gracePeriodMs);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [value, gracePeriodMs]);

  return sticky;
}

function SkeletonList({ count }: { count: number }) {
  return (
    <div className="space-y-3" aria-label="Loading services" aria-busy="true">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="border-border bg-card rounded-xl border p-4">
          <div className="flex items-center gap-3">
            <div className="h-5 w-5 shrink-0 animate-pulse rounded-full bg-zinc-800" />
            <div className="flex-1 space-y-2">
              <div className="h-3 w-1/3 animate-pulse rounded bg-zinc-800" />
              <div className="h-2.5 w-1/2 animate-pulse rounded bg-zinc-800/70" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
