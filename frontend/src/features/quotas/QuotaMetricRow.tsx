import type { ReactNode } from "react";

import { useT } from "@/features/i18n/i18n";
import { cn } from "@/utils/cn";

export interface QuotaMetricView {
  key: string;
  icon: ReactNode;
  label: string;
  description: string;
  usage: string;
  limit: string;
  remaining: string;
  ratio: number | null;
  tone: "pulse" | "ok" | "volt" | "warn" | "bad";
  draft: string | null;
  inputLabel: string;
  inputStep?: string;
}

const TONES: Record<QuotaMetricView["tone"], { text: string; bar: string; border: string }> = {
  pulse: { text: "text-pulse", bar: "bg-pulse", border: "border-pulse/30" },
  ok: { text: "text-ok", bar: "bg-ok", border: "border-ok/30" },
  volt: { text: "text-volt", bar: "bg-volt", border: "border-volt/30" },
  warn: { text: "text-warn", bar: "bg-warn", border: "border-warn/30" },
  bad: { text: "text-bad", bar: "bg-bad", border: "border-bad/30" },
};

interface Props {
  metric: QuotaMetricView;
  editing: boolean;
  disabled: boolean;
  onDraftChange: (value: string | null) => void;
}

export function QuotaMetricRow({ metric, editing, disabled, onDraftChange }: Props) {
  const t = useT();
  const tone = TONES[metric.tone];
  const pressureTone =
    metric.ratio !== null && metric.ratio >= 100
      ? TONES.bad
      : metric.ratio !== null && metric.ratio >= 80
        ? TONES.warn
        : tone;

  return (
    <article className="grid min-h-32 gap-4 border-b border-line/80 px-4 py-4 last:border-b-0 sm:px-6 lg:grid-cols-[minmax(15rem,1.1fr)_minmax(14rem,1fr)_minmax(15rem,0.9fr)] lg:items-center">
      <div className="flex min-w-0 items-start gap-3">
        <span
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-md border bg-ink/80",
            tone.text,
            tone.border,
          )}
        >
          {metric.icon}
        </span>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-ice">{metric.label}</h2>
          <p className="mt-1 max-w-md text-[11px] leading-5 text-ghost/65">
            {metric.description}
          </p>
        </div>
      </div>

      <div className="min-w-0">
        <div className="mb-2 flex items-end justify-between gap-3">
          <div>
            <span className="font-mono text-[9px] uppercase text-ghost/50">{t("quotas.used")}</span>
            <p className="mt-0.5 truncate font-display text-lg font-semibold text-ice">
              {metric.usage}
            </p>
          </div>
          <span className={cn("font-mono text-[10px]", pressureTone.text)}>
            {metric.ratio === null ? t("quotas.unlimited") : `${Math.round(metric.ratio)}%`}
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-line">
          <div
            className={cn("h-full rounded-full transition-[width] duration-500", pressureTone.bar)}
            style={{ width: `${metric.ratio === null ? 0 : Math.min(100, metric.ratio)}%` }}
          />
        </div>
      </div>

      {editing ? (
        <div className="flex min-w-0 items-center justify-end gap-3">
          <label className="flex min-h-11 shrink-0 items-center gap-2 px-1 text-xs text-ghost">
            <input
              type="checkbox"
              aria-label={`${metric.label}${t("quotas.unlimited")}`}
              checked={metric.draft === null}
              disabled={disabled}
              onChange={(event) =>
                onDraftChange(event.target.checked ? null : "0")
              }
              className="h-4 w-4 accent-cyan-400"
            />
            {t("quotas.unlimited")}
          </label>
          {metric.draft !== null && (
            <label className="min-w-0 flex-1 sm:max-w-48">
              <span className="sr-only">{metric.inputLabel}</span>
              <input
                type="number"
                min="0"
                step={metric.inputStep ?? "1"}
                value={metric.draft}
                disabled={disabled}
                onChange={(event) => onDraftChange(event.target.value)}
                className="field-input h-11 py-0 text-right font-mono"
                aria-label={metric.inputLabel}
              />
            </label>
          )}
        </div>
      ) : (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-right">
          <div>
            <dt className="font-mono text-[9px] uppercase text-ghost/45">{t("quotas.limit")}</dt>
            <dd className="mt-1 truncate text-xs text-ice">{metric.limit}</dd>
          </div>
          <div>
            <dt className="font-mono text-[9px] uppercase text-ghost/45">{t("quotas.remaining")}</dt>
            <dd className={cn("mt-1 truncate text-xs", pressureTone.text)}>
              {metric.remaining}
            </dd>
          </div>
        </dl>
      )}
    </article>
  );
}
