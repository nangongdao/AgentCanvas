"""Safe outbound HTTP transport with SSRF protection.

Shared by the durable workflow callback dispatcher (C1-4) and the HTTP Request
node (C2-3). Every outbound request is pinned to a pre-validated public
destination: hostnames are resolved before the connection, only global
addresses are accepted, and the underlying transport is locked to those IPs
so a DNS rebinding or HTTP redirect cannot reach an internal network.

The transport mirrors the guarantees enforced by
``WorkflowCallbackDispatcher._post_callback``: no environment-driven proxies
(``trust_env=False``), no automatic redirects (``follow_redirects=False``),
no Unix sockets, and a TLS context that trusts neither the environment nor
the host CA store beyond the default verification chain.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx

__all__ = [
    "OutboundDestinationError",
    "ResolvedDestination",
    "build_pinned_client",
    "resolve_public_destination",
]


SocketOption = (
    tuple[int, int, int]
    | tuple[int, int, bytes | bytearray]
    | tuple[int, int, None, int]
)


class OutboundDestinationError(OSError):
    """Raised when an outbound URL cannot be validated as a public target."""


class ResolvedDestination:
    """A hostname and the global IP addresses it resolved to."""

    __slots__ = ("host", "addresses")

    def __init__(self, host: str, addresses: tuple[str, ...]) -> None:
        if not addresses:
            raise OutboundDestinationError("at least one destination address is required")
        self.host = host
        self.addresses = addresses


def _normalized_host(host: str) -> str:
    return host.rstrip(".").casefold()


def resolve_public_destination(url: str) -> ResolvedDestination:
    """Resolve ``url`` and verify every address is a public IP.

    Raises ``OutboundDestinationError`` for non-HTTP schemes, missing or
    non-ASCII hostnames, resolution failures, or any resolved address that is
    not globally routable (loopback, private, link-local, etc.).
    """
    parsed = httpx.URL(url)
    if parsed.scheme not in {"http", "https"}:
        raise OutboundDestinationError("outbound URL must use HTTP or HTTPS")
    try:
        host = parsed.raw_host.decode("ascii")
    except (UnicodeDecodeError, AttributeError) as exc:
        raise OutboundDestinationError("outbound URL hostname is invalid") from exc
    if not host:
        raise OutboundDestinationError("outbound URL has no hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OutboundDestinationError("outbound hostname did not resolve") from exc
    if not resolved:
        raise OutboundDestinationError("outbound hostname did not resolve")
    addresses: list[str] = []
    for _family, _type, _proto, _canonname, sockaddr in resolved:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise OutboundDestinationError("outbound hostname resolved to a non-IP address") from exc
        if not address.is_global:
            raise OutboundDestinationError("outbound hostname resolved to a non-public address")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    return ResolvedDestination(host, tuple(addresses))


async def _resolve_public_destination_async(url: str) -> ResolvedDestination:
    return await asyncio.to_thread(resolve_public_destination, url)


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect a validated hostname through only its resolved IPs."""

    def __init__(
        self,
        destination: ResolvedDestination,
        *,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._hostname = _normalized_host(destination.host)
        self._addresses = destination.addresses
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if _normalized_host(host) != self._hostname:
            raise OSError("outbound transport attempted an unexpected hostname")
        last_error: Exception | None = None
        for address in self._addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (OSError, httpcore.NetworkError, httpcore.TimeoutException) as exc:
                last_error = exc
        raise OSError("failed to connect to validated outbound destination") from last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise OSError("outbound transport does not permit Unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(
        self,
        destination: ResolvedDestination | None = None,
        *,
        backend: _PinnedNetworkBackend | None = None,
    ) -> None:
        # HTTPX does not expose httpcore's network backend constructor argument.
        # Its locked transport contract delegates exclusively through this pool.
        if backend is not None:
            network_backend = backend
        elif destination is not None:
            network_backend = _PinnedNetworkBackend(destination)
        else:
            raise ValueError("either destination or backend must be provided")
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            max_connections=1,
            max_keepalive_connections=0,
            network_backend=network_backend,
        )


def build_pinned_client(destination: ResolvedDestination) -> httpx.AsyncClient:
    """Return a short-lived client locked to a pre-validated destination."""
    transport = _PinnedAsyncHTTPTransport(destination)
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    )


async def request_public(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    content: bytes | str | None = None,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.Response:
    """Perform an SSRF-safe outbound HTTP request.

    When ``transport`` is supplied (used by tests to inject a mock), the
    destination is still resolved first so the SSRF guard runs regardless of
    the transport. The pinned transport is only constructed for real network
    calls so each request validates its own destination — callers that share
    a client (the callback dispatcher) bypass this helper and pin once.
    """
    destination = await _resolve_public_destination_async(url)
    if transport is not None:
        async with httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return await client.request(
                method,
                url,
                headers=headers,
                content=content,
                timeout=timeout_seconds,
            )
    async with build_pinned_client(destination) as client:
        return await client.request(
            method,
            url,
            headers=headers,
            content=content,
            timeout=timeout_seconds,
        )


def is_http_failure_status(status_code: int, expected_status_range: tuple[int, int]) -> bool:
    """Return True when ``status_code`` falls outside the expected 2xx-ish band."""
    low, high = expected_status_range
    return not (low <= status_code <= high)


def _coerce_content(body: Any) -> bytes | str | None:
    """Normalize a request body into an httpx-compatible content value."""
    if body is None:
        return None
    if isinstance(body, (bytes, str)):
        return body
    import json

    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
