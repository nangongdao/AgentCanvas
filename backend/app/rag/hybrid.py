"""Reciprocal-rank fusion and score detail for hybrid retrieval (C4-1)."""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.rag.store import VectorHit

RRF_K = 60


@dataclass(frozen=True)
class HitScoreDetail:
    """Per-hit score breakdown surfaced to the retrieval debug view (C4-4)."""

    hit_id: str
    retrieval_mode: str
    vector_score: float | None = None
    keyword_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None
    rerank_applied: bool = False

    def as_dict(self) -> dict[str, float | str | bool | None]:
        return {
            "hit_id": self.hit_id,
            "retrieval_mode": self.retrieval_mode,
            "vector_score": self.vector_score,
            "keyword_score": self.keyword_score,
            "fused_score": self.fused_score,
            "rerank_score": self.rerank_score,
            "rerank_applied": self.rerank_applied,
        }


@dataclass
class _FusionEntry:
    hit: VectorHit
    vector_score: float | None = None
    keyword_score: float | None = None
    rank_sum: float = 0.0


def rrf_fuse(
    vector_hits: list[VectorHit],
    keyword_hits: list[VectorHit],
    *,
    k: int = RRF_K,
) -> list[tuple[VectorHit, HitScoreDetail]]:
    """Fuse two ranked lists with reciprocal-rank fusion.

    The fused score is normalized into ``[0, 1]`` by dividing the raw RRF sum
    by its theoretical maximum (rank 1 in every list, i.e. ``1/k`` each),
    keeping it comparable with component cosine/BM25 scores. Hits appearing in
    only one list keep the other component as ``None``.
    """
    entries: dict[str, _FusionEntry] = {}

    def record(hits: list[VectorHit], list_name: str) -> None:
        for rank, hit in enumerate(hits, start=1):
            entry = entries.get(hit.id)
            if entry is None:
                entry = _FusionEntry(hit=hit)
                entries[hit.id] = entry
            setattr(entry, f"{list_name}_score", hit.score)
            entry.rank_sum += 1.0 / (k + rank)

    record(vector_hits, "vector")
    record(keyword_hits, "keyword")

    maximum = 2.0 / k
    fused = sorted(
        ((min(1.0, entry.rank_sum / maximum), hit_id) for hit_id, entry in entries.items()),
        key=lambda item: (-item[0], item[1]),
    )

    results: list[tuple[VectorHit, HitScoreDetail]] = []
    for score, hit_id in fused:
        entry = entries[hit_id]
        merged = replace(entry.hit, score=round(score, 6))
        detail = HitScoreDetail(
            hit_id=hit_id,
            retrieval_mode="hybrid",
            vector_score=entry.vector_score,
            keyword_score=entry.keyword_score,
            fused_score=round(score, 6),
        )
        results.append((merged, detail))
    return results


def component_threshold(detail: HitScoreDetail, threshold: float) -> bool:
    """Hybrid threshold rule: a hit passes when any retriever is confident.

    RRF scores are rank-based and not calibrated to cosine confidence, so the
    user-facing ``score_threshold`` keeps its per-retriever meaning: the
    maximum of the contributing component scores must reach it.
    """
    scores = [value for value in (detail.vector_score, detail.keyword_score) if value is not None]
    if not scores:
        return False
    return max(scores) >= threshold


__all__ = ["HitScoreDetail", "RRF_K", "component_threshold", "rrf_fuse"]
