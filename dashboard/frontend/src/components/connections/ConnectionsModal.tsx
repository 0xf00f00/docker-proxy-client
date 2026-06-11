import { useState } from "react";
import { Activity, ArrowDown, ArrowUp, ChevronDown, Globe, Loader2 } from "lucide-react";
import Modal from "@/components/common/Modal";
import Sparkline from "@/components/common/Sparkline";
import UsageView from "@/components/connections/UsageView";
import { useConnections, type DisplaySite } from "@/hooks/useConnections";
import type { ConnectionDetail } from "@/types";
import { cn } from "@/utils/cn";
import { formatAgo, formatBytes, formatRate, formatRateShort } from "@/utils/format";

// Friendly label for an exit proxy: name the special routes, otherwise show the
// raw route name as-is.
function exitLabel(name: string): string {
  if (!name || name === "DIRECT") return "Direct";
  if (name === "REJECT") return "Blocked";
  return name;
}

function hostHue(host: string): number {
  let h = 0;
  for (let i = 0; i < host.length; i += 1) h = (h * 31 + host.charCodeAt(i)) % 360;
  return h;
}

function hostInitial(host: string): string {
  const m = host.match(/[a-z0-9]/i);
  return m ? m[0].toUpperCase() : "•";
}

type Tab = "live" | "usage";

export default function ConnectionsModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("live");
  // Mounted across tab switches so the stream and ended-list survive a tab change.
  const live = useConnections();

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      title="Connections"
      subtitle="Live activity and where your data goes"
      size="full"
    >
      <div className="flex h-full flex-col">
        <TabBar tab={tab} onChange={setTab} />
        <div className={cn("flex min-h-0 flex-1 flex-col", tab !== "live" && "hidden")}>
          <LiveTab {...live} />
        </div>
        {tab === "usage" && <UsageView />}
      </div>
    </Modal>
  );
}

function TabBar({ tab, onChange }: { tab: Tab; onChange: (t: Tab) => void }) {
  const tabs: { id: Tab; label: string }[] = [
    { id: "live", label: "Live" },
    { id: "usage", label: "Usage" },
  ];
  return (
    <div className="border-border flex shrink-0 border-b px-2">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          aria-current={tab === t.id}
          className={cn(
            "relative min-h-11 px-4 text-sm font-medium transition-colors",
            tab === t.id ? "text-foreground" : "text-muted hover:text-foreground",
          )}
        >
          {t.label}
          {tab === t.id && <span className="bg-primary absolute inset-x-2 -bottom-px h-0.5 rounded-full" />}
        </button>
      ))}
    </div>
  );
}

function LiveTab({ status, snapshot, sites, rateHistory }: ReturnType<typeof useConnections>) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (host: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(host)) next.delete(host);
      else next.add(host);
      return next;
    });

  const loading = status === "connecting" && snapshot === null;
  const firstEnded = sites.findIndex((s) => s.phase === "ended");

  return (
    <>
      <SummaryBar
        count={snapshot?.count ?? 0}
        downRate={snapshot?.totals.downRate ?? 0}
        upRate={snapshot?.totals.upRate ?? 0}
        totalDown={snapshot?.totals.down ?? 0}
        totalUp={snapshot?.totals.up ?? 0}
        history={rateHistory}
        status={status}
      />

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16">
            <Loader2 className="text-muted h-5 w-5 animate-spin" />
            <span className="text-muted text-sm">Loading connections…</span>
          </div>
        ) : sites.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className="divide-border/60 divide-y">
            {sites.map((site, i) => (
              <SiteRow
                key={site.host}
                site={site}
                showEndedDivider={i === firstEnded}
                expanded={expanded.has(site.host)}
                onToggle={() => toggle(site.host)}
              />
            ))}
            {snapshot && snapshot.truncated > 0 && (
              <li className="text-muted px-4 py-3 text-center text-xs">
                +{snapshot.truncated} more {snapshot.truncated === 1 ? "site" : "sites"} not shown
              </li>
            )}
          </ul>
        )}
      </div>
    </>
  );
}

function SummaryBar({
  count,
  downRate,
  upRate,
  totalDown,
  totalUp,
  history,
  status,
}: {
  count: number;
  downRate: number;
  upRate: number;
  totalDown: number;
  totalUp: number;
  history: { down: number; up: number }[];
  status: string;
}) {
  return (
    <div className="border-border bg-card sticky top-0 z-10 border-b px-4 py-3">
      <div className="flex items-center gap-3">
        <div className="flex min-w-0 flex-col">
          <span className="inline-flex items-center gap-1.5 text-xl font-semibold tabular-nums">
            {count > 0 && status === "live" && (
              <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-emerald-400" aria-hidden="true" />
            )}
            {count}
          </span>
          <span className="text-muted text-xs">active {count === 1 ? "connection" : "connections"}</span>
        </div>

        <Sparkline down={history.map((h) => h.down)} up={history.map((h) => h.up)} width={64} height={26} />

        <div className="ml-auto flex flex-col items-end gap-1 text-sm tabular-nums">
          <span className="inline-flex items-center gap-1 text-sky-400">
            <ArrowDown className="h-3.5 w-3.5 shrink-0" />
            {formatRate(downRate)}
          </span>
          <span className="inline-flex items-center gap-1 text-emerald-400">
            <ArrowUp className="h-3.5 w-3.5 shrink-0" />
            {formatRate(upRate)}
          </span>
        </div>
      </div>

      <div className="text-muted mt-2 flex items-center gap-3 text-xs">
        <span>
          This session: <span className="text-foreground/80 tabular-nums">↓ {formatBytes(totalDown)}</span> ·{" "}
          <span className="text-foreground/80 tabular-nums">↑ {formatBytes(totalUp)}</span>
        </span>
        {status === "reconnecting" && (
          <span className="ml-auto inline-flex items-center gap-1 text-amber-400">
            <Loader2 className="h-3 w-3 animate-spin" />
            Reconnecting…
          </span>
        )}
      </div>
    </div>
  );
}

