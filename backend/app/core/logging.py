"""Structured JSON logging, correlation context, and central redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
execution_id_var: ContextVar[str] = ContextVar("execution_id", default="-")
workflow_id_var: ContextVar[str] = ContextVar("workflow_id", default="-")
node_id_var: ContextVar[str] = ContextVar("node_id", default="-")
provider_var: ContextVar[str] = ContextVar("provider", default="-")
error_code_var: ContextVar[str] = ContextVar("error_code", default="-")

_CONTEXT_VARS: dict[str, ContextVar[str]] = {
    "request_id": request_id_var,
    "execution_id": execution_id_var,
    "workflow_id": workflow_id_var,
    "node_id": node_id_var,
    "provider": provider_var,
    "error_code": error_code_var,
}
_RECORD_FIELDS = (
    "method",
    "route",
    "status_code",
    "duration_ms",
    "operation",
    "outcome",
)
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|password|secret|token|credential)", re.I
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@", re.I)
_QUERY_SECRET = re.compile(
    r"(?i)(?P<key>(?:api[_-]?key|password|secret|token))=(?P<value>[^&\s]+)"
)


def redact_value(value: Any, *, key: str = "") -> Any:
    """Recursively mask known secret keys and credential-shaped strings."""
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): redact_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if not isinstance(value, str):
        return value
    redacted = _BEARER.sub("Bearer [REDACTED]", value)
    redacted = _URL_CREDENTIALS.sub(r"\g<scheme>[REDACTED]@", redacted)
    return _QUERY_SECRET.sub(r"\g<key>=[REDACTED]", redacted)


@contextmanager
def bind_log_context(**fields: str | None) -> Iterator[None]:
    """Bind correlation fields for the current async context and restore them."""
    tokens: list[tuple[ContextVar[str], Token[str]]] = []
    try:
        for name, value in fields.items():
            variable = _CONTEXT_VARS.get(name)
            if variable is not None and value:
                tokens.append((variable, variable.set(value)))
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


class ContextFilter(logging.Filter):
    """Inject stable correlation fields into every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name, variable in _CONTEXT_VARS.items():
            setattr(record, name, variable.get())
        return True


class JsonFormatter(logging.Formatter):
    """Emit one redacted JSON object per line for log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_value(record.getMessage()),
        }
        for name in _CONTEXT_VARS:
            value = getattr(record, name, "-")
            if value and value != "-":
                payload[name] = redact_value(value, key=name)
        for name in _RECORD_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = redact_value(value, key=name)
        if record.exc_info:
            payload["exception"] = redact_value(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def setup_logging(level: str = "INFO", *, json_logs: bool = True) -> None:
    """Configure one owned root handler while preserving test/capture handlers."""
    root = logging.getLogger()
    root.setLevel(level)
    owned = next(
        (handler for handler in root.handlers if getattr(handler, "_agentcanvas", False)),
        None,
    )
    if owned is None:
        owned = logging.StreamHandler(sys.stdout)
        owned.__dict__["_agentcanvas"] = True
        owned.addFilter(ContextFilter())
        root.addHandler(owned)
    if json_logs:
        owned.setFormatter(JsonFormatter())
    else:
        owned.setFormatter(
            logging.Formatter(
                fmt=(
                    "%(asctime)s | %(levelname)-7s | %(name)s | "
                    "req=%(request_id)s exec=%(execution_id)s | %(message)s"
                ),
                datefmt="%H:%M:%S",
            )
        )

    for noisy in ("httpx", "httpcore", "chromadb", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
