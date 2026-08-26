"""Pluggable rerank stage for hybrid retrieval (C4-1).

The default is disabled. When a knowledge base enables reranking, its
configured ``kind="rerank"`` model row points at an OpenAI-compatible
``POST {base_url}/rerank`` endpoint (Jina/Cohere/vLLM-style request shape);
the provider call maps returned relevance scores back onto the input order.
A local cross-encoder backend can be added behind the same protocol without
touching the retrieval pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

import httpx

from app.rag.store import VectorHit

logger = logging.getLogger(__name__)

_MAX_RERANK_DOCUMENTS = 50


class RerankError(RuntimeError):
    """Raised when a rerank provider call cannot be completed."""


class Reranker(Protocol):
    name: str

    async def rerank(self, query: str, documents: Sequence[str], *, top_k: int) -> list[float]:
        """Return one relevance score in [0, 1] aligned to ``documents``."""
        ...


class OpenAICompatReranker:
    """Client for an OpenAI-compatible /rerank endpoint."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.name = f"openai_compat_rerank:{model}"
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def rerank(self, query: str, documents: Sequence[str], *, top_k: int) -> list[float]:
        if not documents:
            return []
        body = {
            "model": self.model,
            "query": query,
            "documents": [document[:4000] for document in documents],
            "top_n": max(1, min(top_k, len(documents))),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/rerank", headers=headers, json=body)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RerankError(f"rerank provider call failed: {exc}") from exc
        scores = [0.0] * len(documents)
        for result in payload.get("results") or []:
            try:
                index = int(result["index"])
                score = float(result.get("relevance_score", result.get("score", 0.0)))
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= index < len(scores):
                scores[index] = max(0.0, min(1.0, score))
        return scores


class NoopReranker:
    """Preserves input order; used while reranking is disabled."""

    name = "disabled"

    async def rerank(self, query: str, documents: Sequence[str], *, top_k: int) -> list[float]:
        return [1.0] * len(documents)


async def apply_rerank(
    reranker: Reranker,
    query: str,
    hits: Sequence[VectorHit],
    *,
    top_k: int,
) -> list[tuple[VectorHit, float]] | None:
    """Rerank bounded candidates, returning hits with rerank scores.

    Returns ``None`` when the provider call fails so the caller can degrade to
    the pre-rerank order instead of failing the whole retrieval.
    """
    candidates = hits[:_MAX_RERANK_DOCUMENTS]
    try:
        scores = await reranker.rerank(query, [hit.text for hit in candidates], top_k=top_k)
    except RerankError:
        logger.warning("rerank failed; keeping pre-rerank order", exc_info=True)
        return None
    if len(scores) != len(candidates):
        logger.warning("rerank returned %d scores for %d documents", len(scores), len(candidates))
        return None
    return [(hit, score) for hit, score in zip(candidates, scores, strict=True)]


__all__ = [
    "NoopReranker",
    "OpenAICompatReranker",
    "RerankError",
    "Reranker",
    "apply_rerank",
]
