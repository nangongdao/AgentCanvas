"""Online knowledge sources: fetch, sha256 change detection, API (C4-3)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.outbound_http import ResolvedDestination
from app.db.repositories import KnowledgeBaseRepo, OnlineSourceRepo
from app.main import create_app
from app.rag.embedder import LocalHashEmbeddingProvider
from app.rag.online_sources import (
    FetchedPage,
    OnlineFetchError,
    _extract_text,
    _html_to_text,
    _same_origin_links,
    crawl_source,
    fetch_page,
    fetch_sitemap_urls,
    page_to_section,
)
from app.rag.service import KnowledgeNotFoundError
from app.services.online_source_sync import OnlineSourceClaimLostError


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        local_embedding_dimensions=64,
        rate_limit_default_requests=1000,
        rate_limit_ingest_requests=1000,
        rate_limit_upload_requests=1000,
        rate_limit_retrieval_requests=1000,
        online_source_sync_poll_seconds=3600,
    )


@pytest.fixture
def public_resolve(monkeypatch):
    """Make outbound_http treat a synthetic host as a public destination."""
    from app.core import outbound_http

    async def fake_resolve(url: str) -> ResolvedDestination:
        return ResolvedDestination(host="public-edge.test", addresses=("203.0.113.10",))

    monkeypatch.setattr(outbound_http, "_resolve_public_destination_async", fake_resolve)


def test_html_to_text_strips_tags_and_collapses_whitespace() -> None:
    html = (
        "<html><head><style>a{}</style></head>"
        "<body><script>evil()</script>"
        "<h1>Title</h1><p>Hello&nbsp;&amp;<b>world</b></p>"
        '<a href="/x">link</a></body></html>'
    )
    assert _html_to_text(html) == "Title Hello & world link"


def test_same_origin_links_filters_cross_origin_and_anchors() -> None:
    html = (
        '<a href="/doc">doc</a>'
        '<a href="https://other.example/page">other</a>'
        '<a href="#frag">frag</a>'
        '<a href="mailto:x@example.com">mail</a>'
        '<a href="https://public-edge.test/sub">keep</a>'
    )
    links = _same_origin_links("https://public-edge.test/seed", html)
    assert links == ["https://public-edge.test/doc", "https://public-edge.test/sub"]


def test_text_link_and_section_boundary_helpers() -> None:
    assert _extract_text(b"  plain text\n", "text/plain", "https://example.test") == "plain text"
    assert _same_origin_links(
        "https://public-edge.test/seed",
        '<a href="/same">one</a><a href="/same">duplicate</a>',
    ) == ["https://public-edge.test/same"]
    assert _same_origin_links("ftp://public-edge.test/seed", '<a href="/file">file</a>') == []

    page = FetchedPage(
        url="https://public-edge.test/doc",
        text="retrieval body",
        sha256="a" * 64,
        status_code=200,
        content_type="text/plain",
    )
    section = page_to_section(page)
    assert section.text == "retrieval body"
    assert section.page is None


async def test_fetch_page_extracts_text_via_mock_transport(public_resolve) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/docs/handbook"
        return httpx.Response(
            200,
            content=b"<html><body><p>Canary rollout procedure.</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    page = await fetch_page(
        "https://public-edge.test/docs/handbook",
        transport=httpx.MockTransport(handler),
    )
    assert "Canary rollout procedure." in page.text
    assert page.status_code == 200
    assert page.sha256 == hashlib.sha256(
        b"<html><body><p>Canary rollout procedure.</p></body></html>"
    ).hexdigest()


async def test_fetch_page_rejects_non_public_destination() -> None:
    # 127.0.0.1 is not a public address; the SSRF guard must reject it before
    # any network call, regardless of the transport.
    with pytest.raises(OnlineFetchError, match="SSRF guard"):
        await fetch_page(
            "http://127.0.0.1:9999/secret",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x")),
        )


async def test_fetch_page_surfaces_http_errors(public_resolve) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"missing")

    with pytest.raises(OnlineFetchError, match="HTTP 404"):
        await fetch_page(
            "https://public-edge.test/missing",
            transport=httpx.MockTransport(handler),
        )


async def test_crawl_source_sitemap_bounded_by_max_pages(public_resolve) -> None:
    sitemap = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://public-edge.test/a</loc></url>
  <url><loc>https://public-edge.test/b</loc></url>
  <url><loc>https://public-edge.test/c</loc></url>
</urlset>"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sitemap.xml":
            return httpx.Response(200, content=sitemap.encode(), headers={"content-type": "application/xml"})
        return httpx.Response(
            200,
            content=f"<html><body>{path} page</body></html>".encode(),
            headers={"content-type": "text/html"},
        )

    pages = await crawl_source(
        "https://public-edge.test/sitemap.xml",
        max_pages=2,
        depth=1,
        transport=httpx.MockTransport(handler),
    )
    assert len(pages) == 2
    assert all(p.url.startswith("https://public-edge.test/") for p in pages)


async def test_crawl_source_follows_same_origin_links(public_resolve) -> None:
    seed_html = (
        '<html><body><p>seed</p>'
        '<a href="/page1">1</a><a href="/page2">2</a>'
        '<a href="https://cross.example/x">cross</a>'
        "</body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = seed_html if path == "/seed" else f"<html><body>{path}</body></html>"
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/html"}
        )

    pages = await crawl_source(
        "https://public-edge.test/seed",
        max_pages=3,
        depth=2,
        transport=httpx.MockTransport(handler),
    )
    urls = {p.url for p in pages}
    assert "https://public-edge.test/seed" in urls
    assert "https://public-edge.test/page1" in urls
    assert all("cross.example" not in url for url in urls)


async def test_fetch_sitemap_urls_recurses_into_index(public_resolve) -> None:
    index = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://public-edge.test/sitemap-1.xml</loc></sitemap>
</sitemapindex>"""
    child = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://public-edge.test/from-child</loc></url>
