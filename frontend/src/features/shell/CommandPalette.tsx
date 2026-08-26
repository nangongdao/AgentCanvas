import {
  ArrowRight,
  Boxes,
  Copy,
  Database,
  FileText,
  Loader2,
  Plus,
  RotateCw,
  Search,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";

import {
  searchGlobalResources,
  type GlobalSearchResultDTO,
} from "@/api/endpoints/search";
import { useAuth } from "@/features/auth/AuthProvider";
import { useT, type Translate, type TranslationKey } from "@/features/i18n/i18n";
import {
  PLATFORM_DESTINATIONS,
  destinationDescription,
  destinationKeywords,
  destinationTitle,
  preloadDestination,
} from "@/features/shell/navigation";
import { cn } from "@/utils/cn";

type PaletteSection = "command" | "destination" | "resource";
type PaletteAction = "copy-link" | "new-workflow";

interface PaletteItem {
  id: string;
  section: PaletteSection;
  label: string;
  description: string;
  to?: string;
  action?: PaletteAction;
  icon: LucideIcon;
  keywords: string;
  required?: "editor" | "admin";
  /** C6-5: route-level preload hint fired when the item is highlighted. */
  preload?: () => void;
}

function commandItems(t: Translate): PaletteItem[] {
  return [
    {
      id: "command:new-workflow",
      section: "command",
      label: t("palette.newWorkflow.label"),
      description: t("palette.newWorkflow.description"),
      action: "new-workflow",
      icon: Plus,
      keywords: t("palette.newWorkflow.keywords"),
      required: "editor",
    },
    {
      id: "command:copy-link",
      section: "command",
      label: t("palette.copyLink.label"),
      description: t("palette.copyLink.description"),
      action: "copy-link",
      icon: Copy,
      keywords: t("palette.copyLink.keywords"),
    },
  ];
}

function destinationItems(t: Translate): PaletteItem[] {
  return PLATFORM_DESTINATIONS.filter(
    (destination) => destination.palette !== false,
  ).map((destination) => ({
    id: `destination:${destination.to}`,
    section: "destination" as const,
    label: destinationTitle(destination, t),
    description: destinationDescription(destination, t),
    to: destination.to,
    icon: destination.icon,
    keywords: destinationKeywords(destination, t),
    required: destination.required,
    preload: destination.preload
      ? () => preloadDestination(destination)
      : undefined,
  }));
}

const RESOURCE_PRESENTATION: Record<
  GlobalSearchResultDTO["kind"],
  { key: TranslationKey; icon: LucideIcon }
> = {
  workflow: { key: "palette.kind.workflow", icon: Workflow },
  app: { key: "palette.kind.app", icon: Boxes },
  knowledge_base: { key: "palette.kind.knowledge_base", icon: Database },
  document: { key: "palette.kind.document", icon: FileText },
};

function resourceRoute(result: GlobalSearchResultDTO): string {
  if (result.kind === "workflow") return `/workflows/${result.id}`;
  if (result.kind === "app") {
    const query = new URLSearchParams({ app_id: result.id });
    if (result.project_id) query.set("project_id", result.project_id);
    return `/apps?${query.toString()}`;
  }
  const query = new URLSearchParams();
  query.set("kb_id", result.kind === "document" ? (result.parent_id ?? "") : result.id);
  if (result.kind === "document") query.set("document_id", result.id);
  return `/knowledge?${query.toString()}`;
}

function resourceItem(result: GlobalSearchResultDTO, t: Translate): PaletteItem {
  const presentation = RESOURCE_PRESENTATION[result.kind];
  return {
    id: `resource:${result.kind}:${result.id}`,
    section: "resource",
    label: result.title,
    description: result.subtitle || t(presentation.key),
    to: resourceRoute(result),
    icon: presentation.icon,
    keywords: "",
  };
}

function sectionLabel(section: PaletteSection, t: Translate): string {
  if (section === "command") return t("palette.sectionCommand");
  if (section === "destination") return t("palette.sectionDestination");
  return t("palette.sectionResource");
}

export function CommandPalette({ onOpen }: { onOpen?: () => void }) {
  const navigate = useNavigate();
  const { can } = useAuth();
  const t = useT();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [remoteResults, setRemoteResults] = useState<
    readonly GlobalSearchResultDTO[]
  >([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [requestVersion, setRequestVersion] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const requestIdRef = useRef(0);

  const availableStaticItems = useMemo(
    () =>
      [...commandItems(t), ...destinationItems(t)].filter(
        (item) => item.required === undefined || can(item.required),
      ),
    [can, t],
  );

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const localResults = useMemo(() => {
    if (!normalizedQuery) return availableStaticItems;
    return availableStaticItems.filter((item) =>
      `${item.label} ${item.description} ${item.keywords}`
        .toLocaleLowerCase()
        .includes(normalizedQuery),
    );
  }, [availableStaticItems, normalizedQuery]);
  const items = useMemo(
    () => [
      ...localResults,
      ...remoteResults.map((result) => resourceItem(result, t)),
    ],
    [localResults, remoteResults, t],
  );
  const activeItemId = items[activeIndex]?.id;

  // C6-5: warm the highlighted destination's lazy chunk so Enter navigates
  // without a chunk-fetch delay. Preload is idempotent and best-effort.
  useEffect(() => {
    items[activeIndex]?.preload?.();
  }, [activeIndex, items]);

  const openPalette = useCallback(() => {
    const active = document.activeElement;
    returnFocusRef.current =
      active instanceof HTMLElement && active !== document.body
        ? active
        : triggerRef.current;
    onOpen?.();
    setOpen(true);
  }, [onOpen]);

  const closePalette = useCallback((restoreFocus = true) => {
    setOpen(false);
    setQuery("");
    setRemoteResults([]);
    setError(null);
    if (!restoreFocus) return;
    window.requestAnimationFrame(() => {
      const target = returnFocusRef.current;
      (target?.isConnected ? target : triggerRef.current)?.focus();
    });
  }, []);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        openPalette();
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [openPalette]);

  useEffect(() => {
    if (!open) return;
    window.requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  useEffect(() => {
    if (!open || !normalizedQuery) {
      requestIdRef.current += 1;
      setRemoteResults([]);
      setLoading(false);
      setError(null);
      return;
    }

    const controller = new AbortController();
    const requestId = ++requestIdRef.current;
    setRemoteResults([]);
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(() => {
      void searchGlobalResources(query.trim(), {
        signal: controller.signal,
      })
        .then((response) => {
          if (requestId === requestIdRef.current) setRemoteResults(response.items);
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted || requestId !== requestIdRef.current) return;
          setRemoteResults([]);
          setError(cause instanceof Error ? cause.message : t("palette.serviceUnavailable"));
        })
        .finally(() => {
          if (requestId === requestIdRef.current) setLoading(false);
        });
    }, 160);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [normalizedQuery, open, query, requestVersion, t]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query, remoteResults]);

  useEffect(() => {
    if (activeIndex < items.length) return;
    setActiveIndex(Math.max(0, items.length - 1));
  }, [activeIndex, items.length]);

  useEffect(() => {
    if (!open || !activeItemId) return;
    document.getElementById(activeItemId)?.scrollIntoView({ block: "nearest" });
  }, [activeItemId, open]);

  const selectItem = useCallback(
    (item: PaletteItem) => {
      closePalette();
      if (item.action === "copy-link") {
        void navigator.clipboard.writeText(window.location.href);
        return;
      }
      if (item.action === "new-workflow") {
        navigate("/workflows/new", {
          state: { newWorkflowCommand: crypto.randomUUID() },
        });
        return;
      }
      if (item.to) navigate(item.to);
    },
    [closePalette, navigate],
  );

  const handleDialogKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closePalette();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((current) => (items.length ? (current + 1) % items.length : 0));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) =>
        items.length ? (current - 1 + items.length) % items.length : 0,
      );
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(Math.max(0, items.length - 1));
      return;
    }
    if (event.key === "Enter" && items[activeIndex]) {
      event.preventDefault();
      selectItem(items[activeIndex]);
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>(
        'input, button:not([disabled]), [tabindex]:not([tabindex="-1"])',
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
  };

  let previousSection: PaletteSection | null = null;
  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={openPalette}
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-void/55 text-ghost transition hover:border-pulse/35 hover:bg-pulse/5 hover:text-ice sm:w-56 sm:justify-start sm:gap-2.5 sm:px-3 lg:w-72"
        aria-label={t("palette.triggerLabel")}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-keyshortcuts="Control+K Meta+K"
        title={t("palette.triggerLabel")}
      >
        <Search size={14} className="shrink-0" />
        <span className="hidden truncate text-xs sm:block">{t("palette.triggerLabel")}</span>
      </button>

      {open && (
        <div className="fixed inset-0 z-[70] flex items-start justify-center px-2 pt-[max(0.5rem,6dvh)] sm:px-4 sm:pt-[12dvh]">
          <button
            type="button"
            className="absolute inset-0 bg-void/85 backdrop-blur-sm"
            onClick={() => closePalette()}
            aria-label={t("palette.closeLabel")}
          />
          <section
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="command-palette-title"
            onKeyDown={handleDialogKeyDown}
            className="relative flex max-h-[min(42rem,88dvh)] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-line bg-ink shadow-card animate-fade-up"
          >
            <h2 id="command-palette-title" className="sr-only">
              {t("palette.title")}
            </h2>
            <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line px-3 sm:px-4">
              <Search size={17} className="shrink-0 text-pulse" />
              <input
                ref={inputRef}
                role="combobox"
                aria-label={t("palette.inputLabel")}
                aria-controls="command-palette-results"
                aria-expanded="true"
                aria-activedescendant={activeItemId}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("palette.placeholder")}
                autoComplete="off"
                spellCheck="false"
                className="min-w-0 flex-1 bg-transparent text-sm text-ice outline-hidden placeholder:text-ghost/45"
              />
              {loading && <Loader2 size={15} className="shrink-0 animate-spin text-pulse" />}
              <button
                type="button"
                onClick={() => closePalette()}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
                aria-label={t("palette.closeLabel")}
                title={t("palette.closeButtonTitle")}
              >
                <X size={16} />
              </button>
            </div>

            <div
              id="command-palette-results"
              role="listbox"
              aria-label={t("palette.title")}
              className="min-h-40 flex-1 overflow-y-auto py-2"
            >
              {items.map((item, index) => {
                const Icon = item.icon;
                const showSection = previousSection !== item.section;
                previousSection = item.section;
                return (
                  <div key={item.id} role="presentation">
                    {showSection && (
                      <div className="px-4 pb-1 pt-2 font-mono text-[9px] uppercase text-ghost">
                        {sectionLabel(item.section, t)}
                      </div>
                    )}
                    <button
                      id={item.id}
                      type="button"
                      role="option"
                      aria-selected={index === activeIndex}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => selectItem(item)}
                      className={cn(
                        "grid min-h-12 w-full grid-cols-[2.25rem_minmax(0,1fr)_1.5rem] items-center gap-2 border-l-2 px-3 text-left transition sm:px-4",
                        index === activeIndex
                          ? "border-pulse bg-pulse/10"
                          : "border-transparent hover:bg-line/45",
                      )}
                    >
                      <span
                        className={cn(
                          "flex h-8 w-8 items-center justify-center rounded-md border border-line bg-void/65",
                          index === activeIndex ? "text-pulse" : "text-ghost",
                        )}
                      >
                        <Icon size={15} />
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-xs font-medium text-ice">
                          {item.label}
                        </span>
                        <span className="mt-0.5 block truncate text-[10px] text-ghost">
                          {item.description}
                        </span>
                      </span>
                      <ArrowRight
                        size={13}
                        className={cn(
                          "justify-self-end",
                          index === activeIndex ? "text-pulse" : "text-ghost/35",
                        )}
                      />
                    </button>
                  </div>
                );
              })}

              {loading && items.length === 0 && (
                <div className="flex min-h-32 items-center justify-center gap-2 text-xs text-ghost">
                  <Loader2 size={14} className="animate-spin text-pulse" />
                  <span>{t("palette.searching")}</span>
                </div>
              )}
              {!loading && error && (
                <div className="flex min-h-32 flex-col items-center justify-center gap-3 px-6 text-center">
                  <p className="text-xs text-bad">{error}</p>
                  <button
                    type="button"
                    onClick={() => setRequestVersion((current) => current + 1)}
                    className="flex h-8 items-center gap-2 rounded-md border border-line px-3 text-xs text-ice transition hover:border-pulse/40 hover:text-pulse"
                  >
                    <RotateCw size={13} />
                    {t("palette.retry")}
                  </button>
                </div>
              )}
              {!loading && !error && normalizedQuery && items.length === 0 && (
                <div className="flex min-h-32 flex-col items-center justify-center px-6 text-center">
                  <Search size={18} className="mb-2 text-ghost/40" />
                  <p className="text-xs text-ghost">{t("palette.empty")}</p>
                </div>
              )}
            </div>
            <div className="h-px shrink-0 bg-pulse/45" />
            <span className="sr-only" aria-live="polite">
              {loading
                ? t("palette.searching")
                : error
                  ? t("palette.searchFailed")
                  : t("palette.resultCount", { count: items.length })}
            </span>
          </section>
        </div>
      )}
    </>
  );
}
