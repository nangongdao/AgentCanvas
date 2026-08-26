"""Document loaders for the P4 retrieval pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


class DocumentLoadError(ValueError):
    """Raised when an uploaded document cannot produce searchable text."""


@dataclass(frozen=True)
class LoadedSection:
    text: str
    page: int | None = None


def _load_text(path: Path) -> list[LoadedSection]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentLoadError("text documents must use UTF-8 encoding") from exc
    return [LoadedSection(text=text)]


def _load_pdf(path: Path) -> list[LoadedSection]:
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise DocumentLoadError("encrypted PDF documents are not supported")
        sections = [
            LoadedSection(text=page.extract_text() or "", page=index)
            for index, page in enumerate(reader.pages, start=1)
        ]
    except DocumentLoadError:
        raise
    except Exception as exc:
        raise DocumentLoadError("PDF could not be parsed") from exc
    return sections


def load_document(path: Path, filename: str) -> list[LoadedSection]:
    """Load supported content and reject documents without searchable text."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        sections = _load_pdf(path)
    elif suffix in {".txt", ".md", ".markdown"}:
        sections = _load_text(path)
    else:
        raise DocumentLoadError(f"unsupported document extension: {suffix or '(none)'}")

    if not any(section.text.strip() for section in sections):
        raise DocumentLoadError("document contains no searchable text")
    return sections