</urlset>"""

    def handler(request: httpx.Request) -> httpx.Response:
        content = index if request.url.path == "/sitemap.xml" else child
        return httpx.Response(200, content=content.encode(), headers={"content-type": "application/xml"})

    urls = await fetch_sitemap_urls(
        "https://public-edge.test/sitemap.xml",
        transport=httpx.MockTransport(handler),
        max_pages=10,
    )
    assert urls == ["https://public-edge.test/from-child"]


async def test_sitemap_failure_validation_and_bounds(public_resolve) -> None:
    index = """<?xml version="1.0"?>
<sitemapindex>
  <sitemap><loc></loc></sitemap>
  <sitemap><loc>https://public-edge.test/child.xml</loc></sitemap>
  <sitemap><loc>https://public-edge.test/unused.xml</loc></sitemap>
</sitemapindex>"""
    child = """<?xml version="1.0"?>
<urlset>
  <url><loc></loc></url>
  <url><loc>https://public-edge.test/a</loc></url>
  <url><loc>https://public-edge.test/b</loc></url>
</urlset>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/missing-sitemap.xml":
            return httpx.Response(404, content=b"missing")
        if request.url.path == "/invalid-sitemap.xml":
            return httpx.Response(200, content=b"<urlset>", headers={"content-type": "text/xml"})
        content = child if request.url.path == "/child.xml" else index
        return httpx.Response(200, content=content.encode(), headers={"content-type": "text/xml"})

    transport = httpx.MockTransport(handler)
    assert await fetch_sitemap_urls(
        "https://public-edge.test/missing-sitemap.xml",
        transport=transport,
        max_pages=1,
    ) == []
    with pytest.raises(OnlineFetchError, match="not valid XML"):
        await fetch_sitemap_urls(
            "https://public-edge.test/invalid-sitemap.xml",
            transport=transport,
            max_pages=1,
        )
    assert await fetch_sitemap_urls(
        "https://public-edge.test/sitemap.xml",
        transport=transport,
        max_pages=1,
    ) == ["https://public-edge.test/a"]
    assert await crawl_source(
        "https://public-edge.test/seed",
        max_pages=0,
        depth=1,
        transport=transport,
    ) == []
    assert await crawl_source(
        "https://public-edge.test/missing-sitemap.xml",
        max_pages=1,
        depth=1,
        transport=transport,
    ) == []


async def test_crawl_source_skips_failed_and_visited_links(public_resolve) -> None:
    seed = '<a href="/next">next</a><a href="/bad">bad</a>'
    next_page = '<a href="/seed">seed</a><a href="/bad">bad again</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bad":
            return httpx.Response(500, content=b"unavailable")
        body = seed if request.url.path == "/seed" else next_page
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/html"})

    pages = await crawl_source(
        "https://public-edge.test/seed",
        max_pages=5,
        depth=3,
        transport=httpx.MockTransport(handler),
    )
    assert [page.url for page in pages] == [
        "https://public-edge.test/seed",
        "https://public-edge.test/next",
    ]


