import { Database, Plus } from "lucide-react";

import type { KnowledgeBaseDTO } from "@/api/endpoints/knowledge";
import { useT } from "@/features/i18n/i18n";
import { cn } from "@/utils/cn";

interface Props {
  rows: KnowledgeBaseDTO[];
  activeId: string | null;
  loading: boolean;
  canEdit: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
}

export function KnowledgeSidebar(props: Props) {
  const t = useT();
  return (
    <aside className="glass flex max-h-60 w-full shrink-0 flex-col border-b border-line md:max-h-none md:w-72 md:border-b-0 md:border-r">
      <div className="flex h-12 items-center justify-between border-b border-line/70 px-4">
        <span className="font-mono text-[9px] uppercase text-ghost">
          Collections / {props.rows.length}
        </span>
        {props.canEdit && (
          <button
            type="button"
            onClick={props.onCreate}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-ok/10 hover:text-ok"
            title="新建知识库"
          >
            <Plus size={15} />
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2">
        {props.rows.map((row, index) => (
          <button
            key={row.id}
            type="button"
            onClick={() => props.onSelect(row.id)}
            className={cn(
              "mb-1 flex w-full items-center gap-3 rounded-md border px-3 py-3 text-left transition animate-slide-in",
              props.activeId === row.id
                ? "border-ok/35 bg-ok/10"
                : "border-transparent hover:border-line hover:bg-line/35",
            )}
            style={{ animationDelay: `${index * 35}ms` }}
          >
            <span
              className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-md border",
                props.activeId === row.id
                  ? "border-ok/30 bg-ok/10 text-ok"
                  : "border-line bg-void/50 text-ghost",
              )}
            >
              <Database size={14} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium text-ice">{row.name}</span>
              <span className="mt-0.5 block truncate font-mono text-[9px] text-ghost/55">
                {row.document_count} docs / {row.chunk_size}:{row.chunk_overlap}
              </span>
            </span>
          </button>
        ))}
        {!props.loading && props.rows.length === 0 && (
          <div
            className="flex flex-col items-center rounded-md border border-line bg-ink/60 px-4 py-8 text-center"
            data-testid="knowledge-onboarding-card"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-md border border-ok/30 bg-ok/10 text-ok">
              <Database size={16} strokeWidth={1.4} />
            </span>
            <h3 className="mt-3 text-[13px] font-semibold text-ice">
              {t("onboarding.knowledge.title")}
            </h3>
            <p className="mt-1.5 text-xs leading-5 text-ghost">
              {t("onboarding.knowledge.body")}
            </p>
            {props.canEdit && (
              <button
                type="button"
                onClick={props.onCreate}
                className="mt-4 flex h-9 items-center justify-center gap-2 rounded-md bg-ok px-4 text-xs font-semibold text-void transition hover:brightness-110"
              >
                <Plus size={13} />
                {t("onboarding.knowledge.create")}
              </button>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
