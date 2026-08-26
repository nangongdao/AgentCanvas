"""Embedding providers plus a content-addressed database cache."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.secret_providers import SecretResolver
from app.core.security import SecretBox
from app.db.models import EmbeddingCache, KnowledgeBase
from app.db.repositories import EmbeddingCacheRepo, ModelConfigRepo
from app.services.project_quotas import ProjectQuotaService


class EmbeddingError(RuntimeError):
    """Raised when embeddings cannot be configured or generated."""


class EmbeddingProvider(Protocol):
    model_key: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    cache_hits: int
    cache_misses: int
    model_key: str


class LocalHashEmbeddingProvider:
    """Offline deterministic feature hashing for demos and tests."""

    def __init__(self, dimensions: int) -> None:
        if dimensions < 32:
            raise EmbeddingError("local embedding dimensions must be at least 32")
        self.dimensions = dimensions
        self.model_key = f"local-hash-v1:{dimensions}"

    def _features(self, text: str) -> list[str]:
        lowered = text.lower()
        latin = re.findall(r"[a-z0-9_]+", lowered)
        han = re.findall(r"[\u4e00-\u9fff]", lowered)
        han_bigrams = ["".join(han[index : index + 2]) for index in range(len(han) - 1)]
        features = latin + han + han_bigrams
        return features or [lowered.strip()]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for feature in self._features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout_seconds: int,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.model_key = f"openai_compat:{self.base_url}:{self.model}"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {"model": self.model, "input": list(texts)}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/embeddings", headers=headers, json=body
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc

        payload = response.json()
        items = sorted(payload.get("data") or [], key=lambda item: int(item["index"]))
        vectors = [item.get("embedding") for item in items]
        if len(vectors) != len(texts) or not all(isinstance(item, list) for item in vectors):
            raise EmbeddingError("embedding provider returned an invalid response")
        return [[float(value) for value in vector] for vector in vectors]


def _cache_key(model_key: str, text: str) -> str:
    return hashlib.sha256(f"{model_key}\0{text}".encode()).hexdigest()


def _encode_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _decode_vector(payload: bytes, dimensions: int) -> list[float]:
    expected = dimensions * 4
    if dimensions < 1 or len(payload) != expected:
        raise EmbeddingError("cached embedding has invalid dimensions")
    return list(struct.unpack(f"<{dimensions}f", payload))


class EmbeddingCoordinator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        secret_box: SecretBox,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.secret_box = secret_box
        self.secret_resolver = secret_resolver or SecretResolver(secret_box)

    async def _provider(self, kb: KnowledgeBase) -> EmbeddingProvider:
        async with self.session_factory() as session:
            repo = ModelConfigRepo(session)
            row = await repo.get(kb.embedding_model_id)
            if row is None and kb.embedding_model_id == "default-embedding":
                row = await repo.get_default("embedding")
        if row is None:
            raise EmbeddingError(f"embedding model config not found: {kb.embedding_model_id}")
        if row.kind != "embedding":
            raise EmbeddingError(f"model config is not an embedding model: {row.id}")
        api_key = self.secret_resolver.decrypt(row.api_key_encrypted or "")
        if not api_key:
            return LocalHashEmbeddingProvider(self.settings.local_embedding_dimensions)
        if row.provider != "openai_compat":
            raise EmbeddingError(f"embedding provider is not supported yet: {row.provider}")
        return OpenAIEmbeddingProvider(
            model=row.model_name,
            api_key=api_key,
            base_url=row.base_url or self.settings.openai_base_url,
            timeout_seconds=self.settings.embedding_timeout_seconds,
        )

    async def embed_texts(self, kb: KnowledgeBase, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult([], 0, 0, "none")
        provider = await self._provider(kb)
        keys = [_cache_key(provider.model_key, text) for text in texts]

        async with self.session_factory() as session:
            cached_rows = await EmbeddingCacheRepo(session).get_many(list(set(keys)))

        cached: dict[str, list[float]] = {}
        for key, row in cached_rows.items():
            try:
                cached[key] = _decode_vector(row.vector, row.dimensions)
            except EmbeddingError:
                continue

        missing: dict[str, str] = {}
        for key, text in zip(keys, texts, strict=True):
            if key not in cached:
                missing.setdefault(key, text)

        missing_items = list(missing.items())
        embedding_bytes = sum(len(text.encode("utf-8")) for _key, text in missing_items)
        if kb.project_id is not None and embedding_bytes:
            async with self.session_factory() as session:
                await ProjectQuotaService(session).charge_monthly(
                    kb.project_id,
                    "embedding_input_bytes",
                    embedding_bytes,
                )
                await session.commit()
        batches = [
            missing_items[index : index + self.settings.embedding_batch_size]
            for index in range(0, len(missing_items), self.settings.embedding_batch_size)
        ]
        semaphore = asyncio.Semaphore(self.settings.embedding_concurrency)

        async def embed_batch(batch: list[tuple[str, str]]) -> list[tuple[str, list[float]]]:
            async with semaphore:
                vectors = await provider.embed([text for _, text in batch])
            if len(vectors) != len(batch):
                raise EmbeddingError("embedding provider returned the wrong vector count")
            return [
                (key, self._validate_vector(vector))
                for (key, _), vector in zip(batch, vectors, strict=True)
            ]

        generated: list[tuple[str, list[float]]] = []
        if batches:
            try:
                async with asyncio.timeout(self.settings.embedding_timeout_seconds):
                    for result in await asyncio.gather(*(embed_batch(batch) for batch in batches)):
                        generated.extend(result)
            except TimeoutError as exc:
                raise EmbeddingError("embedding generation timed out") from exc

            rows = [
                EmbeddingCache(
                    cache_key=key,
                    model=provider.model_key,
                    dimensions=len(vector),
                    vector=_encode_vector(vector),
                )
                for key, vector in generated
            ]
            async with self.session_factory() as session:
                await EmbeddingCacheRepo(session).upsert_many(rows)
                await session.commit()
            cached.update(dict(generated))

        return EmbeddingResult(
            vectors=[cached[key] for key in keys],
            cache_hits=sum(key in cached_rows for key in keys),
            cache_misses=len(missing_items),
            model_key=provider.model_key,
        )

    @staticmethod
    def _validate_vector(vector: Sequence[float]) -> list[float]:
        values = [float(value) for value in vector]
        if not values or not all(math.isfinite(value) for value in values):
            raise EmbeddingError("embedding provider returned a non-finite vector")
        return values
