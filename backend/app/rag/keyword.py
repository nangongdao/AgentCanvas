"""Shared keyword-retrieval scoring for hybrid search (C4-1).

A portable BM25 scorer plus a tokenizer that mixes latin word tokens with Han
character bigrams, so keyword matching works for English and Chinese content
alike. Stores without a server-side full-text engine (embedded Chroma, the
SQLite SQL fallback) rank chunk texts in process with this module;
PostgreSQL uses its tsvector index and only shares the tokenizer contract.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

_LATIN = re.compile(r"[a-z0-9_]+")
_HAN_RUN = re.compile(r"[\u4e00-\u9fff]+")

_K1 = 1.5
_B = 0.75

_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "with",
        "as",
        "at",
        "by",
        "from",
        "but",
        "not",
        "what",
        "which",
        "who",
        "when",
        "where",
        "why",
        "how",
        "does",
        "do",
        "did",
        "can",
        "的",
        "了",
        "是",
        "在",
        "和",
        "有",
        "对",
        "从",
        "与",
        "或",
    }
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens plus Han bigrams, minus common stopwords."""
    lowered = (text or "").lower()
    tokens: list[str] = _LATIN.findall(lowered)
    for run in _HAN_RUN.findall(lowered):
        tokens.append(run)
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return [token for token in tokens if token not in _STOPWORDS]


@dataclass
class KeywordDocument:
    """One scored candidate with its token statistics."""

    key: str
    text_length: int
    term_freqs: Counter = field(default_factory=Counter)


class InProcessBm25Index:
    """Classic BM25 ranking over an in-memory chunk corpus.

    Scores are normalized to ``[0, 1]`` via ``score / (score + 1)`` so they can
    travel through the same thresholding and fusion paths as cosine scores.
    """

    def __init__(self) -> None:
        self._documents: list[KeywordDocument] = []
        self._doc_freqs: Counter = Counter()
        self._total_length = 0

    @property
    def empty(self) -> bool:
        return not self._documents

    def add(self, key: str, text: str) -> None:
        terms = tokenize(text)
        document = KeywordDocument(
            key=key,
            text_length=max(1, len(terms)),
            term_freqs=Counter(terms),
        )
        self._documents.append(document)
        self._doc_freqs.update(document.term_freqs.keys())
        self._total_length += document.text_length

    def score(self, query: str) -> dict[str, float]:
        """Return normalized BM25 scores for every matching document key."""
        query_terms = [term for term in tokenize(query) if term in self._doc_freqs]
        if not query_terms or not self._documents:
            return {}
        average_length = self._total_length / len(self._documents)
        scores: dict[str, float] = {}
        document_count = len(self._documents)
        for document in self._documents:
            raw = 0.0
            for term in query_terms:
                frequency = document.term_freqs.get(term, 0)
                if frequency == 0:
                    continue
                doc_frequency = self._doc_freqs[term]
                idf = math.log(1.0 + (document_count - doc_frequency + 0.5) / (doc_frequency + 0.5))
                denominator = frequency + _K1 * (
                    1.0 - _B + _B * document.text_length / average_length
                )
                raw += idf * (frequency * (_K1 + 1.0)) / denominator
            if raw > 0.0:
                scores[document.key] = raw / (raw + 1.0)
        return scores


__all__ = ["InProcessBm25Index", "tokenize"]
