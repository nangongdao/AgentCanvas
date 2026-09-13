"""Provider endpoint diagnostics: discover the models an endpoint actually serves.

Adding a model configuration used to require knowing the vendor's exact model id
and typing it by hand, and a wrong key or base URL only surfaced later, inside a
failed execution. This service probes the endpoint's own model-listing API so the
model dialog can offer a picker and a "test connection" affordance.

Every outbound call goes through :func:`app.core.outbound_http.request_public`, so
the SSRF guard runs even when tests inject a transport. The public-IP pin is lifted
only for an explicit ``allow_private_network`` opt-in (an on-prem gateway or a
locally hosted Ollama is why that escape hatch exists), mirroring the HTTP request
node's policy.

The API key only ever travels in a request header. It is never logged, never echoed
in an error message, and never included in the returned payload.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx

from app.core.outbound_http import OutboundDestinationError, request_public

# Providers whose listing API this service knows how to call. ``mock`` is included
# on purpose: it answers without touching the network, so the feature stays
# demoable and testable with no credentials.
PROBE_PROVIDERS: Final[frozenset[str]] = frozenset(
    {"anthropic", "gemini", "ollama", "openai_compat", "mock"}
)

DEFAULT_BASE_URLS: Final[dict[str, str]] = {
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "ollama": "http://localhost:11434",
    "openai_compat": "https://api.openai.com/v1",
}

DEFAULT_TIMEOUT_SECONDS: Final[float] = 15.0
_MAX_ERROR_BODY_CHARS: Final[int] = 240
_EMBEDDING_HINTS: Final[tuple[str, ...]] = ("embed",)

# ``mock`` mirrors the demo provider's canned reply so the dialog flow can be
# exercised end to end without a real vendor.
_MOCK_MODELS: Final[tuple[tuple[str, Literal["chat", "embedding"]], ...]] = (
    ("mock-chat", "chat"),
    ("mock-embed", "embedding"),
)


class ModelDiscoveryError(RuntimeError):
    """Raised when an endpoint cannot be reached or does not answer in kind."""


@dataclass(frozen=True)
class DiscoveredModel:
    """One model id offered by the endpoint."""

    id: str
    kind: Literal["chat", "embedding"] = "chat"
    owned_by: str | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    """The outcome of a successful probe."""

    provider: str
    base_url: str
    models: tuple[DiscoveredModel, ...]
    latency_ms: int


def supported_providers() -> tuple[str, ...]:
    """Providers this service can probe, sorted for stable presentation."""
    return tuple(sorted(PROBE_PROVIDERS))


def _infer_kind(model_id: str, *, embedding_signal: bool = False) -> Literal["chat", "embedding"]:
    """Classify a model id as ``chat`` or ``embedding``.

    Vendor listing APIs rarely state the modality, so this is a naming heuristic.
    Callers surface it as a default the operator can override, never as a fact.
    """
    if embedding_signal:
        return "embedding"
    lowered = model_id.casefold()
    return "embedding" if any(hint in lowered for hint in _EMBEDDING_HINTS) else "chat"


def _join(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _resolve_base_url(provider: str, base_url: str | None) -> str:
    candidate = (base_url or "").strip() or DEFAULT_BASE_URLS.get(provider, "")
    if not candidate:
        raise ModelDiscoveryError(f"provider '{provider}' requires an explicit base_url")
    if not candidate.startswith(("http://", "https://")):
        raise ModelDiscoveryError("base_url must be an absolute http(s) URL")
    return candidate


def _snippet(response: httpx.Response, secret: str = "") -> str:
    """A short, collapsed excerpt of an error body for the operator.

    Some vendors echo the submitted credential back inside the error message, so
    any occurrence of ``secret`` is redacted before the text is returned.
    """
    try:
        text = response.text
    except Exception:  # noqa: BLE001 - a broken body must not mask the status
        return ""
    collapsed = " ".join(text.split())
    if secret and secret in collapsed:
        collapsed = collapsed.replace(secret, "***")
    if len(collapsed) > _MAX_ERROR_BODY_CHARS:
        collapsed = f"{collapsed[:_MAX_ERROR_BODY_CHARS]}…"
    return collapsed


def _json_payload(response: httpx.Response, provider: str, secret: str = "") -> Any:
    if not 200 <= response.status_code < 300:
        detail = _snippet(response, secret)
        suffix = f": {detail}" if detail else ""
        raise ModelDiscoveryError(
            f"{provider} endpoint returned HTTP {response.status_code}{suffix}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ModelDiscoveryError(f"{provider} endpoint did not return JSON") from exc


def _rows(payload: Any, key: str, provider: str) -> list[Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise ModelDiscoveryError(f"{provider} endpoint response has no '{key}' list")
    return list(payload[key])


def _parse_openai(payload: Any) -> list[DiscoveredModel]:
    models: list[DiscoveredModel] = []
    for entry in _rows(payload, "data", "openai_compat"):
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        model_id = str(entry["id"])
        owner = entry.get("owned_by")
        models.append(
            DiscoveredModel(
                id=model_id,
                kind=_infer_kind(model_id),
                owned_by=str(owner) if owner else None,
            )
        )
    return models


def _parse_anthropic(payload: Any) -> list[DiscoveredModel]:
    models: list[DiscoveredModel] = []
    for entry in _rows(payload, "data", "anthropic"):
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        model_id = str(entry["id"])
        display = entry.get("display_name")
        models.append(
            DiscoveredModel(
                id=model_id,
                kind=_infer_kind(model_id),
                owned_by=str(display) if display else None,
            )
        )
    return models


def _parse_gemini(payload: Any) -> list[DiscoveredModel]:
    models: list[DiscoveredModel] = []
    for entry in _rows(payload, "models", "gemini"):
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        # Gemini names models as "models/gemini-2.5-flash"; the config field wants
        # the bare id.
        model_id = str(entry["name"]).removeprefix("models/")
        methods = entry.get("supportedGenerationMethods")
        embedding_signal = isinstance(methods, list) and any(
            "embed" in str(method).casefold() for method in methods
        )
        models.append(
            DiscoveredModel(
                id=model_id,
                kind=_infer_kind(model_id, embedding_signal=embedding_signal),
                owned_by=str(entry.get("displayName") or "") or None,
            )
        )
    return models


def _parse_ollama(payload: Any) -> list[DiscoveredModel]:
    models: list[DiscoveredModel] = []
    for entry in _rows(payload, "models", "ollama"):
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        model_id = str(entry["name"])
        models.append(
            DiscoveredModel(
                id=model_id,
                kind=_infer_kind(model_id),
                owned_by=str(entry.get("details", {}).get("family") or "") or None,
            )
        )
    return models


def _request_spec(
    provider: str, base_url: str, api_key: str
) -> tuple[str, dict[str, str], Callable[[Any], list[DiscoveredModel]]]:
    """Return ``(url, headers, parser)`` for one provider's listing API."""
    headers: dict[str, str] = {"accept": "application/json"}
    if provider == "openai_compat":
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        return _join(base_url, "models"), headers, _parse_openai
    if provider == "anthropic":
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        return _join(base_url, "v1/models"), headers, _parse_anthropic
    if provider == "gemini":
        if api_key:
            headers["x-goog-api-key"] = api_key
        return _join(base_url, "v1beta/models"), headers, _parse_gemini
    if provider == "ollama":
        return _join(base_url, "api/tags"), headers, _parse_ollama
    raise ModelDiscoveryError(f"provider '{provider}' cannot be probed")


