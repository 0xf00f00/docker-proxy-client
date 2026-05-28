import { useState } from "react";
import { Globe } from "lucide-react";
import type { IpInfo } from "@/types";
import { cn } from "@/utils/cn";

/**
 * Map an ISO-3166 alpha-2 code to a country name without bundling a dataset.
 * `Intl.DisplayNames` is supported in every browser we care about.
 */
let _regionNames: Intl.DisplayNames | null = null;
export function countryName(code: string | null | undefined): string | null {
  if (!code || code.length !== 2) return null;
  try {
    _regionNames ??= new Intl.DisplayNames(["en"], { type: "region" });
    return _regionNames.of(code.toUpperCase()) ?? null;
  } catch {
    return null;
  }
}

interface Props {
  info: IpInfo | null | undefined;
  className?: string;
}

/**
 * Tiny country-flag chip that reveals the public IP on hover (desktop) or
 * tap (mobile). Resting state is just the flag — IP is supplementary detail
 * for the curious, not a piece of UI fighting for attention.
 */
export default function IpFlag({ info, className }: Props) {
  const [pinned, setPinned] = useState(false);

  if (!info) return null;
  const name = countryName(info.country_code);
  const flag = info.flag_emoji;

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        setPinned((v) => !v);
      }}
      title={[name ?? info.country_code, info.ip].filter(Boolean).join(" · ")}
      aria-label={`Public IP ${info.ip}${name ? `, ${name}` : ""}`}
      aria-expanded={pinned}
      className={cn(
        "group inline-flex shrink-0 select-none items-center gap-1 rounded-md bg-zinc-800/80 px-1.5 py-0.5 text-xs leading-none transition-colors hover:bg-zinc-700/80",
        className,
      )}
    >
      {flag ? (
        // Emoji needs an extra font-family chain on some platforms (mainly
        // Linux/headless) to render in color rather than as a monochrome box.
        <span
          aria-hidden="true"
          className="text-sm"
          style={{ fontFamily: '"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif' }}
        >
          {flag}
        </span>
      ) : (
        <Globe aria-hidden="true" className="text-muted h-3 w-3" />
      )}
      <span
        className={cn(
          "overflow-hidden whitespace-nowrap font-mono tabular-nums transition-[max-width,opacity,margin] duration-200 ease-out",
          pinned
            ? "max-w-[180px] opacity-100"
            : "max-w-0 opacity-0 group-hover:max-w-[180px] group-hover:opacity-100 group-focus-visible:max-w-[180px] group-focus-visible:opacity-100",
        )}
      >
        <span className="ml-1">{info.ip}</span>
      </span>
    </button>
  );
}
