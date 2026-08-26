import { useEffect, useRef, useState } from "react";
import { Languages, LogOut, Monitor, Moon, ShieldCheck, Sun } from "lucide-react";

import { useAuth } from "@/features/auth/AuthProvider";
import {
  syncLocaleToAccount,
  useI18nStore,
  useT,
  type Locale,
} from "@/features/i18n/i18n";
import { useTheme, type ThemeMode } from "@/features/shell/useTheme";
import { cn } from "@/utils/cn";

const THEME_OPTIONS: Array<{ value: ThemeMode; labelKey: "account.theme.light" | "account.theme.dark" | "account.theme.system"; icon: typeof Sun }> = [
  { value: "light", labelKey: "account.theme.light", icon: Sun },
  { value: "dark", labelKey: "account.theme.dark", icon: Moon },
  { value: "system", labelKey: "account.theme.system", icon: Monitor },
];

const LOCALE_OPTIONS: Array<{ value: Locale; labelKey: "account.language.zh" | "account.language.en" }> = [
  { value: "zh", labelKey: "account.language.zh" },
  { value: "en", labelKey: "account.language.en" },
];

export function AuthStatus() {
  const { ready, authenticated, authEnabled, role, logout } = useAuth();
  const { mode, setTheme } = useTheme();
  const t = useT();
  const locale = useI18nStore((state) => state.locale);
  const setLocale = useI18nStore((state) => state.setLocale);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close the menu on outside click / Escape so it never lingers over the
  // canvas, and restore focus to the trigger for keyboard users.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!ready || !authenticated || !authEnabled) return null;

  const switchLocale = (next: Locale) => {
    setLocale(next);
    // Best-effort server persistence; token subjects keep a local choice.
    void syncLocaleToAccount(next);
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2.5 font-mono text-[9px] uppercase text-ok transition hover:border-ok/40 hover:bg-ok/5"
        title={t("account.menuTrigger")}
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <ShieldCheck size={13} /> {role}
      </button>
      {open && (
        <div
          className="glass absolute right-0 top-10 z-50 min-w-44 rounded-md border border-line p-1 shadow-card"
          role="group"
          aria-label={t("account.menuGroupLabel")}
        >
          <div className="px-3 pb-1 pt-2">
            <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-ghost">
              {t("account.appearance")}
            </p>
          </div>
          <div
            className="flex items-center gap-1 px-1.5 pb-2"
            role="radiogroup"
            aria-label={t("account.themeGroupLabel")}
          >
            {THEME_OPTIONS.map(({ value, labelKey, icon: Icon }) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={mode === value}
                onClick={() => setTheme(value)}
                title={t(labelKey)}
                className={cn(
                  "flex h-7 flex-1 items-center justify-center gap-1 rounded-xs text-[10px] transition",
                  mode === value
                    ? "bg-pulse text-void"
                    : "text-ghost hover:bg-line/50 hover:text-ice",
                )}
              >
                <Icon size={12} /> {t(labelKey)}
              </button>
            ))}
          </div>
          <div className="px-3 pb-1 pt-2">
            <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-ghost">
              {t("account.language")}
            </p>
          </div>
          <div
            className="flex items-center gap-1 px-1.5 pb-2"
            role="radiogroup"
            aria-label={t("account.languageGroupLabel")}
          >
            {LOCALE_OPTIONS.map(({ value, labelKey }) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={locale === value}
                onClick={() => switchLocale(value)}
                title={t(labelKey)}
                className={cn(
                  "flex h-7 flex-1 items-center justify-center gap-1 rounded-xs text-[10px] transition",
                  locale === value
                    ? "bg-pulse text-void"
                    : "text-ghost hover:bg-line/50 hover:text-ice",
                )}
              >
                <Languages size={12} /> {t(labelKey)}
              </button>
            ))}
          </div>
          <div className="mx-1.5 border-t border-line" />
          <button
            type="button"
            onClick={() => void logout()}
            className="flex h-8 w-full items-center gap-2 rounded-xs px-3 text-left text-xs text-ghost transition hover:bg-bad/10 hover:text-bad"
          >
            <LogOut size={13} /> {t("account.signOut")}
          </button>
        </div>
      )}
    </div>
  );
}
