import { create } from "zustand";

import { ApiError } from "@/api/client";
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  deleteKnowledgeDocument,
  getKnowledgeDocument,
  getKnowledgeBase,
  getIngestJob,
  ingestKnowledgeDocument,
  listKnowledgeBases,
  listKnowledgeDocuments,
  retrieveKnowledge,
  updateKnowledgeBase,
  uploadKnowledgeDocument,
  type IngestResultDTO,
  type KnowledgeBaseDTO,
  type KnowledgeBaseInput,
  type KnowledgeBaseUpdate,
  type KnowledgeDocumentDTO,
  type RetrievalResultDTO,
} from "@/api/endpoints/knowledge";

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

interface ResourceState {
  knowledgeBases: KnowledgeBaseDTO[];
  activeKnowledgeBaseId: string | null;
  documents: KnowledgeDocumentDTO[];
  retrieval: RetrievalResultDTO | null;
  loading: boolean;
  working: string | null;
  error: string | null;
  loadKnowledge: (preferredId?: string, preferredDocumentId?: string) => Promise<void>;
  selectKnowledgeBase: (id: string) => Promise<void>;
  createKnowledgeBase: (input: KnowledgeBaseInput) => Promise<KnowledgeBaseDTO>;
  updateKnowledgeBase: (
    id: string,
    input: KnowledgeBaseUpdate,
  ) => Promise<KnowledgeBaseDTO>;
  deleteKnowledgeBase: (id: string) => Promise<void>;
  uploadAndIngest: (file: File) => Promise<IngestResultDTO>;
  retryIngest: (documentId: string) => Promise<IngestResultDTO>;
  deleteDocument: (documentId: string) => Promise<void>;
  probeRetrieval: (
    query: string,
    topK: number,
    threshold: number,
  ) => Promise<RetrievalResultDTO>;
  clearError: () => void;
}

function replaceDocument(
  rows: KnowledgeDocumentDTO[],
  replacement: KnowledgeDocumentDTO,
): KnowledgeDocumentDTO[] {
  const found = rows.some((row) => row.id === replacement.id);
  return found
    ? rows.map((row) => (row.id === replacement.id ? replacement : row))
    : [replacement, ...rows];
}

async function waitForIngest(
  kbId: string,
  documentId: string,
  jobId: string | null | undefined,
  replace: (result: IngestResultDTO) => void,
): Promise<IngestResultDTO> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const result = await getIngestJob(kbId, documentId);
    replace(result);
    if (
      result.job_id === jobId &&
      (result.job_status === "succeeded" ||
        result.job_status === "failed" ||
        result.job_status === "cancelled")
    ) {
      return result;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 150));
  }
  throw new Error("document ingestion timed out");
}

