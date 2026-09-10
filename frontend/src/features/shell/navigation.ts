import {
  AppWindow,
  ShieldCheck,
  BrainCircuit,
  ChartNoAxesCombined,
  Database,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LibraryBig,
  MessageSquareText,
  ScrollText,
  Users,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import type { Translate } from "@/features/i18n/i18n";

/** Destination identity consumed by the i18n dictionaries as
 * `nav.<key>.label|title|description|keywords`. */
export type DestinationKey =
  | "overview"
  | "workflows"
  | "knowledge"
  | "evaluations"
  | "apps"
  | "chat"
  | "cost"
  | "models"
  | "mcp"
  | "quotas"
  | "audit"
  | "platform"
  | "members";

export interface PlatformDestination {
  to: string;
  key: DestinationKey;
  icon: LucideIcon;
  end?: boolean;
  required?: "admin";
  palette?: boolean;
  /** C6-5 route-level preload hint: dynamically imports the destination's
   * lazy chunk so hover/focus/palette-highlight warms it before navigation.
   * Must swallow errors — prefetching is best-effort. */
  preload?: () => Promise<unknown>;
}

/** One route identity for both the rail and header, including saved canvases. */
export function isDestinationActive(destination: PlatformDestination, pathname: string): boolean {
  const base = destination.key === "workflows" ? "/workflows" : destination.to;
  return pathname === base || (!destination.end && pathname.startsWith(`${base}/`));
}

export function destinationLabel(
  destination: PlatformDestination,
  t: Translate,
): string {
  return t(`nav.${destination.key}.label` as Parameters<Translate>[0]);
}

export function destinationTitle(
  destination: PlatformDestination,
  t: Translate,
): string {
  return t(`nav.${destination.key}.title` as Parameters<Translate>[0]);
}

export function destinationDescription(
  destination: PlatformDestination,
  t: Translate,
): string {
  return t(`nav.${destination.key}.description` as Parameters<Translate>[0]);
}

export function destinationKeywords(
  destination: PlatformDestination,
  t: Translate,
): string {
  return t(`nav.${destination.key}.keywords` as Parameters<Translate>[0]);
}

/** Invoke a destination's preload hint (C6-5). Safe to call repeatedly — the
 * browser deduplicates in-flight/duplicate dynamic imports of the same chunk. */
export function preloadDestination(destination: PlatformDestination): void {
  destination.preload?.();
}

/** Best-effort preload wrapper: a failed prefetch (offline, deploy race) must
 * never surface to the user — navigation itself will surface the real error. */
function safePreload(loader: () => Promise<unknown>): () => Promise<unknown> {
  return () => loader().catch(() => undefined);
}

export const WORKSPACE_DESTINATIONS: readonly PlatformDestination[] = [
  {
    to: "/",
    key: "overview",
    icon: LayoutDashboard,
    end: true,
    preload: safePreload(() => import("@/features/overview/OverviewPage")),
  },
  {
    to: "/workflows/new",
    key: "workflows",
    icon: Workflow,
    palette: false,
    preload: safePreload(() => import("@/App")),
  },
  {
    to: "/knowledge",
    key: "knowledge",
    icon: Database,
    preload: safePreload(() => import("@/features/knowledge/KnowledgePage")),
  },
  {
    to: "/evaluations",
    key: "evaluations",
    icon: FlaskConical,
    preload: safePreload(() => import("@/features/evaluations/EvaluationPage")),
  },
  {
    to: "/apps",
    key: "apps",
    icon: AppWindow,
    preload: safePreload(() => import("@/features/apps/AppsPage")),
  },
  {
    to: "/chat",
    key: "chat",
    icon: MessageSquareText,
    preload: safePreload(() => import("@/features/chat/ChatPage")),
  },
  {
    to: "/cost",
    key: "cost",
    icon: ChartNoAxesCombined,
    preload: safePreload(() => import("@/features/cost/CostGovernancePage")),
  },
];

export const SETTINGS_DESTINATIONS: readonly PlatformDestination[] = [
  {
    to: "/settings/models",
    key: "models",
    icon: BrainCircuit,
    preload: safePreload(() => import("@/features/models/ModelsPage")),
  },
  {
    to: "/settings/mcp",
    key: "mcp",
    icon: LibraryBig,
    required: "admin",
    preload: safePreload(() => import("@/features/mcp/McpCatalogPage")),
  },
  {
    to: "/settings/quotas",
    key: "quotas",
    icon: Gauge,
    preload: safePreload(() => import("@/features/quotas/ProjectQuotasPage")),
  },
  {
    to: "/settings/members",
    key: "members",
    icon: Users,
    preload: safePreload(() => import("@/features/members/MembersPage")),
  },
  {
    to: "/settings/audit",
    key: "audit",
    icon: ScrollText,
    required: "admin",
    preload: safePreload(() => import("@/features/audit/AuditLogsPage")),
  },
  {
    to: "/settings/platform",
    key: "platform",
    icon: ShieldCheck,
    required: "admin",
    preload: safePreload(() => import("@/features/admin/PlatformAdminPage")),
  },
];

export const PLATFORM_DESTINATIONS = [
  ...WORKSPACE_DESTINATIONS,
  ...SETTINGS_DESTINATIONS,
] as const;
