import { useCallback } from "react";
import { create } from "zustand";

import { updateLanguagePreference } from "@/api/endpoints/auth";
import { en } from "./locales/en";
import { zh } from "./locales/zh";
import type { TranslationKey } from "./locales/zh";

export type Locale = "zh" | "en";
export type { TranslationKey };

const STORAGE_KEY = "agentcanvas:locale";

const DICTIONARIES: Record<Locale, Record<TranslationKey, string>> = { zh, en };

export type TranslateParams = Record<string, string | number>;

/** Resolve `{placeholder}` interpolations inside a translated string. */
export function interpolate(template: string, params?: TranslateParams): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match,
  );
}

function translate(
  locale: Locale,
  key: TranslationKey,
  params?: TranslateParams,
): string {
  const dictionary = DICTIONARIES[locale];
  const template = dictionary[key] ?? zh[key] ?? key;
  return interpolate(template, params);
}

function readStoredLocale(): Locale | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "zh" || raw === "en") return raw;
  } catch {
    /* localStorage unavailable — default below */
  }
  return null;
}

function applyDocumentLang(locale: Locale): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = locale;
}

interface I18nState {
  locale: Locale;
  /** Switch the active locale, persist it locally, and sync the document lang.
   * The server-side preference is persisted separately by the switcher UI
   * (account sessions only — token subjects have no user row). */
  setLocale: (locale: Locale) => void;
  /** Adopt the account's server-side language after login when the browser
   * has no explicit local choice, so preferences follow the user across
   * devices without fighting a deliberate local override. */
  hydrateFromSession: (language: string | null | undefined) => void;
}

export const useI18nStore = create<I18nState>((set, get) => ({
  locale: readStoredLocale() ?? "zh",
  setLocale: (locale) => {
    set({ locale });
    applyDocumentLang(locale);
    try {
      window.localStorage.setItem(STORAGE_KEY, locale);
    } catch {
      /* best-effort; locale still applies for this session */
    }
  },
  hydrateFromSession: (language) => {
    if (language !== "zh" && language !== "en") return;
    if (readStoredLocale() !== null) return;
    if (get().locale === language) {
      applyDocumentLang(language);
      return;
    }
    set({ locale: language });
    applyDocumentLang(language);
  },
}));

/** Persist the locale to the signed-in account (best-effort; token subjects
 * get a silent 403 and keep the browser-local choice). */
export async function syncLocaleToAccount(locale: Locale): Promise<void> {
  try {
    await updateLanguagePreference(locale);
  } catch {
    /* preference stays local for this browser */
  }
}

export type Translate = (
  key: TranslationKey,
  params?: TranslateParams,
) => string;

/** Component-facing translator bound to the active locale; the callback is
 * recreated on locale change so subscribers re-render with fresh strings. */
export function useT(): Translate {
  const locale = useI18nStore((state) => state.locale);
  return useCallback(
    (key, params) => translate(locale, key, params),
    [locale],
  );
}

/** Non-hook translator for module-scope code that cannot subscribe (store
 * bootstrapping, plain helpers). Reads the current locale at call time. */
export function tNow(key: TranslationKey, params?: TranslateParams): string {
  return translate(useI18nStore.getState().locale, key, params);
}
