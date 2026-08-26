"""Bounded, traversal-safe storage for uploaded knowledge documents."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


class UploadValidationError(ValueError):
    """Raised when an upload violates the file policy."""


@dataclass(frozen=True)
class StoredUpload:
    document_id: str
    filename: str
    relative_path: str
    mime_type: str
    size_bytes: int
    sha256: str


class DocumentStorage:
    _MIME_BY_SUFFIX = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
    }
    _ALLOWED_CLIENT_MIMES = {
        "",
        "application/octet-stream",
        "application/pdf",
        "text/markdown",
        "text/plain",
        "text/x-markdown",
    }

    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def _validate_name(self, filename: str) -> tuple[str, str]:
        if not filename or filename in {".", ".."} or "\x00" in filename:
            raise UploadValidationError("a valid filename is required")
        if "/" in filename or "\\" in filename or Path(filename).name != filename:
            raise UploadValidationError("filename must not contain a path")
        suffix = Path(filename).suffix.lower()
        if suffix not in self._MIME_BY_SUFFIX:
            raise UploadValidationError("supported formats are PDF, Markdown, and text")
        return filename, suffix

    def _safe_path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if not candidate.is_relative_to(self.root):
            raise UploadValidationError("stored document path escapes the upload root")
        return candidate

    async def save(self, kb_id: str, upload: UploadFile) -> StoredUpload:
        filename, suffix = self._validate_name(upload.filename or "")
        client_mime = (upload.content_type or "").lower()
        if client_mime not in self._ALLOWED_CLIENT_MIMES:
            raise UploadValidationError("uploaded content type does not match a document")

        data = bytearray()
        digest = hashlib.sha256()
        while chunk := await upload.read(1024 * 1024):
            if len(data) + len(chunk) > self.max_bytes:
                raise UploadValidationError(
                    f"document exceeds the {self.max_bytes} byte upload limit"
                )
            data.extend(chunk)
            digest.update(chunk)

        document_id = uuid4().hex
        relative = Path(kb_id) / f"{document_id}{suffix}"
        target = self._safe_path(relative.as_posix())
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(target.write_bytes, bytes(data))
        except Exception:
            if target.exists():
                await asyncio.to_thread(target.unlink)
            raise

        return StoredUpload(
            document_id=document_id,
            filename=filename,
            relative_path=relative.as_posix(),
            mime_type=self._MIME_BY_SUFFIX[suffix],
            size_bytes=len(data),
            sha256=digest.hexdigest(),
        )

    def resolve(self, relative_path: str) -> Path:
        path = self._safe_path(relative_path)
        if not path.is_file():
            raise FileNotFoundError("stored document file is missing")
        return path

    async def delete(self, relative_path: str) -> None:
        path = self._safe_path(relative_path)
        if path.is_file():
            await asyncio.to_thread(path.unlink)
        parent = path.parent
        if parent != self.root and parent.is_dir():
            with suppress(OSError):
                await asyncio.to_thread(parent.rmdir)
