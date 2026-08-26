import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Megaphone } from "lucide-react";

import { ANNOUNCEMENTS_CHANGED_EVENT } from "@/features/shell/AnnouncementBanner";

import {
  createAnnouncement,
  deleteAnnouncement,
  listAnnouncements,
  updateAnnouncement,
  type Announcement,
  type AnnouncementLevel,
} from "@/api/endpoints/adminConsole";

const LEVEL_LABELS: Record<AnnouncementLevel, string> = {
  info: "信息",
  warning: "警告",
  critical: "严重",
};

export function AdminAnnouncementsSection() {
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [message, setMessage] = useState("");
  const [level, setLevel] = useState<AnnouncementLevel>("info");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAnnouncements(await listAnnouncements());
      window.dispatchEvent(new Event(ANNOUNCEMENTS_CHANGED_EVENT));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载公告失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!message.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await createAnnouncement({ message: message.trim(), level });
      setMessage("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "发布公告失败");
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (announcement: Announcement) => {
    setBusy(true);
    setError(null);
    try {
      await updateAnnouncement(announcement.id, {
        is_active: !announcement.is_active,
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新公告失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (announcement: Announcement) => {
    setBusy(true);
    setError(null);
    try {
      await deleteAnnouncement(announcement.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除公告失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label="公告横幅"
      className="glass rounded-lg border border-line p-4"
    >
      <div className="mb-3 flex items-center gap-2">
        <Megaphone size={15} className="text-pulse" />
        <h2 className="font-display text-sm font-semibold">公告横幅</h2>
      </div>
      {error && (
        <p role="alert" className="mb-2 font-mono text-[10px] text-bad">
          {error}
        </p>
      )}
      <form onSubmit={submit} className="mb-4 flex flex-wrap gap-2">
        <label className="sr-only" htmlFor="announcement-message">
          公告内容
        </label>
        <input
          id="announcement-message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          maxLength={1000}
          placeholder="向全部登录用户发布横幅公告…"
          className="min-w-0 flex-1 rounded border border-line bg-void px-2 py-1.5 text-xs text-ice placeholder:text-ghost/60 focus:outline-none focus:ring-1 focus:ring-pulse"
        />
        <label className="sr-only" htmlFor="announcement-level">
          级别
        </label>
        <select
          id="announcement-level"
          value={level}
          onChange={(event) => setLevel(event.target.value as AnnouncementLevel)}
          className="rounded border border-line bg-void px-2 py-1.5 text-xs text-ice"
        >
          {(Object.keys(LEVEL_LABELS) as AnnouncementLevel[]).map((option) => (
            <option key={option} value={option}>
              {LEVEL_LABELS[option]}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={busy || !message.trim()}
          className="rounded border border-pulse/60 bg-pulse/10 px-3 py-1.5 text-xs text-pulse transition hover:bg-pulse/20 disabled:opacity-50"
        >
          发布
        </button>
      </form>
      {loading ? (
        <p className="font-mono text-[10px] uppercase text-ghost">loading…</p>
      ) : announcements.length === 0 ? (
        <p className="font-mono text-[10px] text-ghost">尚无公告</p>
      ) : (
        <ul className="space-y-1.5">
          {announcements.map((announcement) => (
            <li
              key={announcement.id}
              className="flex flex-wrap items-center gap-2 border-t border-line/60 py-1.5 text-xs"
            >
              <span
                className={
                  announcement.level === "critical"
                    ? "rounded bg-bad/10 px-1.5 py-0.5 text-[10px] text-bad"
                    : announcement.level === "warning"
                      ? "rounded bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn"
                      : "rounded bg-ok/10 px-1.5 py-0.5 text-[10px] text-ok"
                }
              >
                {LEVEL_LABELS[announcement.level]}
              </span>
              <span className="min-w-0 flex-1 truncate" title={announcement.message}>
                {announcement.message}
              </span>
              <span
                className={
                  announcement.is_active
                    ? "rounded bg-ok/10 px-1.5 py-0.5 text-[10px] text-ok"
                    : "rounded bg-line/60 px-1.5 py-0.5 text-[10px] text-ghost"
                }
              >
                {announcement.is_active ? "展示中" : "已下线"}
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() => void toggle(announcement)}
                className="rounded border border-line px-2 py-0.5 text-[10px] text-ghost transition hover:bg-line hover:text-ice disabled:opacity-50"
              >
                {announcement.is_active ? "下线" : "上线"}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void remove(announcement)}
                className="rounded border border-bad/40 px-2 py-0.5 text-[10px] text-bad transition hover:bg-bad/10 disabled:opacity-50"
              >
                删除
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
