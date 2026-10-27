import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/** Maps the `dashboard.widget` label to a lazily imported component, so a widget loads only when its service exists. */

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
