import { Check, Minus } from "lucide-react";

import {
  CAPABILITY_NAMES,
  type ProviderDescriptorDTO,
} from "@/api/endpoints/meta";
import { CAPABILITY_LABELS } from "@/features/models/capabilityPresentation";

export function ProviderCapabilityMatrix({
  providers,
}: {
  providers: ProviderDescriptorDTO[];
}) {
  if (providers.length === 0) return null;

  return (
    <section className="mb-6 min-w-0" aria-label="能力矩阵">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2
          id="provider-capability-title"
          aria-hidden="true"
          className="font-mono text-[10px] uppercase tracking-[0.2em] text-ghost/60"
        >
          Provider Capability Matrix
        </h2>
        <span className="font-mono text-[9px] text-ghost/45">{providers.length} adapters</span>
      </div>
      <div className="min-w-0 max-w-full overflow-x-auto border-y border-line/80 bg-ink/35">
        <table className="w-full min-w-[620px] border-collapse text-left" aria-label="能力矩阵">
          <thead>
            <tr className="border-b border-line/70 font-mono text-[9px] uppercase text-ghost/55">
              <th scope="col" className="w-44 px-3 py-2 font-medium">
                Provider
              </th>
              {CAPABILITY_NAMES.map((name) => (
                <th key={name} scope="col" className="px-2 py-2 text-center font-medium">
                  {CAPABILITY_LABELS[name]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {providers.map((provider) => (
              <tr key={provider.id} className="border-b border-line/40 last:border-b-0">
                <th scope="row" className="px-3 py-2.5 font-mono text-[10px] font-medium text-ice">
                  {provider.id}
                </th>
                {CAPABILITY_NAMES.map((name) => {
                  const available = provider.capabilities[name];
                  return (
                    <td key={name} className="px-2 py-2.5 text-center">
                      <span
                        className={
                          available
                            ? "inline-flex text-ok"
                            : "inline-flex text-ghost/25"
                        }
                        title={`${CAPABILITY_LABELS[name]}: ${available ? "支持" : "不支持"}`}
                      >
                        <span className="sr-only">
                          {CAPABILITY_LABELS[name]}: {available ? "支持" : "不支持"}
                        </span>
                        {available ? (
                          <Check aria-hidden="true" size={13} />
                        ) : (
                          <Minus aria-hidden="true" size={13} />
                        )}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
