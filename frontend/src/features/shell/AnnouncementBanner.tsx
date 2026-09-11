import { useCallback, useEffect, useState } from "react";
import { Megaphone, X } from "lucide-react";

import { listActiveAnnouncements } from "@/api/endpoints/adminConsole";
import type { Announcement } from "@/api/endpoints/adminConsole";
import { useT } from "@/features/i18n/i18n";

const DISMISS_STORAGE_KEY = "agentcanvas:dismissed-announcements";

/** Fired by the admin console after publishing/updating an announcement so
 *  open shells refresh the banner immediately instead of waiting for the poll. */
export const ANNOUNCEMENTS_CHANGED_EVENT = "agentcanvas:announcements-changed";

function dismissedMap(): Map<string, string> {
  try {
    const raw = window.sessionStorage.getItem(DISMISS_STORAGE_KEY);
    if (!raw) return new Map();
    const parsed: unknown = JSON.parse(raw);
    if (!(typeof parsed === "object" && parsed !== null)) return new Map();
    return new Map(Object.entries(parsed as Record<string, string>));
  } catch {
    return new Map();
  }
}

function persistDismissed(map: Map<string, string>): void {
  try {
    window.sessionStorage.setItem(
      DISMISS_STORAGE_KEY,
      JSON.stringify(Object.fromEntries(map)),
    );
  } catch {
    // Storage may be unavailable (private mode); dismissal just won't persist.
  }
}

const LEVEL_STYLES: Record<string, string> = {
  info: "border-line bg-ink text-ice",
  warning: "border-warn/40 bg-warn/10 text-warn",
  critical: "border-bad/40 bg-bad/10 text-bad",
};

/**
 * Platform-wide banner strip rendered under the global top bar (C7-3).
 * Every authenticated user sees active announcements; dismissal is
 * session-scoped and keyed by id+updated_at so an edited announcement
 * reappears.
 */
export function AnnouncementBanner() {
  const t = useT();
  const [visible, setVisible] = useState<Announcement[]>([]);

  const refresh = useCallback(async () => {
    try {
      const rows = await listActiveAnnouncements();
      const dismissed = dismissedMap();
      setVisible(
        rows.filter((row) => dismissed.get(row.id) !== row.updated_at),
      );
    } catch {
      // Banner is strictly additive; failures render nothing.
      setVisible([]);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 60_000);
    const onChanged = () => void refresh();
    window.addEventListener(ANNOUNCEMENTS_CHANGED_EVENT, onChanged);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener(ANNOUNCEMENTS_CHANGED_EVENT, onChanged);
    };
  }, [refresh]);

  const dismiss = (announcement: Announcement) => {
    const map = dismissedMap();
    map.set(announcement.id, announcement.updated_at);
    persistDismissed(map);
    setVisible((rows) => rows.filter((row) => row.id !== announcement.id));
  };

  if (visible.length === 0) return null;

  return (
    <div
      role="region"
      aria-label={t("shell.announcementRegion")}
      className="flex shrink-0 flex-col gap-px border-b border-line"
    >
      {visible.map((announcement) => (
        <div
          key={announcement.id}
          className={`flex items-center gap-2 border-b border-line/60 px-3 py-1.5 text-xs last:border-b-0 sm:px-5 ${
            LEVEL_STYLES[announcement.level] ?? LEVEL_STYLES.info
          }`}
        >
          <Megaphone size={12} className="shrink-0" />
          <p className="min-w-0 flex-1 truncate" title={announcement.message}>
            {announcement.message}
          </p>
          <button
            type="button"
            onClick={() => dismiss(announcement)}
            aria-label={t("shell.dismissAnnouncement")}
            className="flex h-5 w-5 shrink-0 items-center justify-center rounded transition hover:bg-line/60"
          >
            <X size={11} />
          </button>
        </div>
      ))}
    </div>
  );
}
