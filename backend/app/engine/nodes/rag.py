"""Knowledge retrieval node executor."""

from __future__ import annotations

from typing import Any

from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn, register_node
from app.engine.state import WorkflowState
from app.engine.templates import build_context, render_string
from app.schemas.dsl import NodeSpec, RagConfig
from app.schemas.events import EventType


@register_node("rag")
class RagNodeExecutor(BaseNodeExecutor):
    config_model = RagConfig
    output_schema = {
        "type": "object",
        "properties": {
            "output": {
                "description": "合并文本(merged_text)或检索块数组,取决于 output_format",
            },
            "text": {"type": "string", "description": "按引用标签拼接的合并文本"},
            "query": {"type": "string", "description": "渲染后的检索查询"},
            "chunks": {
                "type": "array",
                "description": "命中的检索块,含文本、分数与引用",
                "items": {"type": "object"},
            },
            "citations": {
                "type": "array",
                "description": "结构化引用来源,供前端 Sources 面板渲染",
                "items": {"type": "object"},
            },
        },
    }

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        cfg = RagConfig.model_validate(node.config or {})

        async def run(state: WorkflowState) -> dict[str, Any]:
            if ctx.rag_service is None:
                raise RuntimeError("RAG service is not configured")
            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )
            query = render_string(cfg.query, template_ctx).strip()
            if not query:
                raise ValueError("RAG query rendered to an empty value")
            if not cfg.kb_id:
                raise ValueError("RAG node requires a knowledge base")

            scope = {"project_id": ctx.project_id} if ctx.project_id is not None else {}
            result = await ctx.rag_service.retrieve(
                cfg.kb_id,
                query,
                top_k=cfg.top_k,
                score_threshold=cfg.score_threshold,
                **scope,
            )
            chunks: list[dict[str, Any]] = []
            citations: list[dict[str, Any]] = []
            for index, hit in enumerate(result.hits, start=1):
                label = f"[{index}]"
                citation = {
                    "id": hit.id,
                    "label": label,
                    "kb_id": hit.kb_id or cfg.kb_id,
                    "document_id": hit.document_id,
                    "filename": hit.filename,
                    "page": hit.page,
                    "chunk_index": hit.chunk_index,
                    "score": hit.score,
                    "text": hit.text,
                    "parent_text": hit.parent_text,
                }
                citations.append(citation)
                chunks.append({"text": hit.text, "score": hit.score, "citation": citation})

            merged = "\n\n".join(
                f"{citation['label']} {citation['text']}" for citation in citations
            )
            output: Any = merged if cfg.output_format == "merged_text" else chunks
            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node.id,
                payload={
                    "kind": "retrieval",
                    "query": query,
                    "hit_count": len(citations),
                    "citations": citations,
                },
            )
            return {
                "node_outputs": {
                    node.id: {
                        "output": output,
                        "text": merged,
                        "query": query,
                        "chunks": chunks,
                        "citations": citations,
                    }
                }
            }

        return run
