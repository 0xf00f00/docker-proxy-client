import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/**
 * Maps the `dashboard.widget` label value to a React component. Adding a
 * service-specific widget is one entry in this map.
 *
 * Each entry is a lazy import so widget bundles only load when the matching
 * service is actually present.
 */

export interface WidgetEntry {
  Component: LazyExoticComponent<ComponentType>;
  Skeleton?: ComponentType;
}

import { SystemProxyWidgetSkeleton } from "./SystemProxyWidget";

const WIDGETS: Record<string, WidgetEntry> = {
  "system-proxy": {
    Component: lazy(() => import("./SystemProxyWidget")),
    Skeleton: SystemProxyWidgetSkeleton,
  },
};

export function getWidget(name: string | null | undefined): WidgetEntry | null {
  if (!name) return null;
  return WIDGETS[name] ?? null;
}
