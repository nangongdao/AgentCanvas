import { useState } from "react";
import { createPortal } from "react-dom";
import { Check, Loader2, Sparkles, TriangleAlert, X } from "lucide-react";

import { ApiError } from "@/api/client";
import { draftWorkflowWithCopilot, type CopilotDraftDTO } from "@/api/endpoints/copilot";
import { useDialogFocus } from "@/components/useDialogFocus";
import { useT } from "@/features/i18n/i18n";
import { useWorkflowStore } from "@/stores/workflowStore";
import { cn } from "@/utils/cn";

interface Props {
  canEdit: boolean;
  /** Soft editing lock held by the current user (collaborative editing). */
  editingAllowed: boolean;
  onNotify: (message: string) => void;
}

/**
 * Workflow AI Copilot: describe a workflow in natural language, get a draft
 * back, and apply it to the canvas.
 *
 * The draft is only ever a proposal — it is not persisted — and this panel
 * refuses to apply anything the backend validator rejected, so the copilot
 * cannot put an unrunnable graph on the canvas by accident. Applying goes
 * through ``applyCopilotDraft``, which is an ordinary undoable change rather
 * than the history-resetting ``loadDSL``.
 */
export function CopilotDialog({ canEdit, editingAllowed, onNotify }: Props) {
  const t = useT();
  const toDSL = useWorkflowStore((state) => state.toDSL);
  const canvasNodeCount = useWorkflowStore((state) => state.nodes.length);
  const applyCopilotDraft = useWorkflowStore((state) => state.applyCopilotDraft);
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [useCurrent, setUseCurrent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<CopilotDraftDTO | null>(null);

  const close = () => {
    setOpen(false);
    setBusy(false);
  };

  const dialogRef = useDialogFocus<HTMLElement>({ open, onClose: close });

  if (!canEdit) return null;

  const openDialog = () => {
    setError(null);
    setDraft(null);
    setUseCurrent(false);
    setOpen(true);
  };

  const generate = async () => {
    const trimmed = prompt.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    setDraft(null);
    try {
      const result = await draftWorkflowWithCopilot({
        prompt: trimmed,
        ...(useCurrent ? { base_dsl: toDSL() } : {}),
      });
      setDraft(result);
    } catch (cause) {
      const message = cause instanceof ApiError ? cause.message : String(cause);
      setError(t("copilot.failed", { message }));
    } finally {
      setBusy(false);
    }
  };

  const apply = () => {
    if (!draft?.valid || !editingAllowed) return;
    applyCopilotDraft(draft.dsl);
    onNotify(t("copilot.applied"));
    setPrompt("");
    setDraft(null);
    setOpen(false);
  };

  const nodeLabels = draft?.dsl.nodes.map((node) => node.name ?? node.type) ?? [];

  return (
    <>
      <button
        type="button"
        onClick={openDialog}
        aria-label={t("copilot.trigger")}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={t("copilot.trigger")}
        className="flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2 text-xs text-ghost transition hover:border-pulse/60 hover:bg-line/60 hover:text-pulse"
      >
        <Sparkles size={13} />
        <span className="hidden sm:inline">{t("copilot.trigger")}</span>
      </button>

      {open &&
        createPortal(
          <div className="fixed inset-0 z-[70] flex items-center justify-center bg-void/80 p-3 backdrop-blur-xs sm:p-6">
            <button
              type="button"
              className="absolute inset-0"
              onClick={close}
              aria-label={t("copilot.close")}
            />
            <section
              ref={dialogRef}
              tabIndex={-1}
              role="dialog"
              aria-modal="true"
              aria-labelledby="copilot-dialog-title"
              className="relative flex max-h-[min(44rem,90dvh)] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-line bg-ink shadow-card animate-fade-up"
            >
              <header className="flex items-start gap-3 border-b border-line px-4 py-3">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-pulse/40 bg-pulse/10 text-pulse">
                  <Sparkles size={14} />
                </span>
                <div className="min-w-0 flex-1">
                  <h2
                    id="copilot-dialog-title"
                    className="font-display text-sm font-semibold text-ice"
                  >
                    {t("copilot.title")}
                  </h2>
                  <p className="mt-0.5 text-xs text-ghost">{t("copilot.subtitle")}</p>
                </div>
                <button
                  type="button"
                  onClick={close}
                  aria-label={t("copilot.close")}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-line/60 hover:text-ice"
                >
                  <X size={15} />
                </button>
              </header>

              <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
                <div className="space-y-1.5">
                  <label
                    htmlFor="copilot-prompt"
                    className="block text-xs font-medium text-ghost"
                  >
                    {t("copilot.promptLabel")}
                  </label>
                  <textarea
                    id="copilot-prompt"
                    data-dialog-initial-focus
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                    rows={3}
                    maxLength={4000}
                    placeholder={t("copilot.promptPlaceholder")}
                    className="w-full resize-y rounded-md border border-line bg-void/50 px-3 py-2 text-sm text-ice outline-hidden transition placeholder:text-ghost/40 focus:border-pulse/60"
                  />
                </div>

                {canvasNodeCount > 0 && (
                  <label className="flex items-center gap-2 text-xs text-ghost">
                    <input
                      type="checkbox"
                      checked={useCurrent}
                      onChange={(event) => setUseCurrent(event.target.checked)}
                      className="h-3.5 w-3.5 accent-pulse"
                    />
                    {t("copilot.useCurrent")}
                  </label>
                )}

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void generate()}
                    disabled={busy || !prompt.trim()}
                    className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {busy ? (
                      <Loader2 size={13} className="animate-spin" />
                    ) : (
                      <Sparkles size={13} />
                    )}
                    <span>{busy ? t("copilot.generating") : t("copilot.generate")}</span>
                  </button>
                  <span className="font-mono text-[10px] text-ghost/50">
                    {prompt.length}/4000
                  </span>
                </div>

                {error && (
                  <p
                    role="alert"
                    className="flex items-start gap-1.5 rounded-md border border-bad/40 bg-bad/10 px-3 py-2 text-xs text-bad"
                  >
                    <TriangleAlert size={13} className="mt-0.5 shrink-0" />
                    <span>{error}</span>
                  </p>
                )}

                {draft && (
                  <div
                    aria-live="polite"
                    className="space-y-2 rounded-md border border-line bg-void/40 p-3"
                  >
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span
                        className={cn(
                          "flex items-center gap-1 rounded-sm px-1.5 py-0.5 font-mono text-[10px] uppercase",
                          draft.valid
                            ? "bg-ok/10 text-ok"
                            : "bg-bad/10 text-bad",
                        )}
                      >
                        {draft.valid ? <Check size={10} /> : <TriangleAlert size={10} />}
                        {draft.valid ? t("copilot.valid") : t("copilot.invalid")}
                      </span>
                      <span className="truncate font-display text-sm text-ice">
                        {draft.name}
                      </span>
                      <span className="font-mono text-[10px] text-ghost">
                        {t("copilot.nodes", { count: draft.dsl.nodes.length })} ·{" "}
                        {t("copilot.edges", { count: draft.dsl.edges.length })}
                      </span>
                      <span className="font-mono text-[10px] text-ghost/60">
                        {t("copilot.modelLabel")}: {draft.provider}/{draft.model}
                      </span>
                      <span className="font-mono text-[10px] text-ghost/60">
                        {t("copilot.attempts", { count: draft.attempts })}
                      </span>
                    </div>

                    {nodeLabels.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {nodeLabels.map((label, index) => (
                          <span
                            key={`${label}-${index}`}
                            className="rounded-sm border border-line bg-ink/70 px-1.5 py-0.5 text-[10px] text-ghost"
                          >
                            {label}
                          </span>
                        ))}
                      </div>
                    )}

                    {draft.errors.length > 0 && (
                      <div className="space-y-1">
                        <p className="font-mono text-[10px] uppercase text-bad">
                          {t("copilot.errors")}
                        </p>
                        <ul className="space-y-0.5 text-xs text-bad">
                          {draft.errors.map((message) => (
                            <li key={message}>· {message}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {draft.warnings.length > 0 && (
                      <div className="space-y-1">
                        <p className="font-mono text-[10px] uppercase text-warn">
                          {t("copilot.warnings")}
                        </p>
                        <ul className="space-y-0.5 text-xs text-warn">
                          {draft.warnings.map((message) => (
                            <li key={message}>· {message}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <footer className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
                <p className="min-w-0 flex-1 text-[11px] text-ghost/70">
                  {draft && !draft.valid ? t("copilot.invalidHint") : t("copilot.applyHint")}
                </p>
                <button
                  type="button"
                  onClick={close}
                  className="flex h-8 items-center rounded-md border border-line bg-ink/80 px-3 text-xs text-ghost transition hover:border-ghost/50 hover:text-ice"
                >
                  {t("copilot.discard")}
                </button>
                <button
                  type="button"
                  onClick={apply}
                  disabled={!draft?.valid || !editingAllowed}
                  title={editingAllowed ? undefined : t("copilot.locked")}
                  className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Check size={13} />
                  <span>{t("copilot.apply")}</span>
                </button>
              </footer>
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}