export const useResourceStore = create<ResourceState>((set, get) => ({
  knowledgeBases: [],
  activeKnowledgeBaseId: null,
  documents: [],
  retrieval: null,
  loading: false,
  working: null,
  error: null,

  loadKnowledge: async (preferredId, preferredDocumentId) => {
    set({ loading: true, error: null });
    try {
      let knowledgeBases = (await listKnowledgeBases({ limit: 200 })).items;
      const current = preferredId ?? get().activeKnowledgeBaseId;
      let active = knowledgeBases.find((row) => row.id === current) ?? null;
      if (preferredId && !active) {
        active = await getKnowledgeBase(preferredId);
        knowledgeBases = [active, ...knowledgeBases];
      }
      active ??= knowledgeBases[0] ?? null;
      let documents = active
        ? (await listKnowledgeDocuments(active.id, { limit: 200 })).items
        : [];
      if (
        active &&
        preferredDocumentId &&
        !documents.some((document) => document.id === preferredDocumentId)
      ) {
        const target = await getKnowledgeDocument(active.id, preferredDocumentId);
        documents = [target, ...documents];
      }
      set({
        knowledgeBases,
        activeKnowledgeBaseId: active?.id ?? null,
        documents,
        retrieval: null,
      });
    } catch (error) {
      set({ error: errorText(error) });
      throw error;
    } finally {
      set({ loading: false });
    }
  },

  selectKnowledgeBase: async (id) => {
    set({ activeKnowledgeBaseId: id, loading: true, error: null, retrieval: null });
    try {
      set({ documents: (await listKnowledgeDocuments(id, { limit: 200 })).items });
    } catch (error) {
      set({ error: errorText(error) });
      throw error;
    } finally {
      set({ loading: false });
    }
  },

  createKnowledgeBase: async (input) => {
    set({ working: "create", error: null });
    try {
      const created = await createKnowledgeBase(input);
      await get().loadKnowledge(created.id);
      return created;
    } catch (error) {
      set({ error: errorText(error) });
      throw error;
    } finally {
      set({ working: null });
    }
  },

  updateKnowledgeBase: async (id, input) => {
    set({ working: "update", error: null });
    try {
      const updated = await updateKnowledgeBase(id, input);
      const documents = (await listKnowledgeDocuments(id, { limit: 200 })).items;
      set((state) => ({
        knowledgeBases: state.knowledgeBases.map((row) =>
          row.id === id ? { ...updated, document_count: documents.length } : row,
        ),
        documents,
        retrieval: null,
      }));
      return updated;
    } catch (error) {
      set({ error: errorText(error) });
      throw error;
    } finally {
      set({ working: null });
    }
  },

  deleteKnowledgeBase: async (id) => {
    set({ working: "delete-kb", error: null });
    try {
      await deleteKnowledgeBase(id);
      const remaining = get().knowledgeBases.filter((row) => row.id !== id);
      set({ knowledgeBases: remaining });
      if (remaining[0]) await get().selectKnowledgeBase(remaining[0].id);
      else {
        set({ activeKnowledgeBaseId: null, documents: [], retrieval: null });
      }
    } catch (error) {
      set({ error: errorText(error) });
      throw error;
    } finally {
      set({ working: null });
    }
  },

  uploadAndIngest: async (file) => {
    const kbId = get().activeKnowledgeBaseId;
    if (!kbId) throw new Error("Select a knowledge base first");
    set({ working: "upload", error: null });
    try {
      const pending = await uploadKnowledgeDocument(kbId, file);
      set((state) => ({
        documents: replaceDocument(state.documents, pending),
        knowledgeBases: state.knowledgeBases.map((row) =>
          row.id === kbId ? { ...row, document_count: row.document_count + 1 } : row,
        ),
      }));
      set({ working: pending.id });
      const accepted = await ingestKnowledgeDocument(kbId, pending.id);
      const result = await waitForIngest(kbId, pending.id, accepted.job_id, (next) => {
        set((state) => ({ documents: replaceDocument(state.documents, next.document) }));
      });
      if (result.document.status === "failed" || result.document.status === "pending") {
        throw new Error(result.document.error ?? "document ingestion failed");
      }
      return result;
    } catch (error) {
      set({ error: errorText(error) });
      throw error;
    } finally {
      set({ working: null });
    }
  },

  retryIngest: async (documentId) => {
    const kbId = get().activeKnowledgeBaseId;
    if (!kbId) throw new Error("Select a knowledge base first");
    set({ working: documentId, error: null });
    try {
      const accepted = await ingestKnowledgeDocument(kbId, documentId);
      const result = await waitForIngest(kbId, documentId, accepted.job_id, (next) => {
        set((state) => ({ documents: replaceDocument(state.documents, next.document) }));
      });
      if (result.document.status === "failed" || result.document.status === "pending") {
        throw new Error(result.document.error ?? "document ingestion failed");
      }
      return result;
    } catch (error) {
      set({ error: errorText(error) });
      throw error;
    } finally {
      set({ working: null });
    }
  },

  deleteDocument: async (documentId) => {
    const kbId = get().activeKnowledgeBaseId;
    if (!kbId) return;
    set({ working: documentId, error: null });
    try {
      await deleteKnowledgeDocument(kbId, documentId);
      set((state) => ({
        documents: state.documents.filter((row) => row.id !== documentId),
        retrieval: null,
        knowledgeBases: state.knowledgeBases.map((row) =>
          row.id === kbId
            ? { ...row, document_count: Math.max(0, row.document_count - 1) }
            : row,
        ),
      }));
    } catch (error) {
      set({ error: errorText(error) });
      throw error;
    } finally {
      set({ working: null });
    }
  },

  probeRetrieval: async (query, topK, threshold) => {
    const kbId = get().activeKnowledgeBaseId;
    if (!kbId) throw new Error("Select a knowledge base first");
    set({ working: "retrieve", error: null });
    try {
      const retrieval = await retrieveKnowledge(kbId, {
        query,
        top_k: topK,
        score_threshold: threshold,
        include_scores: true,
      });
      set({ retrieval });
      return retrieval;
    } catch (error) {
      set({ error: errorText(error) });
      throw error;
    } finally {
      set({ working: null });
    }
  },

  clearError: () => set({ error: null }),
}));
