import { useEffect, useState } from "react";
import { Braces, RotateCcw } from "lucide-react";

import { DEFAULT_ITERATION_SUBGRAPH } from "@/features/canvas/iterationConfig";
import { cn } from "@/utils/cn";

type IterationConfig = Record<string, unknown>;

function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function textValue(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

interface Props {
  config: IterationConfig;
  onChange: (config: IterationConfig) => void;
}

export function IterationConfigEditor({ config, onChange }: Props) {
  const serialized = JSON.stringify(config.subgraph ?? DEFAULT_ITERATION_SUBGRAPH, null, 2);
  const [subgraphDraft, setSubgraphDraft] = useState(serialized);
  const [subgraphValid, setSubgraphValid] = useState(true);

  useEffect(() => {
    setSubgraphDraft(serialized);
    setSubgraphValid(true);
  }, [serialized]);

  const set = (key: string, value: unknown) => onChange({ ...config, [key]: value });
  const strategy = textValue(config.failure_strategy, "abort");

  return (
    <section className="space-y-4" aria-label="Iteration 配置">
      <label className="flex flex-col gap-1.5">
        <span className="font-mono text-[9px] uppercase text-ghost">数组表达式</span>
        <input
          className="field-input h-9 font-mono text-[11px]"
          value={textValue(config.items, "{{input.items}}")}
          onChange={(event) => set("items", event.target.value)}
        />
      </label>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1.5">
          <span className="font-mono text-[9px] uppercase text-ghost">单项变量</span>
          <input
            className="field-input h-9 font-mono text-[11px]"
            value={textValue(config.item_variable, "item")}
            onChange={(event) => set("item_variable", event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="font-mono text-[9px] uppercase text-ghost">索引变量</span>
          <input
            className="field-input h-9 font-mono text-[11px]"
            value={textValue(config.index_variable, "index")}
            onChange={(event) => set("index_variable", event.target.value)}
          />
        </label>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label="调度批大小"
          value={numberValue(config.batch_size, 10)}
          min={1}
          max={1000}
          onChange={(value) => set("batch_size", value)}
        />
        <NumberField
          label="并发上限"
          value={numberValue(config.concurrency_limit, 4)}
          min={1}
          max={100}
          onChange={(value) => set("concurrency_limit", value)}
        />
      </div>

      <fieldset>
        <legend className="mb-1.5 font-mono text-[9px] uppercase text-ghost">单项失败</legend>
        <div className="grid grid-cols-3 rounded-md border border-line bg-void/60 p-0.5">
          {[
            ["abort", "中止"],
            ["skip", "跳过"],
            ["collect_error", "收集"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={strategy === value}
              onClick={() => set("failure_strategy", value)}
              className={cn(
                "h-7 rounded-sm text-[10px] transition",
                strategy === value
                  ? "bg-pulse/15 text-pulse"
                  : "text-ghost hover:text-ice",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </fieldset>

      <NumberField
        label="子流程递归上限"
        value={numberValue(config.recursion_limit, 50)}
        min={2}
        max={1000}
        onChange={(value) => set("recursion_limit", value)}
      />

      <div>
        <div className="mb-1.5 flex items-center justify-between gap-3">
          <span className="font-mono text-[9px] uppercase text-ghost">子流程 DSL</span>
          <button
            type="button"
            onClick={() => set("subgraph", structuredClone(DEFAULT_ITERATION_SUBGRAPH))}
            className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
            title="恢复默认子流程"
          >
            <RotateCcw size={12} />
          </button>
        </div>
        <div className="relative">
          <Braces size={12} className="absolute right-2 top-2 text-ghost/40" />
          <textarea
            aria-label="子流程 DSL"
            className={cn(
              "min-h-64 w-full resize-y rounded-md border bg-void/70 px-3 py-2 pr-7 font-mono text-[10px] leading-5 text-ice outline-hidden",
              subgraphValid
                ? "border-line focus:border-pulse/60"
                : "border-bad/60 focus:border-bad",
            )}
            value={subgraphDraft}
            spellCheck={false}
            onChange={(event) => {
              setSubgraphDraft(event.target.value);
              try {
                const parsed = JSON.parse(event.target.value) as unknown;
                set("subgraph", parsed);
                setSubgraphValid(true);
              } catch {
                setSubgraphValid(false);
              }
            }}
          />
        </div>
      </div>
    </section>
  );
}

function NumberField(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[9px] uppercase text-ghost">{props.label}</span>
      <input
        type="number"
        className="field-input h-9 font-mono text-[11px]"
        value={props.value}
        min={props.min}
        max={props.max}
        onChange={(event) => {
          const next = Number(event.target.value);
          props.onChange(Number.isFinite(next) ? next : props.min);
        }}
      />
    </label>
  );
}