def test_online_source_api_crud_and_sync_with_change_detection(tmp_path, public_resolve, monkeypatch) -> None:
    settings = _settings(tmp_path)
    pages_served = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        pages_served["count"] += 1
        # First sync returns v1; subsequent syncs return v2 (different body).
        body = (
            b"<html><body><p>canary v1</p></body></html>"
            if pages_served["count"] == 1
            else b"<html><body><p>canary v2 rollout update</p></body></html>"
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    mock_transport = httpx.MockTransport(handler)
    # Inject the transport into the sync service's crawl_source call so the
    # SSRF guard still resolves (public_resolve) but responses are mocked.
    import app.services.online_source_sync as sync_module

    original_crawl = sync_module.crawl_source

    async def crawl_with_mock(url, *, max_pages, depth, transport=None, **_kw):  # noqa: ANN001
        return await original_crawl(
            url, max_pages=max_pages, depth=depth, transport=mock_transport, **_kw
        )

    monkeypatch.setattr(sync_module, "crawl_source", crawl_with_mock)

    with TestClient(create_app(settings)) as client:
        kb = client.post(
            "/api/knowledge-bases",
            json={"name": "Online KB", "chunk_size": 200, "chunk_overlap": 20},
        ).json()
        kb_id = kb["id"]

        # Create an online source.
        created = client.post(
            f"/api/knowledge-bases/{kb_id}/online-sources",
            json={
                "url": "https://public-edge.test/handbook",
                "max_pages": 1,
                "depth": 0,
                "sync_interval_minutes": 60,
            },
        )
        assert created.status_code == 201, created.text
        source = created.json()
        assert source["status"] == "pending"
        assert source["url"] == "https://public-edge.test/handbook"
        assert source["sync_interval_minutes"] == 60
        assert source["next_sync_at"] is not None

        # Validation rejects out-of-range max_pages/depth.
        assert client.post(
            f"/api/knowledge-bases/{kb_id}/online-sources",
            json={"url": "https://public-edge.test/x", "max_pages": 51},
        ).status_code == 422
        assert client.post(
            f"/api/knowledge-bases/{kb_id}/online-sources",
            json={"url": "https://public-edge.test/x", "depth": 4},
        ).status_code == 422
        assert client.post(
            f"/api/knowledge-bases/{kb_id}/online-sources",
            json={"url": "https://public-edge.test/x", "sync_interval_minutes": 4},
        ).status_code == 422

        # First sync ingests content (sha change from None).
        first = client.post(f"/api/knowledge-bases/{kb_id}/online-sources/{source['id']}/sync")
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["status"] == "ready"
        assert first_body["content_sha256"]
        assert first_body["document_id"]
        first_sha = first_body["content_sha256"]

        # The ingested document is ready and has chunks.
        documents = client.get(f"/api/knowledge-bases/{kb_id}/documents").json()["items"]
        online_doc = next(d for d in documents if d["id"] == first_body["document_id"])
        assert online_doc["status"] == "ready", online_doc.get("error")
        assert online_doc["chunk_count"] > 0

        # Second sync with unchanged content should be a no-op re-embed.
        # Force the handler back to v1 by resetting the counter so the body
        # matches the first sync; sha stays equal → no new document.
        pages_served["count"] = 0
        second = client.post(f"/api/knowledge-bases/{kb_id}/online-sources/{source['id']}/sync")
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert second_body["status"] == "ready"
        assert second_body["content_sha256"] == first_sha
        assert second_body["document_id"] == first_body["document_id"]

        # Third sync with changed content re-embeds a new document.
        third = client.post(f"/api/knowledge-bases/{kb_id}/online-sources/{source['id']}/sync")
        assert third.status_code == 200, third.text
        third_body = third.json()
        assert third_body["content_sha256"] != first_sha

        # Update + delete.
        updated = client.put(
            f"/api/knowledge-bases/{kb_id}/online-sources/{source['id']}",
            json={"max_pages": 2, "depth": 1, "sync_interval_minutes": None},
        )
        assert updated.status_code == 200
        assert updated.json()["max_pages"] == 2
        assert updated.json()["sync_interval_minutes"] is None
        assert updated.json()["next_sync_at"] is None

        listed = client.get(f"/api/knowledge-bases/{kb_id}/online-sources").json()
        assert any(row["id"] == source["id"] for row in listed)

        assert (
            client.delete(f"/api/knowledge-bases/{kb_id}/online-sources/{source['id']}").status_code
            == 204
        )
        assert client.get(f"/api/knowledge-bases/{kb_id}/documents").json()["items"] == []
        assert (
            client.get(f"/api/knowledge-bases/{kb_id}/online-sources/{source['id']}").status_code
            == 405  # no GET-by-id route; listing is the read path
            or client.get(f"/api/knowledge-bases/{kb_id}/online-sources").json() == []
        )


def test_changed_online_source_embeds_only_changed_chunks_and_retires_old_document(
    tmp_path, monkeypatch
) -> None:
    import app.services.online_source_sync as sync_module

    versions = [
        ("stable page", "changing page v1"),
        ("stable page", "changing page v2"),
    ]
    fetch_index = 0

    async def controlled_crawl(_url, **_kwargs):  # noqa: ANN001
        nonlocal fetch_index
        stable, changing = versions[min(fetch_index, len(versions) - 1)]
        fetch_index += 1
        return [
            FetchedPage(
                url="https://public-edge.test/stable",
                text=stable,
                sha256="a" * 64,
                status_code=200,
                content_type="text/plain",
            ),
            FetchedPage(
                url="https://public-edge.test/changing",
                text=changing,
                sha256="b" * 64,
                status_code=200,
                content_type="text/plain",
            ),
        ]

    monkeypatch.setattr(sync_module, "crawl_source", controlled_crawl)

    class CountingProvider(LocalHashEmbeddingProvider):
        model_key = "online-source-counting-v1"

        def __init__(self) -> None:
            super().__init__(64)
            self.model_key = "online-source-counting-v1"
            self.calls: list[list[str]] = []

        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            self.calls.append(list(texts))
            return await super().embed(texts)

    provider = CountingProvider()

    with TestClient(create_app(_settings(tmp_path))) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert client.portal is not None

        async def counting_provider(_kb):  # noqa: ANN001
            return provider

        monkeypatch.setattr(container.rag_service.embeddings, "_provider", counting_provider)
        kb = client.post(
            "/api/knowledge-bases",
            json={
                "name": "Incremental online KB",
                "chunk_size": 200,
                "chunk_overlap": 0,
                "split_strategy": "heading",
            },
        ).json()
        source = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={"url": "https://public-edge.test/sitemap.xml", "max_pages": 2},
        ).json()

        first = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources/{source['id']}/sync"
        )
        assert first.status_code == 200, first.text
        first_document_id = first.json()["document_id"]

        visible_during_swap: set[str] = set()
        original_ingest = container.rag_service.ingest_document

        async def ingest_and_probe(kb_id, document_id, **kwargs):  # noqa: ANN001
            result = await original_ingest(kb_id, document_id, **kwargs)
            retrieval = await container.rag_service.retrieve(
                kb_id,
                "visibility probe",
                top_k=50,
                score_threshold=-1.0,
            )
            visible_during_swap.update(hit.document_id for hit in retrieval.hits)
            return result

        monkeypatch.setattr(container.rag_service, "ingest_document", ingest_and_probe)

        second = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources/{source['id']}/sync"
        )
        assert second.status_code == 200, second.text
        assert second.json()["document_id"] != first_document_id
        assert visible_during_swap == {first_document_id}
        ingest_calls = [call for call in provider.calls if call != ["visibility probe"]]
        assert [len(call) for call in ingest_calls] == [2, 1]
        assert "changing page v2" in provider.calls[1][0]

        documents = client.get(
            f"/api/knowledge-bases/{kb['id']}/documents"
        ).json()["items"]
        assert [row["id"] for row in documents] == [second.json()["document_id"]]


