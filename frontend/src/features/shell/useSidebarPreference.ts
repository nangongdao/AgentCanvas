import { useState } from "react";

const STORAGE_KEY = "agentcanvas:sidebar-compact";

function readPreference(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    console.warn("Sidebar preference cannot be read; using expanded navigation.");
    return false;
  }
}

/** A browser-local layout preference. Storage failure must not disable navigation. */
export function useSidebarPreference() {
  const [compact, setCompact] = useState(readPreference);
  const toggleCompact = () => {
    const next = !compact;
    setCompact(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, String(next));
    } catch {
      console.warn("Sidebar preference could not be saved; it applies to this session only.");
    }
  };
  return [compact, toggleCompact] as const;
}