async def discover_models(
    provider: str,
    *,
    base_url: str | None = None,
    api_key: str = "",
    allow_private_network: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> DiscoveryResult:
    """List the models ``provider`` serves at ``base_url``.

    Raises :class:`ModelDiscoveryError` when the provider is not probeable, the
    destination is not a permitted public target, or the endpoint answers with an
    error or an unexpected shape.
    """
    if provider == "mock":
        # The demo provider has no listing endpoint; it answers from a canned
        # catalog so the dialog flow can be exercised without credentials.
        resolved = (base_url or "").strip() or "mock://local"
        return DiscoveryResult(
            provider=provider,
            base_url=resolved,
            models=tuple(
                DiscoveredModel(id=model_id, kind=kind) for model_id, kind in _MOCK_MODELS
            ),
            latency_ms=0,
        )

    if provider not in PROBE_PROVIDERS:
        raise ModelDiscoveryError(
            f"provider '{provider}' does not support model discovery; "
            f"supported: {', '.join(supported_providers())}"
        )

    resolved_base = _resolve_base_url(provider, base_url)
    url, headers, parser = _request_spec(provider, resolved_base, api_key)

    started = time.perf_counter()
    try:
        if allow_private_network:
            # Explicit opt-in for on-prem/local targets: the public-IP pin is
            # lifted, but redirects, proxies, and Unix sockets stay off.
            async with httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.get(url, headers=headers, timeout=timeout_seconds)
        else:
            response = await request_public(
                "GET",
                url,
                headers=headers,
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
    except OutboundDestinationError as exc:
        raise ModelDiscoveryError(
            f"base_url is not a reachable public endpoint ({exc}). "
            "Enable 'allow private network' for an on-prem or local target."
        ) from exc
    except httpx.TimeoutException as exc:
        raise ModelDiscoveryError(
            f"{provider} endpoint timed out after {timeout_seconds:g}s"
        ) from exc
    except httpx.HTTPError as exc:
        raise ModelDiscoveryError(
            f"{provider} endpoint could not be reached ({type(exc).__name__})"
        ) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    payload = _json_payload(response, provider, api_key)
    models = tuple(sorted(parser(payload), key=lambda model: model.id))
    return DiscoveryResult(
        provider=provider,
        base_url=resolved_base,
        models=models,
        latency_ms=latency_ms,
    )


__all__ = [
    "DEFAULT_BASE_URLS",
    "DEFAULT_TIMEOUT_SECONDS",
    "PROBE_PROVIDERS",
    "DiscoveredModel",
    "DiscoveryResult",
    "ModelDiscoveryError",
    "discover_models",
    "supported_providers",
]