def test_online_source_sync_reports_failure(tmp_path, public_resolve, monkeypatch) -> None:
    settings = _settings(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"boom")

    import app.services.online_source_sync as sync_module

    original_crawl = sync_module.crawl_source
    mock_transport = httpx.MockTransport(handler)

    async def crawl_with_mock(url, *, max_pages, depth, transport=None, **_kw):  # noqa: ANN001
        return await original_crawl(
            url, max_pages=max_pages, depth=depth, transport=mock_transport, **_kw
        )

    monkeypatch.setattr(sync_module, "crawl_source", crawl_with_mock)

    with TestClient(create_app(settings)) as client:
        kb = client.post(
            "/api/knowledge-bases",
            json={"name": "Failing KB", "chunk_size": 200, "chunk_overlap": 20},
        ).json()
        source = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={"url": "https://public-edge.test/down"},
        ).json()
        failed = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources/{source['id']}/sync"
        )
        assert failed.status_code == 502, failed.text
        # The source row persists in failed state with an error message.
        listed = client.get(f"/api/knowledge-bases/{kb['id']}/online-sources").json()
        row = next(item for item in listed if item["id"] == source["id"])
        assert row["status"] == "failed"
        assert row["error"]


def test_online_source_sync_service_rejects_missing_source_and_kb(
    tmp_path, monkeypatch
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert client.portal is not None
        with pytest.raises(KnowledgeNotFoundError, match="missing-source"):
            client.portal.call(container.online_source_sync.sync_source, "missing-source")

        kb = client.post("/api/knowledge-bases", json={"name": "Missing KB boundary"}).json()
        source = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={"url": "https://public-edge.test/missing-kb"},
        ).json()

        async def missing_kb(_repo, _kb_id):  # noqa: ANN001
            return None

        monkeypatch.setattr(KnowledgeBaseRepo, "get", missing_kb)
        with pytest.raises(KnowledgeNotFoundError, match=kb["id"]):
            client.portal.call(container.online_source_sync.sync_source, source["id"])


