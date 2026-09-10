import { useEffect, useRef, useState } from "react";
import { ChevronRight, Hexagon, Menu, X } from "lucide-react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { AuthStatus } from "@/features/auth/AuthStatus";
import { useAuth } from "@/features/auth/AuthProvider";
import { useT } from "@/features/i18n/i18n";
import { AnnouncementBanner } from "@/features/shell/AnnouncementBanner";
import { CommandPalette } from "@/features/shell/CommandPalette";
import { PlatformNavigation } from "@/features/shell/PlatformNavigation";
import {
  PLATFORM_DESTINATIONS,
  destinationLabel,
  isDestinationActive,
} from "@/features/shell/navigation";
import { useSidebarPreference } from "@/features/shell/useSidebarPreference";
import { cn } from "@/utils/cn";

export function PlatformShell() {
  const location = useLocation();
  const { can } = useAuth();
  const t = useT();
  const [compact, toggleCompact] = useSidebarPreference();
  const [mobileOpen, setMobileOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const destination = PLATFORM_DESTINATIONS.find(
    (entry) => isDestinationActive(entry, location.pathname) && (!entry.required || can(entry.required)),
  );

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 768px)");
    const closeAtDesktop = () => {
      if (!desktop.matches || !drawerRef.current) return;
      setMobileOpen(false);
      // The menu trigger is hidden at this breakpoint, so restore focus to the
      // routed work surface after the inert state and drawer have committed.
      requestAnimationFrame(() => document.getElementById("main-content")?.focus());
    };
    desktop.addEventListener("change", closeAtDesktop);
    return () => desktop.removeEventListener("change", closeAtDesktop);
  }, []);

  useEffect(() => {
    if (!mobileOpen) return;
    closeButtonRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobileOpen(false);
        requestAnimationFrame(() => menuButtonRef.current?.focus());
        return;
      }
      if (event.key === "Tab" && drawerRef.current) {
        const focusable = Array.from(
          drawerRef.current.querySelectorAll<HTMLElement>(
            'a[href], button:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
          ),
        );
        const first = focusable[0];
        const last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [mobileOpen]);

  const closeMobile = () => {
    setMobileOpen(false);
    // The page becomes focusable again on the next React commit.
    requestAnimationFrame(() => menuButtonRef.current?.focus());
  };

  const brandMark = (
    <span className="workspace-brand-mark">
      <Hexagon size={16} strokeWidth={1.5} aria-hidden="true" />
      <span className="absolute h-1 w-1 rounded-full bg-pulse" aria-hidden="true" />
    </span>
  );

  return (
    <div className="platform-shell flex h-[100dvh] w-full min-w-0 flex-col overflow-hidden bg-void text-ice">
      <div className="flex min-h-0 flex-1 flex-col" inert={mobileOpen || undefined}>
        <header className="workspace-chrome relative z-40 flex h-14 shrink-0 items-center gap-2 border-b border-line px-3 sm:gap-3 sm:px-4">
          <button
            ref={menuButtonRef}
            type="button"
            onClick={() => setMobileOpen(true)}
            className="workspace-icon-button shrink-0 md:hidden"
            aria-label={t("shell.openNavigation")}
            aria-expanded={mobileOpen}
            aria-controls={mobileOpen ? "mobile-navigation" : undefined}
          >
            <Menu size={18} aria-hidden="true" />
          </button>
          {/* Under md the rail is a drawer, so the header keeps a compact mark
              of its own; at md+ the rail owns the full brand block. */}
          <Link to="/" className="flex shrink-0 items-center md:hidden" aria-label={t("shell.brandAriaLabel")}>
            {brandMark}
          </Link>
          {/* Route location, not a page title — the routed page renders its own
              h1, and this must stay a plain string for the shell test hook. */}
          <div className="hidden min-w-0 items-center gap-2 md:flex">
            <span className="shrink-0 text-[11px] text-ghost">{t("shell.workspaceSection")}</span>
            <ChevronRight size={13} className="shrink-0 text-ghost/50" aria-hidden="true" />
            <span data-testid="workspace-destination" className="truncate text-xs font-medium text-ice">
              {destination ? destinationLabel(destination, t) : t("shell.operationsWorkspace")}
            </span>
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <CommandPalette onOpen={() => setMobileOpen(false)} />
            <span className="hidden h-5 w-px shrink-0 bg-line sm:block" aria-hidden="true" />
            <AuthStatus />
          </div>
        </header>

        <AnnouncementBanner />

        <div className="flex min-h-0 min-w-0 flex-1">
          <aside
            className={cn(
              "hidden min-h-0 shrink-0 border-r border-line md:flex md:flex-col",
              compact ? "w-[72px]" : "w-56",
            )}
            data-testid="desktop-navigation"
          >
            <div className="workspace-rail" data-compact={compact}>
              <Link to="/" className="workspace-brand" aria-label={t("shell.brandAriaLabel")}>
                {brandMark}
                {!compact && <span className="workspace-brand-name truncate">AgentCanvas</span>}
              </Link>
              <PlatformNavigation compact={compact} onToggleCompact={toggleCompact} />
            </div>
          </aside>
          <div id="main-content" tabIndex={-1} className="min-h-0 min-w-0 flex-1 overflow-hidden">
            <Outlet />
          </div>
        </div>
      </div>

      {mobileOpen && (
        <div id="mobile-navigation" className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label={t("shell.navigationMenu")}>
          <button
            type="button"
            tabIndex={-1}
            className="absolute inset-0 bg-void/80 backdrop-blur-sm"
            onClick={closeMobile}
            aria-label={t("shell.closeNavigationBackdrop")}
          />
          <aside
            ref={drawerRef}
            className="workspace-chrome relative flex h-full w-[min(18rem,84vw)] flex-col border-r border-line shadow-card animate-slide-in"
          >
            {/* The close button stays the first focusable element so the focus
                trap can wrap from it straight to the last destination. */}
            <div className="flex h-14 shrink-0 items-center border-b border-line px-4">
              <span className="workspace-section-title">{t("shell.navigationMenu")}</span>
              <button
                ref={closeButtonRef}
                type="button"
                onClick={closeMobile}
                className="workspace-icon-button ml-auto"
                aria-label={t("shell.closeNavigation")}
              >
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <PlatformNavigation onNavigate={closeMobile} />
          </aside>
        </div>
      )}
    </div>
  );
}
