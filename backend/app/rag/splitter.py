"""Deterministic chunk splitters with source offsets (C4-2).

Strategies:

* ``window`` (default): the original character-window splitter respecting
  natural boundaries (paragraph / line / sentence) and overlap.
* ``recursive``: a markdown-aware recursive splitter — headings first, then
  paragraphs, then sentences — so chunks keep topical coherence instead of
  cutting mid-sentence.
* ``heading``: per-heading chunking — every top-level markdown section becomes
  one chunk (or several when it exceeds ``chunk_size``).

Parent/child (``parent_chunk``): under ``heading``, child chunks keep a parent
reference so retrieval can return child granularity while citation display
expands the parent's wider context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.rag.loaders import LoadedSection

SplitStrategy = Literal["window", "recursive", "heading"]

_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    start_char: int
    end_char: int
    page: int | None = None
    parent_id: int | None = None
    parent_text: str | None = None


def _natural_end(text: str, start: int, hard_end: int, chunk_size: int) -> int:
    if hard_end >= len(text):
        return len(text)
    minimum = start + max(1, chunk_size // 2)
    candidates = (
        text.rfind("\n\n", minimum, hard_end),
        text.rfind("\n", minimum, hard_end),
        text.rfind(". ", minimum, hard_end),
        text.rfind("。", minimum, hard_end),
    )
    boundary = max(candidates)
    if boundary < minimum:
        return hard_end
    return boundary + (2 if text[boundary : boundary + 2] in {"\n\n", ". "} else 1)


def _window_section(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    base_offset: int,
    page: int | None,
    index_state: dict[str, int],
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    start = 0
    while start < len(text):
        while start < len(text) and text[start].isspace():
            start += 1
        if start >= len(text):
            break
        hard_end = min(len(text), start + chunk_size)
        end = _natural_end(text, start, hard_end, chunk_size)
        content = text[start:end].strip()
        if content:
            chunks.append(
                TextChunk(
                    index=index_state["index"],
                    text=content,
                    start_char=base_offset + start,
                    end_char=base_offset + end,
                    page=page,
                )
            )
            index_state["index"] += 1
        if end >= len(text):
            break
        start = max(start + 1, end - chunk_overlap)
    return chunks


def _heading_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans for each top markdown section."""
    matches = list(_MARKDOWN_HEADING.finditer(text))
    if not matches:
        return []
    spans: list[tuple[int, int]] = []
    for position, match in enumerate(matches):
        start = match.start()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        spans.append((start, end))
    return spans


def _heading_chunks(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    base_offset: int,
    page: int | None,
    index_state: dict[str, int],
    parent_chunk: bool,
) -> list[TextChunk]:
    spans = _heading_spans(text)
    if not spans:
        return _window_section(
            text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            base_offset=base_offset,
            page=page,
            index_state=index_state,
        )
    chunks: list[TextChunk] = []
    for start, end in spans:
        section = text[start:end]
        child_chunks = _window_section(
            section,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            base_offset=base_offset + start,
            page=page,
            index_state=index_state,
        )
        if not child_chunks:
            continue
        if parent_chunk and len(child_chunks) > 1:
            parent_text = section.strip()[: 8 * chunk_size]
            parent_id = index_state["parent"]
            index_state["parent"] += 1
            chunks.extend(
                TextChunk(
                    index=child.index,
                    text=child.text,
                    start_char=child.start_char,
                    end_char=child.end_char,
                    page=child.page,
                    parent_id=parent_id,
                    parent_text=parent_text,
                )
                for child in child_chunks
            )
        else:
            chunks.extend(child_chunks)
    return chunks


def _recursive_chunks(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    base_offset: int,
    page: int | None,
    index_state: dict[str, int],
) -> list[TextChunk]:
    """Recursive markdown-aware splitter: headings → paragraphs → sentences."""
    spans = _heading_spans(text)
    if spans:
        chunks: list[TextChunk] = []
        for start, end in spans:
            # Recurse into the section body, but the body's inner headings are
            # no longer re-split at the top level — only paragraphs/sentences —
            # so the recursion is bounded by the structural depth of the doc.
            body = text[start:end]
            body_no_headings = _MARKDOWN_HEADING.sub("", body, count=0)
            chunks.extend(
                _paragraph_or_sentence_chunks(
                    body_no_headings,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    base_offset=base_offset + start,
                    page=page,
                    index_state=index_state,
                )
            )
        return chunks
    return _paragraph_or_sentence_chunks(
        text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        base_offset=base_offset,
        page=page,
        index_state=index_state,
    )


