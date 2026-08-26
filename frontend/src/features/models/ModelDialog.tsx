import { useState, type ReactNode } from "react";
import { CheckCircle2, X } from "lucide-react";

import type {
  ModelConfigCreate,
  ModelConfigDTO,
  ModelConfigUpdate,
  ProviderCapabilityOverrides,
  ProviderDescriptorDTO,
} from "@/api/endpoints/meta";
import { useDialogFocus } from "@/components/useDialogFocus";
import { ModelCapabilitiesEditor } from "@/features/models/ModelCapabilitiesEditor";

export function ModelDialog({
  model,
  providerDescriptors,
  onClose,
  onSave,
}: {
  model: ModelConfigDTO | null;
  providerDescriptors: ProviderDescriptorDTO[];
  onClose: () => void;
  onSave: (input: ModelConfigCreate | ModelConfigUpdate, id?: string) => void;
}) {
  const [name, setName] = useState(model?.name ?? "");
  const providers = providerDescriptors.map((item) => item.id);
  const [provider, setProvider] = useState(model?.provider ?? providers[0] ?? "openai_compat");
  const [modelName, setModelName] = useState(model?.model_name ?? "");
  const [baseUrl, setBaseUrl] = useState(model?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [promptPrice, setPromptPrice] = useState(
    model?.prompt_price_per_million_usd ?? "",
  );
  const [completionPrice, setCompletionPrice] = useState(
    model?.completion_price_per_million_usd ?? "",
  );
  const [pricingVersion, setPricingVersion] = useState(model?.pricing_version ?? "");
  const [kind, setKind] = useState<"chat" | "embedding">(
    (model?.kind as "chat" | "embedding") ?? "chat",
  );
  const [isDefault, setIsDefault] = useState(model?.is_default ?? false);
  const [capabilityOverrides, setCapabilityOverrides] = useState<ProviderCapabilityOverrides>(
    model?.capability_overrides ?? {},
  );
  const dialogRef = useDialogFocus<HTMLDivElement>({ open: true, onClose });
  const providerDefaults = providerDescriptors.find((item) => item.id === provider)?.capabilities;

  const submit = () => {
    if (!name.trim() || !provider || !modelName.trim()) return;
    const payload: ModelConfigCreate | ModelConfigUpdate = {
      name: name.trim(),
      provider,
      model_name: modelName.trim(),
      base_url: baseUrl.trim() || null,
      kind,
      is_default: isDefault,
      prompt_price_per_million_usd: promptPrice.trim() || null,
      completion_price_per_million_usd: completionPrice.trim() || null,
      pricing_version: pricingVersion.trim() || null,
      capability_overrides: capabilityOverrides,
    };
    if (apiKey) (payload as ModelConfigCreate).api_key = apiKey;
    onSave(payload, model?.id);
  };

  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby="model-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-void/70 p-2 backdrop-blur-xs sm:p-4"
    >
      <div className="glass max-h-[calc(100dvh-1rem)] w-full max-w-xl overflow-y-auto rounded-xl border border-line p-4 shadow-card animate-fade-up sm:max-h-[calc(100dvh-2rem)] sm:p-5">
        <div className="mb-4 flex items-center justify-between">
          <h3 id="model-dialog-title" className="font-display text-sm font-semibold text-ice">
            {model ? "编辑模型" : "新建模型"}
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
            title="关闭"
          >
            <X size={15} />
          </button>
        </div>

        <div className="space-y-3">
          <Field label="名称">
            <input
              data-dialog-initial-focus
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="field-input"
              placeholder="例如 Claude Sonnet"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Provider">
              <select
                value={provider}
                onChange={(event) => {
                  setProvider(event.target.value);
                  setCapabilityOverrides({});
                }}
                className="field-input"
              >
                {providers.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="类型">
              <select
                value={kind}
                onChange={(event) => {
                  const nextKind = event.target.value as "chat" | "embedding";
                  setKind(nextKind);
                  if (nextKind === "embedding") setCapabilityOverrides({});
                }}
                className="field-input"
              >
                <option value="chat">chat</option>
                <option value="embedding">embedding</option>
              </select>
            </Field>
          </div>
          <Field label="模型名 (model_name)">
            <input
              value={modelName}
              onChange={(event) => setModelName(event.target.value)}
              className="field-input"
              placeholder="例如 claude-sonnet-5 / gpt-4o-mini"
            />
          </Field>
          <Field label="Base URL（可选）">
            <input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              className="field-input"
              placeholder="https://api.anthropic.com"
            />
          </Field>
          <Field label={model ? "API Key / Secret Reference（留空则不变）" : "API Key / Secret Reference（可选）"}>
            <input
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              className="field-input"
              placeholder="sk-... 或 env://OPENAI_API_KEY"
              autoComplete="off"
            />
            <p className="mt-1 font-mono text-[9px] leading-relaxed text-ghost/50">
              支持 env://变量名、docker://文件名、external://路径；引用只加密保存，运行时解析。
            </p>
          </Field>
          {kind === "chat" && (
            <div className="space-y-3 border-t border-line pt-3">
              <div className="grid grid-cols-2 gap-3">
                <Field label="输入价 / 百万 token">
                  <input
                    type="number"
                    min="0"
                    step="0.000001"
                    value={promptPrice}
                    onChange={(event) => setPromptPrice(event.target.value)}
                    className="field-input"
                    placeholder="例如 2.5"
                  />
                </Field>
                <Field label="输出价 / 百万 token">
                  <input
                    type="number"
                    min="0"
                    step="0.000001"
                    value={completionPrice}
                    onChange={(event) => setCompletionPrice(event.target.value)}
                    className="field-input"
                    placeholder="例如 10"
                  />
                </Field>
              </div>
              <Field label="价格版本">
                <input
                  value={pricingVersion}
                  onChange={(event) => setPricingVersion(event.target.value)}
                  className="field-input"
                  placeholder="例如 vendor-2026-08-01"
                />
              </Field>
            </div>
          )}
          <ModelCapabilitiesEditor
            defaults={providerDefaults}
            overrides={capabilityOverrides}
            kind={kind}
            onChange={setCapabilityOverrides}
          />
          <label className="flex items-center gap-2 text-xs text-ghost/80">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(event) => setIsDefault(event.target.checked)}
              className="accent-volt"
            />
            设为该类型的默认模型
          </label>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-line px-3 py-1.5 text-xs text-ghost transition hover:bg-line"
          >
            取消
          </button>
          <button
            type="button"
            onClick={submit}
            className="flex items-center gap-1.5 rounded-md bg-ok px-3 py-1.5 text-xs font-semibold text-void transition hover:brightness-110"
          >
            <CheckCircle2 size={13} />
            保存
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block font-mono text-[9px] uppercase tracking-widest text-ghost/60">
        {label}
      </span>
      {children}
    </label>
  );
}
