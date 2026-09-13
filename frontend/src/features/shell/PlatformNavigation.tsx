import { PanelLeftClose, PanelLeftOpen, Settings2, Workflow } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import { useAuth } from "@/features/auth/AuthProvider";
import { useT } from "@/features/i18n/i18n";
import {
  SETTINGS_DESTINATIONS,
  WORKSPACE_DESTINATIONS,
  destinationLabel,
  destinationTitle,
  isDestinationActive,
  preloadDestination,
  type PlatformDestination,
} from "@/features/shell/navigation";
import { cn } from "@/utils/cn";

export function PlatformNavigation({
  compact = false,
  onToggleCompact,
  onNavigate,
}: {
  compact?: boolean;
  onToggleCompact?: () => void;
  onNavigate?: () => void;
}) {
  const { can } = useAuth();
  const { pathname } = useLocation();
  const t = useT();
  const renderEntry = (entry: PlatformDestination) => {
    if (entry.required && !can(entry.required)) return null;
    const Icon = entry.icon;
    const active = isDestinationActive(entry, pathname);
    return (
      <Link
        key={entry.to}
        to={entry.to}
        onClick={onNavigate}
        onMouseEnter={() => preloadDestination(entry)}
        onFocus={() => preloadDestination(entry)}
        title={destinationTitle(entry, t)}
        aria-current={active ? "page" : undefined}
        className="workspace-nav-link group"
      >
        {/* The icon sits in its own tile so the active state has somewhere to
            read as "selected" beyond a text colour change. */}
        <span className="workspace-nav-icon">
          <Icon size={15} aria-hidden="true" />
        </span>
        <span className={compact ? "sr-only" : "truncate"}>{destinationLabel(entry, t)}</span>
        {!compact && active && <span className="ml-auto h-1 w-1 shrink-0 rounded-full bg-pulse" aria-hidden="true" />}
      </Link>
    );
  };

  return (
    <nav aria-label={t("shell.navAriaLabel")} className="flex min-h-0 flex-1 flex-col" data-compact={compact}>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2.5 pb-4">
        <p className={cn("workspace-nav-section", compact && "justify-center")}>
          {compact ? <Workflow size={12} aria-hidden="true" /> : t("shell.workspaceSection")}
        </p>
        <div className="space-y-0.5">{WORKSPACE_DESTINATIONS.map(renderEntry)}</div>
        <p className={cn("workspace-nav-section", compact && "justify-center")}>
          <Settings2 size={12} aria-hidden="true" />
          <span className={compact ? "sr-only" : undefined}>{t("shell.settingsSection")}</span>
        </p>
        <div className="space-y-0.5">{SETTINGS_DESTINATIONS.map(renderEntry)}</div>
      </div>
      <div className={cn("workspace-rail-footer", compact && "justify-center")}>
        {!compact && (
          <div className="workspace-workspace-card">
            <span className="workspace-workspace-mark" aria-hidden="true">A</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[11px] font-medium text-ice">AgentCanvas</span>
              <span className="mt-0.5 block truncate text-[10px] text-ghost">{t("shell.workspaceCaption")}</span>
            </span>
          </div>
        )}
        {onToggleCompact && (
          <button
            type="button"
            onClick={onToggleCompact}
            aria-label={t(compact ? "shell.expandNavigation" : "shell.collapseNavigation")}
            title={t(compact ? "shell.expandNavigation" : "shell.collapseNavigation")}
            aria-expanded={!compact}
            className="workspace-icon-button shrink-0"
          >
            {compact ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </button>
        )}
      </div>
    </nav>
  );
}
