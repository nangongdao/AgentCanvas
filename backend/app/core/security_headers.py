"""Site-wide defensive security headers (C8-3).

A small pure-ASGI middleware that stamps every HTTP response with hardened
headers regardless of which layer produced it (route, CORS rejection,
rate-limit 429, observability, static file, etc.). This is the front-line
baseline; per-app frame-ancestors CSP on the public runtime is layered on top
by ``app_runtime._harden`` for embed control.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# HSTS is only meaningful over HTTPS and must be opt-in via settings; the rest
# apply everywhere. Clickjacking is blocked by X-Frame-Options: DENY for the
# whole API surface; per-app frame-ancestors CSP is layered on by
# app_runtime._harden for the embeddable public runtime (C3-3), so the default
# CSP deliberately omits frame-ancestors to avoid a second policy directive
# that would intersect with the app allow-list to nothing.
_DEFAULT_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'",
}


class SecurityHeadersMiddleware:
    """Stamp hardened security headers on every HTTP response."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        strict_transport_security: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.app = app
        self._headers = dict(_DEFAULT_HEADERS)
        if strict_transport_security:
            self._headers["Strict-Transport-Security"] = strict_transport_security
        if extra_headers:
            self._headers.update(extra_headers)
        # 404 responses for the SPA fallback come from the app too; nothing
        # special is needed because the middleware wraps the whole tree.
        # Header names are normalized to lowercase so the existence check in
        # __call__ matches original-case headers set by routes ("Referrer-Policy"
        # vs "referrer-policy").
        self._header_tuples = [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in self._headers.items()
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _ in headers}
                for name, value in self._header_tuples:
                    if name not in existing:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = ["SecurityHeadersMiddleware"]