function SiteRow({
  site,
  expanded,
  onToggle,
  showEndedDivider,
}: {
  site: DisplaySite;
  expanded: boolean;
  onToggle: () => void;
  showEndedDivider: boolean;
}) {
  const exit = exitLabel(site.exit);
  const hue = hostHue(site.host);
  const ended = site.phase === "ended";
  const active = !ended && site.downRate + site.upRate >= 1;

  return (
    <>
      {showEndedDivider && (
        <li className="text-muted bg-zinc-950/40 px-4 py-1.5 text-[10px] font-medium tracking-wide uppercase">
          Recently ended
        </li>
      )}
      <li
        className={cn(
          // Fade in on arrival (@starting-style); ended rows stay dimmed.
          "transition-opacity duration-500 ease-out starting:opacity-0",
          ended && "opacity-50",
        )}
      >
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="flex min-h-[3.25rem] w-full items-center gap-3 px-4 py-2.5 text-left active:bg-zinc-800/50"
        >
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold"
            style={
              ended
                ? { backgroundColor: "hsl(240 4% 22%)", color: "hsl(240 5% 65%)" }
                : { backgroundColor: `hsl(${hue} 45% 20%)`, color: `hsl(${hue} 70% 72%)` }
            }
            aria-hidden="true"
          >
            {hostInitial(site.host)}
          </span>

          <span className="flex min-w-0 flex-1 flex-col">
            <span className="flex items-center gap-1.5">
              <span className="truncate text-sm font-medium">{site.host}</span>
              {ended && (
                <span className="shrink-0 rounded-full bg-zinc-700/60 px-1.5 py-px text-[10px] font-medium text-zinc-300">
                  Ended
                </span>
              )}
            </span>
            <span className="text-muted truncate text-xs">
              {ended ? (
                <>
                  used ↓ {formatBytes(site.down)} · ↑ {formatBytes(site.up)}
                </>
              ) : (
                `${site.count} ${site.count === 1 ? "connection" : "connections"}`
              )}
              {exit && <>{` · via ${exit}`}</>}
            </span>
          </span>

          {!ended && (
            <span className="flex shrink-0 flex-col items-end gap-0.5 text-[11px] tabular-nums">
              <span className={cn("inline-flex items-center gap-0.5", active ? "text-sky-400" : "text-zinc-500")}>
                <ArrowDown className="h-2.5 w-2.5 shrink-0" />
                <span className="min-w-[2.5rem] text-right">{formatRateShort(site.downRate)}/s</span>
              </span>
              <span className={cn("inline-flex items-center gap-0.5", active ? "text-emerald-400" : "text-zinc-500")}>
                <ArrowUp className="h-2.5 w-2.5 shrink-0" />
                <span className="min-w-[2.5rem] text-right">{formatRateShort(site.upRate)}/s</span>
              </span>
            </span>
          )}

          <ChevronDown
            className={cn("text-muted h-4 w-4 shrink-0 transition-transform", expanded && "rotate-180")}
            aria-hidden="true"
          />
        </button>

        {expanded && <SiteDetails site={site} exit={exit} />}
      </li>
    </>
  );
}

function SiteDetails({ site, exit }: { site: DisplaySite; exit: string }) {
  return (
    <div className="bg-zinc-950/40 px-4 pt-1 pb-3">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
        <Stat label="Downloaded" value={formatBytes(site.down)} />
        <Stat label="Uploaded" value={formatBytes(site.up)} />
        <Stat label="Connected" value={site.since ? formatAgo(site.since) + " ago" : "—"} />
        <Stat label="Exit" value={exit} />
      </dl>

      <div className="text-muted mt-3 mb-1.5 text-[10px] font-medium tracking-wide uppercase">
        {site.connections.length} {site.connections.length === 1 ? "connection" : "connections"}
      </div>
      <ul className="space-y-1">
        {site.connections.map((c) => (
          <ConnectionRow key={c.id} conn={c} />
        ))}
      </ul>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <dt className="text-muted text-[10px] tracking-wide uppercase">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}

function ConnectionRow({ conn }: { conn: ConnectionDetail }) {
  const target = [conn.dest, conn.port].filter(Boolean).join(":");
  const active = conn.downRate + conn.upRate >= 1;
  return (
    <li className="flex items-center gap-2 rounded-md bg-zinc-900/60 px-2 py-1.5 font-mono text-[11px]">
      <span className="text-muted shrink-0 uppercase">{conn.network || "tcp"}</span>
      <span className="min-w-0 flex-1 truncate text-zinc-300">{target || conn.rule || "—"}</span>
      {conn.rule && target && <span className="text-muted hidden shrink-0 truncate sm:inline">{conn.rule}</span>}
      <span className={cn("shrink-0 tabular-nums", active ? "text-sky-400" : "text-zinc-600")}>
        ↓{formatRateShort(conn.downRate)}
      </span>
      <span className={cn("shrink-0 tabular-nums", active ? "text-emerald-400" : "text-zinc-600")}>
        ↑{formatRateShort(conn.upRate)}
      </span>
    </li>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <div className="relative">
        <Globe className="h-12 w-12 text-zinc-700" />
        <Activity className="text-muted absolute -right-1 -bottom-1 h-5 w-5" />
      </div>
      <p className="text-sm font-medium">Nothing connected right now</p>
      <p className="text-muted max-w-xs text-xs">
        Apps and websites will appear here the moment they start sending traffic through the proxy.
      </p>
    </div>
  );
}
