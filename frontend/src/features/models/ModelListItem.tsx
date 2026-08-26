import { Check, KeyRound, Minus, Settings2, ShieldAlert, Trash2 } from "lucide-react";

import { CAPABILITY_NAMES, type ModelConfigDTO } from "@/api/endpoints/meta";
import { CAPABILITY_LABELS } from "@/features/models/capabilityPresentation";
import { cn } from "@/utils/cn";

export function ModelListItem({
  model,
  canEdit,
  onEdit,
  onDelete,
}: {
  model: ModelConfigDTO;
  canEdit: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <article className="glass grid gap-3 rounded-lg border border-line px-4 py-3 transition hover:border-line/80 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[13px] font-medium text-ice">{model.name}</h3>
          {model.is_default && (
            <span className="rounded-xs border border-ok/40 bg-ok/10 px-1.5 py-0.5 font-mono text-[8px] uppercase text-ok">
              default
            </span>
          )}
          <span
            className={cn(
              "rounded-xs border px-1.5 py-0.5 font-mono text-[8px] uppercase",
              model.kind === "embedding"
                ? "border-pulse/40 bg-pulse/10 text-pulse"
                : "border-volt/40 bg-volt/10 text-volt",
            )}
          >
            {model.kind}
          </span>
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[10px] text-ghost/70">
          <span className="text-ice/80">{model.provider}</span>
          <span className="text-ghost/30">/</span>
          <span>{model.model_name}</span>
          {model.base_url && (
            <>
              <span className="text-ghost/30">/</span>
              <span className="max-w-full truncate">{model.base_url}</span>
            </>
          )}
          {(model.prompt_price_per_million_usd != null ||
            model.completion_price_per_million_usd != null) && (
            <>
              <span className="text-ghost/30">/</span>
              <span>
                ${model.prompt_price_per_million_usd ?? "?"} in · $
                {model.completion_price_per_million_usd ?? "?"} out / 1M
              </span>
              {model.pricing_version && <span>{model.pricing_version}</span>}
            </>
          )}
        </div>

        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1" aria-label="有效能力">
          {CAPABILITY_NAMES.map((name) => {
            const enabled = model.capabilities[name];
            return (
              <span
                key={name}
                data-capability={name}
                data-enabled={String(enabled)}
                className={cn(
                  "inline-flex items-center gap-1 font-mono text-[9px]",
                  enabled ? "text-ok" : "text-ghost/30",
                )}
                title={`${CAPABILITY_LABELS[name]}: ${enabled ? "启用" : "不可用"}`}
              >
                {enabled ? <Check size={10} /> : <Minus size={10} />}
                {CAPABILITY_LABELS[name]}
              </span>
            );
          })}
        </div>

        {model.capability_error && (
          <p className="mt-2 flex items-center gap-1.5 text-[10px] text-bad">
            <ShieldAlert size={12} />
            <span className="min-w-0 break-words">{model.capability_error}</span>
          </p>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 sm:justify-end">
        <span
          className={cn(
            "flex items-center gap-1 rounded-xs border px-2 py-0.5 font-mono text-[9px] uppercase",
            model.has_api_key
              ? "border-ok/40 bg-ok/10 text-ok"
              : "border-warn/40 bg-warn/10 text-warn",
          )}
          title={
            model.has_api_key
              ? `已配置凭据，来源：${model.api_key_source}`
              : "未配置 API Key"
          }
        >
          <KeyRound size={11} />
          {model.has_api_key ? `key · ${model.api_key_source}` : "no key"}
        </span>
        {canEdit && (
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={onEdit}
              className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
              title="编辑"
            >
              <Settings2 size={14} />
            </button>
            <button
              type="button"
              onClick={onDelete}
              className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-bad/10 hover:text-bad"
              title="删除"
            >
              <Trash2 size={14} />
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
