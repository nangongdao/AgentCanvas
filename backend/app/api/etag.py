"""Shared strong-ETag helper for high-frequency GET list endpoints (C6-4).

The platform's list surfaces are polled repeatedly by the frontend while the
underlying data changes rarely. Hashing the serialized body gives a weak
validator that lets a client with ``If-None-Match`` receive an empty 304
instead of the full payload. Responses depend on the caller's cookie session
(authorization shapes the rows), so they always carry ``Vary: Cookie``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Request, Response

_JSON_SEPARATORS = (",", ":")


def etag_json_response(
    request: Request,
    payload: Any,
    *,
    status_code: int = 200,
) -> Response:
    """Serialize ``payload`` deterministically and serve it with a weak ETag."""
    body = json.dumps(
        payload, ensure_ascii=False, separators=_JSON_SEPARATORS
    ).encode("utf-8")
    etag = f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'
    headers = {"ETag": etag, "Vary": "Cookie"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        content=body,
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )


__all__ = ["etag_json_response"]