def test_online_source_sync_service_marks_empty_and_ingest_failures(
    tmp_path, monkeypatch
) -> None:
    import app.services.online_source_sync as sync_module

    async def controlled_crawl(url, **_kwargs):  # noqa: ANN001
        if url.endswith("/empty"):
            return []
        return [
            FetchedPage(
                url=url,
                text="changed online content",
                sha256="f" * 64,
                status_code=200,
                content_type="text/plain",
            )
        ]

    monkeypatch.setattr(sync_module, "crawl_source", controlled_crawl)
    with TestClient(create_app(_settings(tmp_path))) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert client.portal is not None
        kb = client.post("/api/knowledge-bases", json={"name": "Failure boundaries"}).json()
        empty = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={"url": "https://public-edge.test/empty"},
        ).json()
        failing = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={"url": "https://public-edge.test/ingest-failure"},
        ).json()

        with pytest.raises(OnlineFetchError, match="no fetchable pages"):
            client.portal.call(container.online_source_sync.sync_source, empty["id"])

        monkeypatch.setattr(
            container.rag_service,
            "ingest_document",
            AsyncMock(side_effect=RuntimeError("embedding unavailable")),
        )
        with pytest.raises(RuntimeError, match="embedding unavailable"):
            client.portal.call(container.online_source_sync.sync_source, failing["id"])

        rows = client.get(
            f"/api/knowledge-bases/{kb['id']}/online-sources"
        ).json()
        by_id = {row["id"]: row for row in rows}
        assert by_id[empty["id"]]["status"] == "failed"
        assert by_id[failing["id"]]["status"] == "failed"
        assert "ingestion failed" in by_id[failing["id"]]["error"]


def test_online_source_sync_service_detects_rows_removed_mid_sync(
    tmp_path, monkeypatch
) -> None:
    import app.services.online_source_sync as sync_module

    async def one_page(url, **_kwargs):  # noqa: ANN001
        return [
            FetchedPage(
                url=url,
                text="stable online content",
                sha256="a" * 64,
                status_code=200,
                content_type="text/plain",
            )
        ]

    monkeypatch.setattr(sync_module, "crawl_source", one_page)
    with TestClient(create_app(_settings(tmp_path))) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert client.portal is not None
        monkeypatch.setattr(container.rag_service, "ingest_document", AsyncMock(return_value=None))
        kb = client.post("/api/knowledge-bases", json={"name": "Vanishing sources"}).json()
        unchanged = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={"url": "https://public-edge.test/unchanged"},
        ).json()

        first = client.portal.call(
            container.online_source_sync.sync_source, unchanged["id"]
        )
        assert first.status == "ready"

        original_get = OnlineSourceRepo.get
        calls = {"count": 0}

        async def vanish_on_second_get(repo, source_id):  # noqa: ANN001
            calls["count"] += 1
            if calls["count"] == 2:
                return None
            return await original_get(repo, source_id)

        monkeypatch.setattr(OnlineSourceRepo, "get", vanish_on_second_get)
        with pytest.raises(OnlineSourceClaimLostError, match="claim was reclaimed"):
            client.portal.call(container.online_source_sync.sync_source, unchanged["id"])

        monkeypatch.setattr(OnlineSourceRepo, "get", original_get)
        changed = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={"url": "https://public-edge.test/changed"},
        ).json()
        calls["count"] = 0
        monkeypatch.setattr(OnlineSourceRepo, "get", vanish_on_second_get)
        with pytest.raises(OnlineSourceClaimLostError, match="claim was reclaimed"):
            client.portal.call(container.online_source_sync.sync_source, changed["id"])

        monkeypatch.setattr(OnlineSourceRepo, "get", original_get)
        client.portal.call(
            container.online_source_sync._mark_failed,  # noqa: SLF001
            "missing-source",
            "already removed",
        )
