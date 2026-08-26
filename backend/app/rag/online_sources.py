"""URL fetching for online knowledge sources (C4-3).

Single-page and sitemap crawls are bounded by ``max_pages`` and ``depth`` so a
misconfigured source cannot expand unbounded. Every outbound request reuses the
shared SSRF-safe ``outbound_http`` transport (public-IP pinning, no redirects,
no env proxies, no Unix sockets) so an online source cannot become an SSRF
vector into the deployment's private network.

HTML pages are reduced to visible text via ``defuddle``-style tag stripping
(markdown source documents are kept verbatim); the result is fed into the
existing RAG ingestion pipeline as a ``LoadedSection``.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.core.outbound_http import OutboundDestinationError, request_public
from app.rag.loaders import LoadedSection

logger = logging.getLogger(__name__)

MAX_FETCH_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 20.0
_SITEMAP_NAMESPACES = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class FetchedPage:
    url: str
    text: str
    sha256: str
    status_code: int
    content_type: str
    raw_html: str = ""


class OnlineFetchError(RuntimeError):
    """Raised when an online source page cannot be fetched safely."""


async def fetch_page(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchedPage:
    """Fetch a single page through the SSRF-safe transport and extract text."""
    try:
        response = await request_public(
            "GET",
            url,
            headers={
                "Accept": "text/html, text/plain, text/markdown, application/xml, */*",
                "User-Agent": "AgentCanvas-RAG/1.0 (+online-sources)",
            },
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
    except OutboundDestinationError as exc:
        raise OnlineFetchError(f"SSRF guard rejected {url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise OnlineFetchError(f"fetch failed for {url}: {exc}") from exc

    if response.status_code >= 400:
        raise OnlineFetchError(
            f"fetch returned HTTP {response.status_code} for {url}"
        )

    raw = response.content[:MAX_FETCH_BYTES]
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    text = _extract_text(raw, content_type, url)
    raw_html = _decode(raw) if content_type.startswith("text/html") else ""
    digest = hashlib.sha256(raw).hexdigest()
    return FetchedPage(
        url=url,
        text=text,
        sha256=digest,
        status_code=response.status_code,
        content_type=content_type,
        raw_html=raw_html,
    )


def _extract_text(raw: bytes, content_type: str, url: str) -> str:
    """Reduce a fetched payload to searchable text without third-party deps."""
    if content_type in {"text/plain", "text/markdown", "text/x-markdown"}:
        return _decode(raw).strip()
    if content_type in {"application/xml", "text/xml"} and _looks_like_sitemap(raw):
        return _decode(raw).strip()
    html = _decode(raw)
    return _html_to_text(html)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _looks_like_sitemap(raw: bytes) -> bool:
    head = raw[:512].lower()
    return b"<urlset" in head or b"<sitemapindex" in head


def _html_to_text(html: str) -> str:
    """Strip tags, scripts, styles, and collapse whitespace (best-effort)."""
    cleaned = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", html)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("&nbsp;", " ").replace("&amp;", "&")
    cleaned = cleaned.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


async def fetch_sitemap_urls(
    sitemap_url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
    max_pages: int,
) -> list[str]:
    """Return page URLs from a sitemap (or sitemap index), bounded by max_pages."""
    try:
        page = await fetch_page(
            sitemap_url, timeout_seconds=timeout_seconds, transport=transport
        )
    except OnlineFetchError:
        return []
    try:
        root = ET.fromstring(page.text)
    except ET.ParseError as exc:
        raise OnlineFetchError(f"sitemap at {sitemap_url} is not valid XML: {exc}") from exc

    urls: list[str] = []
    if root.tag.endswith("sitemapindex"):
        for loc in root.iterfind("{*}sitemap/{*}loc"):
            child = (loc.text or "").strip()
            if not child:
                continue
            urls.extend(
                await fetch_sitemap_urls(
                    child,
                    timeout_seconds=timeout_seconds,
                    transport=transport,
                    max_pages=max_pages - len(urls),
                )
            )
            if len(urls) >= max_pages:
                return urls[:max_pages]
        return urls[:max_pages]

    for loc in root.iterfind("{*}url/{*}loc"):
        value = (loc.text or "").strip()
        if value:
            urls.append(value)
        if len(urls) >= max_pages:
            break
    return urls[:max_pages]


async def crawl_source(
    seed_url: str,
    *,
    max_pages: int,
    depth: int,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[FetchedPage]:
    """Crawl a single page or sitemap, bounded by max_pages and depth.

    When the seed looks like a sitemap (``/sitemap.xml`` or XML content type),
    its listed URLs are fetched directly. Otherwise a single page is fetched
    and same-origin links are followed up to ``depth`` hops.
    """
    if max_pages < 1:
        return []
    if depth < 1:
        depth = 1

    seed = seed_url.strip()
    if _is_sitemap_url(seed):
        urls = await fetch_sitemap_urls(
            seed,
            timeout_seconds=timeout_seconds,
            transport=transport,
            max_pages=max_pages,
        )
        pages: list[FetchedPage] = []
        for url in urls:
            try:
                pages.append(
                    await fetch_page(url, timeout_seconds=timeout_seconds, transport=transport)
                )
            except OnlineFetchError as exc:
                logger.info("online source page skipped: %s", exc)
            if len(pages) >= max_pages:
                break
        return pages

    page = await fetch_page(seed, timeout_seconds=timeout_seconds, transport=transport)
    pages = [page]
    if depth <= 1 or len(pages) >= max_pages:
        return pages

    visited = {seed}
    frontier = _same_origin_links(seed, page.raw_html or page.text)
    for _hop in range(depth - 1):
        next_frontier: list[str] = []
        for link in frontier:
            if link in visited or len(pages) >= max_pages:
                continue
            visited.add(link)
            try:
                linked = await fetch_page(
                    link, timeout_seconds=timeout_seconds, transport=transport
                )
            except OnlineFetchError as exc:
                logger.info("online source link skipped: %s", exc)
                continue
            pages.append(linked)
            next_frontier.extend(_same_origin_links(seed, linked.raw_html or linked.text))
            if len(pages) >= max_pages:
                break
        frontier = next_frontier
        if not frontier or len(pages) >= max_pages:
            break
    return pages[:max_pages]


def _is_sitemap_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".xml") and "sitemap" in path


def _same_origin_links(seed: str, text: str) -> list[str]:
    seed_origin = f"{urlparse(seed).scheme}://{urlparse(seed).netloc}"
    links: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'(?is)<a[^>]+href=["\']([^"\']+)["\']', text):
        href = match.group(1).strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        absolute = urljoin(seed, href)
        parsed = urlparse(absolute)
        if f"{parsed.scheme}://{parsed.netloc}" != seed_origin:
            continue
        if parsed.scheme not in {"http", "https"}:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
    return links


def page_to_section(page: FetchedPage) -> LoadedSection:
    """Wrap a fetched page as a loader section for the RAG ingestion pipeline."""
    return LoadedSection(text=page.text, page=None)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "FetchedPage",
    "OnlineFetchError",
    "crawl_source",
    "fetch_page",
    "fetch_sitemap_urls",
    "page_to_section",
]


# Quiet unused-symbol linters for Any kept for future transport extension.
_: Any = None
