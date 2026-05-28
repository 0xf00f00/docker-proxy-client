import type { ContainerInfo } from "@/types";
import { cn } from "@/utils/cn";

interface Props {
  services: ContainerInfo[];
}

export default function DnsSection({ services }: Props) {
  return (
    <div>
      <h2 className="text-muted mb-3 text-xs font-medium tracking-wide uppercase">DNS Services</h2>
      <div className="border-border bg-card divide-border divide-y overflow-hidden rounded-xl border">
        {services.map((s) => {
          const isRunning = s.status === "running";
          const isHealthy = s.health === "healthy" || (isRunning && s.health === null);
          return (
            <div key={s.id} className="flex min-h-12 items-center gap-3 px-4 py-3">
              <span
                className={cn(
                  "inline-block h-2.5 w-2.5 shrink-0 rounded-full",
                  isRunning && isHealthy ? "bg-emerald-500" : isRunning ? "bg-amber-500" : "bg-zinc-600",
                )}
                aria-label={isRunning ? (isHealthy ? "Healthy" : "Unhealthy") : "Stopped"}
              />
              <span className={cn("truncate text-sm", !isRunning && "text-zinc-500")}>{s.dashboard.name}</span>
              <span className="text-muted ml-auto shrink-0 text-xs">{isRunning ? "Running" : "Stopped"}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
