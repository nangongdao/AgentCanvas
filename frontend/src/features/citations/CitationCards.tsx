import { useState } from "react";
import { ChevronDown, ExternalLink, FileText } from "lucide-react";

import { cn } from "@/utils/cn";

export interface Citation {
  id: string;
  label: string;
  kbId: string | null;
  documentId: string | null;
  filename: string;
  page: number | null;
  chunkIndex: number | null;
  score: number | null;
  text: string;
  parentText: string | null;
}

interface CitationCardsProps {
  citations?: Record<string, unknown>[];
  sourceHref?: (citation: Citation) => string | null;
  sourceTarget?: "_blank";
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function normalizeCitations(
  rows: Record<string, unknown>[] | undefined,
): Citation[] {
  if (!rows) return [];
  const seen = new Set<string>();
  const citations: Citation[] = [];
  for (const [index, row] of rows.entries()) {
    const id = stringValue(row.id);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    citations.push({
      id,
      label: stringValue(row.label) ?? `[${index + 1}]`,
      kbId: stringValue(row.kb_id),
      documentId: stringValue(row.document_id),
      filename: stringValue(row.filename) ?? "未知来源",
      page: numberValue(row.page),
      chunkIndex: numberValue(row.chunk_index),
      score: numberValue(row.score),
      text: stringValue(row.text) ?? "",
      parentText: stringValue(row.parent_text),
    });
  }
  return citations;
}

function platformSourceHref(citation: Citation): string | null {
  if (!citation.kbId || !citation.documentId) return null;
  const query = new URLSearchParams({
    kb_id: citation.kbId,
    document_id: citation.documentId,
  });
  return `/knowledge?${query.toString()}`;
}

export function CitationCards({
  citations: rawCitations,
  sourceHref = platformSourceHref,
  sourceTarget,
}: CitationCardsProps) {
  const citations = normalizeCitations(rawCitations);
  if (citations.length === 0) return null;

  return (
    <div className="mt-2 space-y-1.5" aria-label="引用来源">
      {citations.map((citation) => (
        <CitationCard
          key={citation.id}
          citation={citation}
          sourceHref={sourceHref(citation)}
          sourceTarget={sourceTarget}
        />
      ))}
    </div>
  );
}

function CitationCard({
  citation,
  sourceHref,
  sourceTarget,
}: {
  citation: Citation;
  sourceHref: string | null;
  sourceTarget?: "_blank";
}) {
  const [expanded, setExpanded] = useState(false);
  const widerContext =
    citation.parentText && citation.parentText !== citation.text
      ? citation.parentText
      : null;
  const metadata = [
    citation.page === null ? null : `第 ${citation.page} 页`,
    citation.chunkIndex === null ? null : `块 ${citation.chunkIndex + 1}`,
    citation.score === null ? null : `score ${citation.score.toFixed(3)}`,
  ].filter(Boolean);

  return (
    <div className="min-w-0 rounded-md border border-line bg-void/45 text-left">
      <div className="flex min-w-0 items-center gap-1.5 px-2 py-1.5">
        <button
          type="button"
          aria-expanded={expanded}
          aria-label={`${expanded ? "收起" : "展开"}引用 ${citation.label}`}
          onClick={() => setExpanded((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left text-ghost transition hover:text-ice"
        >
          <span className="font-mono text-[10px] font-semibold text-pulse">
            {citation.label}
          </span>
          <FileText size={12} className="shrink-0 text-volt" />
          <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-ice/90">
            {citation.filename}
          </span>
          <ChevronDown
            size={12}
            className={cn(
              "shrink-0 transition-transform",
              expanded && "rotate-180",
            )}
          />
        </button>
        {sourceHref && (
          <a
            href={sourceHref}
            target={sourceTarget}
            rel={sourceTarget ? "noreferrer" : undefined}
            aria-label={`查看原文 ${citation.filename}`}
            title="查看原文"
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-volt"
          >
            <ExternalLink size={12} />
          </a>
        )}
      </div>

      {expanded && (
        <div className="border-t border-line/70 px-2.5 py-2">
          {metadata.length > 0 && (
            <p className="mb-1.5 font-mono text-[9px] text-ghost/55">
              {metadata.join(" / ")}
            </p>
          )}
          {citation.text && (
            <p className="whitespace-pre-wrap wrap-break-word text-[11px] leading-5 text-ice/80">
              {citation.text}
            </p>
          )}
          {widerContext && (
            <div className="mt-2 border-l-2 border-volt/40 pl-2.5">
              <p className="mb-1 font-mono text-[9px] uppercase text-volt/75">
                所在分块上下文
              </p>
              <p className="max-h-56 overflow-auto whitespace-pre-wrap wrap-break-word text-[11px] leading-5 text-ghost/85">
                {widerContext}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
