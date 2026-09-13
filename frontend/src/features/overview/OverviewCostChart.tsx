import { useRef, useState, type KeyboardEvent } from "react";
import { ArrowRight, ChartNoAxesColumnIncreasing } from "lucide-react";
import { Link } from "react-router-dom";

import type { OverviewDayDTO } from "@/api/endpoints/overview";
import { useI18nStore, useT } from "@/features/i18n/i18n";

function knownCost(day: OverviewDayDTO): number | null {
  if (!day.cost_known || day.estimated_cost_usd === null) return null;
  const cost = Number(day.estimated_cost_usd);
  return Number.isFinite(cost) && cost >= 0 ? cost : null;
}

export function CostTrendPanel({
  daily,
  formatMoney,
}: {
  daily: readonly OverviewDayDTO[];
  formatMoney: (value: string | null) => string;
}) {
  const t = useT();
  const locale = useI18nStore((state) => state.locale);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const selected = daily.find((day) => day.date === selectedDate) ?? daily.at(-1);
  const knownCosts = daily.map(knownCost).filter((cost): cost is number => cost !== null);
  const maxCost = Math.max(0, ...knownCosts);
  const numberFormat = new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US");
  const dateFormat = new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
  const costLabel = (day: OverviewDayDTO) =>
    knownCost(day) === null ? t("overview.unpriced") : formatMoney(day.estimated_cost_usd);
  const selectWithKeyboard = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number;
    switch (event.key) {
      case "ArrowRight":
        next = Math.min(daily.length - 1, index + 1);
        break;
      case "ArrowLeft":
        next = Math.max(0, index - 1);
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = daily.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    buttons.current[next]?.focus();
  };

  return (
    <section aria-labelledby="cost-trend-title" className="p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="workspace-eyebrow">{t("overview.costCaption")}</p>
          <h2 id="cost-trend-title" className="mt-2 text-sm font-semibold text-ice">{t("overview.costTrend")}</h2>
        </div>
        <Link to="/cost" className="workspace-text-link">
          {t("overview.costLink")} <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </div>

      {selected ? (
        <>
          <div className="mt-5 flex min-h-14 flex-wrap items-center gap-x-4 gap-y-1" role="status" aria-live="polite" aria-atomic="true" data-testid="cost-day-detail">
            <span className="min-w-0 font-mono text-2xl font-medium tracking-tight text-ice">{costLabel(selected)}</span>
            <div className="text-[11px] leading-5 text-ghost">
              <time dateTime={selected.date}>{dateFormat.format(new Date(`${selected.date}T00:00:00Z`))}</time>
              <span aria-hidden="true"> · </span>
              <span>{t("overview.chartExecutions", { count: numberFormat.format(selected.executions) })}</span>
            </div>
          </div>
          <div className="overview-chart mt-4" role="group" aria-label={t("overview.costTrend")} aria-describedby="cost-chart-hint">
            <div className="overview-chart-grid" aria-hidden="true"><span /><span /><span /></div>
            <div className="relative grid gap-1 sm:gap-3" style={{ gridTemplateColumns: `repeat(${daily.length}, minmax(0, 1fr))` }}>
              {daily.map((day, index) => {
                const cost = knownCost(day);
                // Unknown amounts get a fixed, hatched marker, not a zero bar.
                // They never participate in the known-cost scale.
                const height = cost === null ? 26 : maxCost > 0 ? Math.max(2, (cost / maxCost) * 100) : 2;
                const active = day.date === selected.date;
                return (
                  <button
                    key={day.date}
                    ref={(element) => { buttons.current[index] = element; }}
                    type="button"
                    className="overview-chart-day"
                    aria-label={t("overview.chartDay", { date: day.date, cost: costLabel(day), count: numberFormat.format(day.executions) })}
                    aria-pressed={active}
                    tabIndex={active ? 0 : -1}
                    onMouseEnter={() => setSelectedDate(day.date)}
                    onFocus={() => setSelectedDate(day.date)}
                    onClick={() => setSelectedDate(day.date)}
                    onKeyDown={(event) => selectWithKeyboard(event, index)}
                  >
                    <span className="overview-chart-track" aria-hidden="true">
                      <span className="overview-chart-bar" data-known={cost !== null} style={{ height: `${height}%` }}>
                        {cost === null && <span className="text-xs font-medium text-warn">?</span>}
                      </span>
                    </span>
                    <span className="mt-3 font-mono text-[10px]">{day.date.slice(5)}</span>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-[10px] text-ghost">
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-xs bg-pulse" aria-hidden="true" />{t("overview.chartKnown")}</span>
            {knownCosts.length !== daily.length && <span className="flex items-center gap-1.5"><span className="overview-unknown-key h-2 w-2" aria-hidden="true" />{t("overview.chartUnknown")}</span>}
          </div>
          <p id="cost-chart-hint" className="mt-3 text-[10px] leading-relaxed text-ghost">{t("overview.chartHint")}</p>
        </>
      ) : (
        <div className="workspace-empty min-h-60">
          <ChartNoAxesColumnIncreasing size={24} className="text-ghost" aria-hidden="true" />
          <p className="text-sm font-medium text-ice">{t("overview.noActivity")}</p>
          <p className="max-w-xs text-xs leading-relaxed text-ghost">{t("overview.noActivityHint")}</p>
        </div>
      )}
    </section>
  );
}
