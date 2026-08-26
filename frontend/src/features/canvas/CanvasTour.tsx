import { useState } from "react";
import { createPortal } from "react-dom";
import { Compass } from "lucide-react";

import { useDialogFocus } from "@/components/useDialogFocus";
import { useT, type TranslationKey } from "@/features/i18n/i18n";
import { cn } from "@/utils/cn";

export const CANVAS_TOUR_STORAGE_KEY = "agentcanvas:canvas-tour";

const STEP_TITLE_KEYS: readonly TranslationKey[] = [
  "tour.step1.title",
  "tour.step2.title",
  "tour.step3.title",
];
const STEP_BODY_KEYS: readonly TranslationKey[] = [
  "tour.step1.body",
  "tour.step2.body",
  "tour.step3.body",
];

function readTourDone(): boolean {
  try {
    return window.localStorage.getItem(CANVAS_TOUR_STORAGE_KEY) === "done";
  } catch {
    return false;
  }
}

/**
 * C5-10: three-step canvas tour shown once per browser on the first canvas
 * entry. Completing *or* skipping persists the dismissal, so the tour never
 * interrupts a returning editor. Viewers never see it — the tour teaches
 * editing gestures they cannot use.
 */
export function CanvasTour({
  open,
  onFinished,
}: {
  open: boolean;
  onFinished: () => void;
}) {
  const t = useT();
  const [step, setStep] = useState(0);

  const finish = () => {
    try {
      window.localStorage.setItem(CANVAS_TOUR_STORAGE_KEY, "done");
    } catch {
      /* best-effort; the tour simply reappears next session */
    }
    onFinished();
  };

  const dialogRef = useDialogFocus<HTMLElement>({
    open,
    onClose: () => finish(),
  });

  if (!open) return null;

  const total = STEP_TITLE_KEYS.length;
  const isLast = step === total - 1;

  const advance = () => {
    if (isLast) finish();
    else setStep((current) => Math.min(current + 1, total - 1));
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowRight" || event.key === "Enter") {
      event.preventDefault();
      advance();
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setStep((current) => Math.max(current - 1, 0));
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-90 flex items-center justify-center bg-void/80 p-4 backdrop-blur-xs">
      <section
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="canvas-tour-title"
        onKeyDown={onKeyDown}
        data-testid="canvas-tour"
        className="glass w-full max-w-sm rounded-lg border border-line p-5 shadow-card animate-fade-up"
      >
        <header className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-pulse/30 bg-pulse/10 text-pulse">
            <Compass size={17} />
          </span>
          <div className="min-w-0">
            <h2 id="canvas-tour-title" className="text-sm font-semibold text-ice">
              {t("tour.title")}
            </h2>
            <p className="mt-0.5 font-mono text-[9px] uppercase text-ghost">
              {t("tour.stepOf", { current: step + 1, total })}
            </p>
          </div>
        </header>

        <div className="mt-4 min-h-20">
          <h3 className="text-[13px] font-semibold text-ice">
            {t(STEP_TITLE_KEYS[step])}
          </h3>
          <p className="mt-1.5 text-xs leading-5 text-ghost">
            {t(STEP_BODY_KEYS[step])}
          </p>
        </div>

        <div className="mt-4 flex items-center gap-1.5" aria-hidden="true">
          {STEP_TITLE_KEYS.map((key, index) => (
            <span
              key={key}
              className={cn(
                "h-1 flex-1 rounded-xs transition",
                index <= step ? "bg-pulse" : "bg-line",
              )}
            />
          ))}
        </div>

        <footer className="mt-5 flex items-center gap-2">
          <button
            type="button"
            onClick={finish}
            className="flex h-9 items-center rounded-xs px-3 text-xs text-ghost transition hover:bg-line/60 hover:text-ice"
          >
            {t("tour.skip")}
          </button>
          <div className="ml-auto flex items-center gap-2">
            {step > 0 && (
              <button
                type="button"
                onClick={() => setStep((current) => Math.max(current - 1, 0))}
                className="flex h-9 items-center rounded-md border border-line px-3 text-xs text-ice transition hover:border-pulse/40 hover:text-pulse"
              >
                {t("tour.back")}
              </button>
            )}
            <button
              type="button"
              onClick={advance}
              className="flex h-9 items-center rounded-md bg-pulse px-4 text-xs font-semibold text-void transition hover:brightness-110"
            >
              {isLast ? t("tour.done") : t("tour.next")}
            </button>
          </div>
        </footer>
      </section>
    </div>,
    document.body,
  );
}

export function isCanvasTourDue(): boolean {
  return !readTourDone();
}
