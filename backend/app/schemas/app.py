"""Application entity schemas (C3-1, C3-3 embed config)."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator


class AppType(StrEnum):
    CHATBOT = "chatbot"
    COMPLETION = "completion"
    API = "api"


class AppVisibility(StrEnum):
    PROJECT = "project"
    LINK = "link"
    PUBLIC = "public"


class AppStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class InputFieldDef(BaseModel):
    """Snapshot of a single input field, derived from the bound version DSL."""

    name: str = Field(min_length=1, max_length=64)
    type: str
    required: bool = False
    default: Any = None


# --- C3-3 embed/theme config validation ---

_THEME_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MAX_EMBED_ORIGINS = 32
# Public alias so callers can import the bound without the private name.
MAX_EMBED_ORIGINS = _MAX_EMBED_ORIGINS


def normalize_theme_color(value: str | None) -> str | None:
    """Validate and normalize a ``#rrggbb`` theme color.

    Returns ``None`` when empty/absent. Raises ``ValueError`` for malformed
    values so Pydantic surfaces them as 422 to the caller.
    """
    if value is None or value == "":
        return None
    if not _THEME_RE.match(value):
        raise ValueError("theme_color must be a #rrggbb hex color")
    return value.lower()


def normalize_embed_origins(
    value: list[str] | None,
) -> list[str] | None:
    """Validate an embed allow-list of origin strings.

    Each entry must be a bare origin (``scheme://host[:port]``) with no path,
    query, or fragment. ``null``/empty means embedding is disabled
    (``frame-ancestors 'none'``). Returns a deduplicated, sorted list.
    """
    if value is None:
        return None
    if len(value) > _MAX_EMBED_ORIGINS:
        raise ValueError(f"embed_allowed_origins must have at most {_MAX_EMBED_ORIGINS} entries")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("embed_allowed_origins entries must be strings")
        origin = raw.strip()
        if not origin:
            continue
        parsed = urlsplit(origin)
        if parsed.scheme not in ("https", "http"):
            raise ValueError(f"embed origin '{origin}' must use http or https scheme")
        if not parsed.hostname:
            raise ValueError(f"embed origin '{origin}' is missing a host")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError(f"embed origin '{origin}' must not contain a path, query, or fragment")
        # Normalize trailing slash on bare origins.
        normalized = f"{parsed.scheme}://{parsed.netloc}"
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


class AppCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=32)
    workflow_id: str | None = Field(default=None, min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    icon: str | None = Field(default=None, max_length=500)
    type: AppType = AppType.CHATBOT
    welcome_message: str | None = Field(default=None, max_length=2000)
    suggested_questions: list[str] = Field(default_factory=list, max_length=10)
    visibility: AppVisibility = AppVisibility.PROJECT
    # C3-3: optional brand theme color (#rrggbb) and embed allow-list.
    theme_color: str | None = Field(default=None, max_length=9)
    embed_allowed_origins: list[str] | None = Field(default=None, max_length=_MAX_EMBED_ORIGINS)

    @model_validator(mode="after")
    def _normalize_questions(self) -> AppCreate:
        cleaned: list[str] = []
        seen: set[str] = set()
        for question in self.suggested_questions:
            trimmed = question.strip()
            if not trimmed or trimmed in seen:
                continue
            if len(trimmed) > 200:
                raise ValueError("suggested question must be at most 200 characters")
            seen.add(trimmed)
            cleaned.append(trimmed)
        # Mutate via object.__setattr__ to stay compatible with frozen=False.
        object.__setattr__(self, "suggested_questions", cleaned)
        return self

    @model_validator(mode="after")
    def _normalize_embed_config(self) -> AppCreate:
        object.__setattr__(self, "theme_color", normalize_theme_color(self.theme_color))
        object.__setattr__(
            self, "embed_allowed_origins", normalize_embed_origins(self.embed_allowed_origins)
        )
        return self


class AppUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    icon: str | None = Field(default=None, max_length=500)
    type: AppType | None = None
    welcome_message: str | None = Field(default=None, max_length=2000)
    suggested_questions: list[str] | None = Field(default=None, max_length=10)
    visibility: AppVisibility | None = None
    status: AppStatus | None = None
    # C3-3: optional brand theme color and embed allow-list.
    theme_color: str | None = Field(default=None, max_length=9)
    embed_allowed_origins: list[str] | None = Field(default=None, max_length=_MAX_EMBED_ORIGINS)

    @model_validator(mode="after")
    def _normalize_questions(self) -> AppUpdate:
        if self.suggested_questions is None:
            return self
        cleaned: list[str] = []
        seen: set[str] = set()
        for question in self.suggested_questions:
            trimmed = question.strip()
            if not trimmed or trimmed in seen:
                continue
            if len(trimmed) > 200:
                raise ValueError("suggested question must be at most 200 characters")
            seen.add(trimmed)
            cleaned.append(trimmed)
        object.__setattr__(self, "suggested_questions", cleaned)
        return self

    @model_validator(mode="after")
    def _normalize_embed_config(self) -> AppUpdate:
        # Only validate when explicitly provided (not None). An empty list
        # is a valid value that disables embedding.
        if self.theme_color is not None:
            object.__setattr__(self, "theme_color", normalize_theme_color(self.theme_color))
        if self.embed_allowed_origins is not None:
            object.__setattr__(
                self,
                "embed_allowed_origins",
                normalize_embed_origins(self.embed_allowed_origins),
            )
        return self


class AppVersionSwitch(BaseModel):
    version_id: str = Field(min_length=1, max_length=32)


class AppOut(BaseModel):
    id: str
    project_id: str
    workflow_id: str | None
    published_version_id: str | None
    published_version_number: int | None
    name: str
    icon: str | None
    type: AppType
    welcome_message: str | None
    suggested_questions: list[str]
    input_form: list[InputFieldDef]
    visibility: AppVisibility
    status: AppStatus
    has_public_access: bool
    token_prefix: str | None
    slug: str
    # C3-3: embed config surfaced to platform editors.
    theme_color: str | None
    embed_allowed_origins: list[str] | None
    created_at: datetime
    updated_at: datetime


class AppIssueOut(BaseModel):
    """Returned on create/rotate when a public token is (re)issued."""

    app: AppOut
    public_url: str | None
    token: str | None


class AppRuntimeOut(BaseModel):
    """Public runtime descriptor for an app (no token, no internal ids leaked)."""

    slug: str
    name: str
    icon: str | None
    type: AppType
    welcome_message: str | None
    suggested_questions: list[str]
    input_form: list[InputFieldDef]
    visibility: AppVisibility
    status: AppStatus
    requires_token: bool
    # C3-3: theme color and embed flag for the standalone runtime page.
    theme_color: str | None
    embed_enabled: bool


class AppRuntimeSessionCreate(BaseModel):
    """Start a runtime conversation; inputs must match the bound version's form."""

    inputs: dict[str, Any] = Field(default_factory=dict)
    title: str = Field(default="", max_length=200)


class AppRuntimeSend(BaseModel):
    """A user turn in a runtime conversation (public/link surface)."""

    message: str = Field(min_length=1, max_length=20_000)
    inputs: dict[str, Any] = Field(default_factory=dict)


class AppUsageDayOut(BaseModel):
    """One UTC day of application activity (C3-5)."""

    date: str
    sessions: int
    messages: int
    executions: int
    total_tokens: int
    estimated_cost_usd: str | None


class AppUsageOut(BaseModel):
    """Per-application usage aggregates over a trailing window (C3-5).

    Token/cost figures reuse the D2 cost estimator over the app sessions'
    executions. ``estimated_cost_usd`` is None (and ``cost_known`` False) when
    provider usage or model pricing is missing — never a silent zero.
    ``feedback_rate`` is None until at least one rating exists.
    """

    app_id: str
    days: int
    since: str
    sessions: int
    user_messages: int
    assistant_messages: int
    executions: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: str | None
    cost_known: bool
    positive_feedback: int
    negative_feedback: int
    feedback_rate: float | None
    available_citations: int
    referenced_citations: int
    citation_coverage: float | None
    daily: list[AppUsageDayOut]


__all__ = [
    "AppCreate",
    "AppIssueOut",
    "AppRuntimeOut",
    "AppRuntimeSend",
    "AppRuntimeSessionCreate",
    "AppStatus",
    "AppType",
    "AppUsageDayOut",
    "AppUsageOut",
    "AppVersionSwitch",
    "AppVisibility",
    "AppUpdate",
    "InputFieldDef",
    "AppOut",
    "MAX_EMBED_ORIGINS",
    "normalize_embed_origins",
    "normalize_theme_color",
]
