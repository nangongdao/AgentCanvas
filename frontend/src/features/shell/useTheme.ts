import { useCallback, useEffect, useState } from "react";

export type ThemeMode = "light" | "dark" | "system";

const STORAGE_KEY = "agentcanvas:theme";

/** C5-8: resolve the *effective* theme (never "system") from the stored
 * preference, falling back to the OS `prefers-color-scheme`. Mirrors the
 * inline pre-paint script in index.html so server/client never disagree. */
function resolveEffective(stored: ThemeMode | null): "light" | "dark" {
  if (stored === "light" || stored === "dark") return stored;
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  return "dark";
}

function applyTheme(effective: "light" | "dark"): void {
  if (typeof document === "undefined") return;
  if (effective === "light") {
    document.documentElement.setAttribute("data-theme", "light");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
}

function readStored(): ThemeMode {
  if (typeof window === "undefined") return "system";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
  } catch {
    /* localStorage unavailable — treat as system default */
  }
  return "system";
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(() => readStored());
  const [effective, setEffective] = useState<"light" | "dark">(() =>
    resolveEffective(readStored()),
  );

  // Apply whenever the mode changes.
  useEffect(() => {
    const next = resolveEffective(mode);
    setEffective(next);
    applyTheme(next);
  }, [mode]);

  // When in system mode, follow OS changes live.
  useEffect(() => {
    if (mode !== "system") return;
    const mql = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => {
      const next = resolveEffective("system");
      setEffective(next);
      applyTheme(next);
    };
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [mode]);

  const setTheme = useCallback((next: ThemeMode) => {
    try {
      if (next === "system") {
        window.localStorage.removeItem(STORAGE_KEY);
      } else {
        window.localStorage.setItem(STORAGE_KEY, next);
      }
    } catch {
      /* best-effort; theme still applies for this session */
    }
    setMode(next);
  }, []);

  return { mode, effective, setTheme };
}