def _paragraph_or_sentence_chunks(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    base_offset: int,
    page: int | None,
    index_state: dict[str, int],
) -> list[TextChunk]:
    """Split paragraphs → sentences → window, in that order."""
    paragraphs = _PARAGRAPH_SPLIT.split(text)
    if len(paragraphs) > 1:
        paragraph_chunks: list[TextChunk] = []
        cursor = 0
        for paragraph in paragraphs:
            offset = text.find(paragraph, cursor)
            if offset == -1:
                offset = cursor
            paragraph_chunks.extend(
                _paragraph_or_sentence_chunks(
                    paragraph,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    base_offset=base_offset + offset,
                    page=page,
                    index_state=index_state,
                )
            )
            cursor = offset + len(paragraph)
        return paragraph_chunks

    sentences = _SENTENCE_SPLIT.split(text)
    if len(sentences) > 1:
        sentence_chunks: list[TextChunk] = []
        cursor = 0
        bucket: list[str] = []
        bucket_start = 0
        for sentence in sentences:
            offset = text.find(sentence, cursor)
            if offset == -1:
                offset = cursor
            tentative = " ".join(bucket + [sentence])
            if len(tentative) > chunk_size and bucket:
                content = " ".join(bucket).strip()
                if content:
                    sentence_chunks.append(
                        TextChunk(
                            index=index_state["index"],
                            text=content,
                            start_char=base_offset + bucket_start,
                            end_char=base_offset + bucket_start + len(content),
                            page=page,
                        )
                    )
                    index_state["index"] += 1
                bucket = [sentence]
                bucket_start = offset
            else:
                if not bucket:
                    bucket_start = offset
                bucket.append(sentence)
            cursor = offset + len(sentence)
        if bucket:
            content = " ".join(bucket).strip()
            if content:
                sentence_chunks.append(
                    TextChunk(
                        index=index_state["index"],
                        text=content,
                        start_char=base_offset + bucket_start,
                        end_char=base_offset + bucket_start + len(content),
                        page=page,
                    )
                )
                index_state["index"] += 1
        return sentence_chunks

    return _window_section(
        text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        base_offset=base_offset,
        page=page,
        index_state=index_state,
    )


_STRATEGIES = {"window", "recursive", "heading"}


def _validate(
    *, chunk_size: int, chunk_overlap: int, strategy: SplitStrategy, parent_chunk: bool
) -> None:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
    if strategy not in _STRATEGIES:
        raise ValueError(f"unknown split strategy: {strategy}")
    if parent_chunk and strategy != "heading":
        raise ValueError("parent_chunk is only supported with the heading strategy")


def split_sections(
    sections: list[LoadedSection],
    *,
    chunk_size: int,
    chunk_overlap: int,
    strategy: SplitStrategy = "window",
    parent_chunk: bool = False,
) -> list[TextChunk]:
    """Split loaded document sections into ranked chunks."""
    _validate(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        strategy=strategy,
        parent_chunk=parent_chunk,
    )

    index_state: dict[str, int] = {"index": 0, "parent": 0}
    chunks: list[TextChunk] = []
    document_offset = 0
    for section in sections:
        text = section.text
        if strategy == "heading":
            section_chunks = _heading_chunks(
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                base_offset=document_offset,
                page=section.page,
                index_state=index_state,
                parent_chunk=parent_chunk,
            )
        elif strategy == "recursive":
            section_chunks = _recursive_chunks(
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                base_offset=document_offset,
                page=section.page,
                index_state=index_state,
            )
        else:
            section_chunks = _window_section(
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                base_offset=document_offset,
                page=section.page,
                index_state=index_state,
            )
        chunks.extend(section_chunks)
        document_offset += len(text) + 1
    return chunks


def preview_chunks(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    strategy: SplitStrategy,
    parent_chunk: bool,
    limit: int = 20,
) -> list[TextChunk]:
    """Render a preview of how a document would chunk — no side effects."""
    return split_sections(
        [LoadedSection(text=text)],
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        strategy=strategy,
        parent_chunk=parent_chunk,
    )[:limit]


__all__ = ["SplitStrategy", "TextChunk", "preview_chunks", "split_sections"]
