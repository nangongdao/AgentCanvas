"""Migrate legacy embedded Chroma vectors into the configured SQL backend."""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import Settings, get_settings, validate_runtime_settings
from app.db.base import create_engine, create_session_factory
from app.db.models import Document, DocumentChunk
from app.rag.store import ChromaVectorStore, SqlVectorStore, build_vector_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorMigrationResult:
    documents: int
    chunks: int
    backend: str


async def migrate_chroma_vectors(
    settings: Settings,
    source_path: Path | None = None,
) -> VectorMigrationResult:
    """Copy every ready document index from Chroma into SQL/pgvector.

    The first pass validates the complete source set before the second pass
    mutates the target. Re-running the command is safe because each document is
    replaced under a durable row lock.
    """

    validate_runtime_settings(settings)
    source = (source_path or settings.chroma_dir).expanduser().resolve()
    if not source.is_dir():
        raise RuntimeError(f"Chroma source directory does not exist: {source}")

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    target = build_vector_store(settings, session_factory)
    if not isinstance(target, SqlVectorStore):
        await target.close()
        await engine.dispose()
        raise RuntimeError(
            "vector migration target must be sql or pgvector; "
            "set VECTOR_BACKEND=pgvector for PostgreSQL"
        )
    legacy = ChromaVectorStore(source, max_concurrent=settings.chroma_max_concurrent)
    try:
        async with session_factory() as session:
            documents = list(
                (
                    await session.execute(
                        select(Document)
                        .where(Document.status == "ready")
                        .order_by(Document.id.asc())
                    )
                ).scalars()
            )

        # Complete preflight: no target row is changed until every ready source
        # document has a self-consistent legacy index.
        for document in documents:
            exported = await legacy.export_document(document.id)
            if exported is None:
                raise RuntimeError(f"ready document has no Chroma vectors: {document.id}")
            if exported.kb_id != document.kb_id:
                raise RuntimeError(f"Chroma knowledge base mismatch for document {document.id}")
            if exported.filename != document.filename:
                raise RuntimeError(f"Chroma filename mismatch for document {document.id}")
            if len(exported.chunks) != document.chunk_count:
                raise RuntimeError(
                    f"Chroma chunk count mismatch for document {document.id}: "
                    f"expected {document.chunk_count}, found {len(exported.chunks)}"
                )

        migrated_chunks = 0
        for document in documents:
            exported = await legacy.export_document(document.id)
            if exported is None:  # source changed after preflight
                raise RuntimeError(f"Chroma vectors disappeared for document {document.id}")
            await target.upsert_document(
                kb_id=exported.kb_id,
                document_id=exported.document_id,
                filename=exported.filename,
                chunks=exported.chunks,
                embeddings=exported.embeddings,
            )
            migrated_chunks += len(exported.chunks)

        async with session_factory() as session:
            stored_chunks = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(DocumentChunk)
                        .join(Document, Document.id == DocumentChunk.document_id)
                        .where(Document.status == "ready")
                    )
                ).scalar_one()
            )
        if stored_chunks != migrated_chunks:
            raise RuntimeError(
                "target chunk verification failed: "
                f"expected {migrated_chunks}, found {stored_chunks}"
            )
        return VectorMigrationResult(
            documents=len(documents),
            chunks=migrated_chunks,
            backend=target.backend_name,
        )
    finally:
        await legacy.close()
        await target.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.migrate_vectors",
        description="Copy embedded Chroma document vectors into SQL/pgvector.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="legacy Chroma directory (defaults to APP_DATA_DIR/chroma)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = asyncio.run(migrate_chroma_vectors(get_settings(), args.source))
    print(
        f"migrated {result.documents} document(s), {result.chunks} chunk(s) "
        f"into {result.backend}"
    )


if __name__ == "__main__":
    main()
