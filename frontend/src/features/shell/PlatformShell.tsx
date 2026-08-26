import { useEffect, useRef, useState } from "react";
import {
  Boxes,
  Hexagon,
  Menu,
  Settings2,
  X,
} from "lucide-react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { AnnouncementBanner } from "@/features/shell/AnnouncementBanner";

import { AuthStatus } from "@/features/auth/AuthStatus";
import { useAuth } from "@/features/auth/AuthProvider";
import { useT } from "@/features/i18n/i18n";
import { CommandPalette } from "@/features/shell/CommandPalette";
import {
  SETTINGS_DESTINATIONS,
  WORKSPACE_DESTINATIONS,
  destinationLabel,
  destinationTitle,
  preloadDestination,
  type PlatformDestination,
} from "@/features/shell/navigation";
import { cn } from "@/utils/cn";

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useAuth();
  const t = useT();
  const renderEntry = (entry: PlatformDestination) => {
    if (entry.required && !can(entry.required)) return null;
    const Icon = entry.icon;
    return (
      <NavLink
        key={entry.to}
        to={entry.to}
        end={entry.end}
        onClick={onNavigate}
        onMouseEnter={() => preloadDestination(entry)}
        onFocus={() => preloadDestination(entry)}
        title={destinationTitle(entry, t)}
        className={({ isActive }) =>
          cn(
            "group flex h-9 items-center gap-3 border-l-2 px-4 text-xs transition",
            isActive
              ? "border-pulse bg-pulse/10 text-ice"
              : "border-transparent text-ghost hover:border-ghost/40 hover:bg-line/45 hover:text-ice",
          )
        }
      >
        <Icon size={15} className="shrink-0 group-aria-[current=page]:text-pulse" />
        <span className="truncate">{destinationLabel(entry, t)}</span>
      </NavLink>
    );
  };

  return (
    <nav aria-label={t("shell.navAriaLabel")} className="flex h-full min-h-0 flex-col">
      <div className="space-y-0.5 py-3">
        {WORKSPACE_DESTINATIONS.map(renderEntry)}
      </div>
      <div className="mx-4 border-t border-line" />
      <div className="flex items-center gap-2 px-4 pb-2 pt-4 font-mono text-[9px] uppercase text-ghost">
        <Settings2 size={11} /> {t("shell.settingsSection")}
      </div>
      <div className="space-y-0.5">{SETTINGS_DESTINATIONS.map(renderEntry)}</div>
      <div className="mt-auto border-t border-line px-4 py-3">
        <div className="flex items-center gap-2 font-mono text-[9px] uppercase text-ghost">
          <span className="h-1.5 w-1.5 rounded-full bg-ok shadow-glow-ok" />
          {t("shell.controlPlaneOnline")}
        </div>
      </div>
    </nav>
  );
}

export function PlatformShell() {
  const location = useLocation();
  const t = useT();
  const [mobileOpen, setMobileOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    closeButtonRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileOpen(false);
        menuButtonRef.current?.focus();
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
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [mobileOpen]);

  const closeMobile = () => {
    setMobileOpen(false);
    menuButtonRef.current?.focus();
  };

  return (
    <div className="flex h-[100dvh] w-full min-w-0 flex-col overflow-hidden bg-void text-ice">
      <header className="glass relative z-40 flex h-13 shrink-0 items-center border-b border-line px-3 sm:px-4">
        <button
          ref={menuButtonRef}
          type="button"
          onClick={() => setMobileOpen(true)}
          className="mr-2 flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice md:hidden"
          aria-label={t("shell.openNavigation")}
          aria-expanded={mobileOpen}
        >
          <Menu size={17} />
        </button>
        <Link to="/" className="flex min-w-0 items-center gap-2.5" aria-label={t("shell.brandAriaLabel")}>
          <span className="relative flex h-8 w-8 shrink-0 items-center justify-center text-pulse">
            <Hexagon size={27} strokeWidth={1.2} />
            <span className="absolute h-1.5 w-1.5 rounded-full bg-pulse shadow-glow-cyan" />
          </span>
          <span className="brand-title truncate font-display text-base font-bold">AgentCanvas</span>
        </Link>
        <div className="mx-4 hidden h-5 w-px bg-line sm:block" />
        <span className="hidden font-mono text-[9px] uppercase text-ghost sm:block">
          {t("shell.operationsWorkspace")}
        </span>
        <div className="ml-auto flex items-center gap-2 sm:ml-5">
          <CommandPalette onOpen={() => setMobileOpen(false)} />
          <span className="hidden items-center gap-1.5 font-mono text-[9px] uppercase text-ok lg:flex">
            <Boxes size={11} /> platform ready
          </span>
          <AuthStatus />
        </div>
      </header>

      <AnnouncementBanner />

      <div className="flex min-h-0 min-w-0 flex-1">
        <div className="hidden w-56 shrink-0 border-r border-line bg-ink/75 md:block">
          <Navigation />
        </div>
        <div id="main-content" className="min-h-0 min-w-0 flex-1 overflow-hidden">
          <Outlet />
        </div>
      </div>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label={t("shell.navigationMenu")}>
          <button
            type="button"
            className="absolute inset-0 bg-void/80 backdrop-blur-sm"
            onClick={closeMobile}
            aria-label={t("shell.closeNavigationBackdrop")}
          />
          <aside
            ref={drawerRef}
            className="relative flex h-full w-[min(18rem,84vw)] flex-col border-r border-line bg-ink shadow-card animate-slide-in"
          >
            <div className="flex h-13 shrink-0 items-center border-b border-line px-4">
              <span className="font-display text-sm font-semibold text-ice">{t("shell.navigationMenu")}</span>
              <button
                ref={closeButtonRef}
                type="button"
                onClick={closeMobile}
                className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
                aria-label={t("shell.closeNavigation")}
              >
                <X size={17} />
              </button>
            </div>
            <Navigation onNavigate={closeMobile} />
          </aside>
        </div>
      )}
    </div>
  );
}
