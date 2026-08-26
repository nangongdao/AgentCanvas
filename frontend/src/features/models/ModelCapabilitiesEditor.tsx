import {
  CAPABILITY_NAMES,
  type ProviderCapabilityOverrides,
  type ProviderCapabilityProfile,
} from "@/api/endpoints/meta";
import { CAPABILITY_LABELS } from "@/features/models/capabilityPresentation";

export function ModelCapabilitiesEditor({
  defaults,
  overrides,
  kind,
  onChange,
}: {
  defaults: ProviderCapabilityProfile | undefined;
  overrides: ProviderCapabilityOverrides;
  kind: "chat" | "embedding";
  onChange: (overrides: ProviderCapabilityOverrides) => void;
}) {
  return (
    <fieldset className="border-t border-line pt-3" disabled={kind !== "chat"}>
      <legend className="mb-2 font-mono text-[9px] uppercase tracking-widest text-ghost/60">
        模型能力
      </legend>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        {CAPABILITY_NAMES.map((name) => {
          const supported = Boolean(defaults?.[name]);
          const enabled = kind === "chat" && supported && overrides[name] !== false;
          return (
            <label
              key={name}
              className="flex min-h-8 items-center justify-between gap-2 border-b border-line/50 text-[11px] text-ghost"
              title={supported ? `${CAPABILITY_LABELS[name]}可由模型配置关闭` : "Provider 未声明支持"}
            >
              <span className={supported && kind === "chat" ? "text-ice/80" : "text-ghost/35"}>
                {CAPABILITY_LABELS[name]}
              </span>
              <input
                type="checkbox"
                checked={enabled}
                disabled={kind !== "chat" || !supported}
                onChange={(event) => {
                  const next = { ...overrides };
                  if (event.target.checked) delete next[name];
                  else next[name] = false;
                  onChange(next);
                }}
                className="h-4 w-4 accent-volt disabled:opacity-25"
              />
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
