"""Bounded secret references and runtime secret-provider adapters.

Stored credentials remain encrypted by :class:`SecretBox`.  The decrypted
payload may either be a legacy literal secret or a reference such as
``env://OPENAI_API_KEY``.  References are resolved only at the runtime
boundary, so rotating an external secret never copies its value into the
database.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.security import SecretBox

MAX_REFERENCE_LENGTH = 512
MAX_SECRET_VALUE_BYTES = 64 * 1024
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_REFERENCE_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]{1,15}$")


class SecretProviderError(RuntimeError):
    """Raised when a secret reference is malformed or cannot be resolved."""


@dataclass(frozen=True)
class SecretReference:
    """A validated, non-secret provider URI."""

    provider: str
    key: str

    @classmethod
    def parse(cls, value: str) -> SecretReference:
        if not isinstance(value, str) or not value:
            raise SecretProviderError("secret reference must be a nonblank string")
        if len(value) > MAX_REFERENCE_LENGTH:
            raise SecretProviderError("secret reference is too long")
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
            raise SecretProviderError("secret reference contains control characters")
        provider, separator, key = value.partition("://")
        if not separator or not _REFERENCE_SCHEME.fullmatch(provider) or not key:
            raise SecretProviderError("secret reference must use provider://key syntax")
        if provider == "env":
            if not _ENV_NAME.fullmatch(key):
                raise SecretProviderError("invalid environment secret name")
        elif provider == "docker":
            _validate_docker_key(key)
        elif provider == "external":
            if (
                key.startswith("/")
                or "\\" in key
                or not key.strip()
                or any(part in {"", ".", ".."} for part in key.split("/"))
            ):
                raise SecretProviderError("invalid external secret key")
        else:
            raise SecretProviderError(f"unsupported secret provider '{provider}'")
        return cls(provider=provider, key=key)

    @property
    def uri(self) -> str:
        return f"{self.provider}://{self.key}"


class SecretProvider(Protocol):
    name: str

    def resolve(self, reference: SecretReference) -> str: ...


def _validate_docker_key(key: str) -> None:
    path = Path(key)
    if (
        key.startswith("/")
        or
        path.is_absolute()
        or "\\" in key
        or ":" in key
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SecretProviderError("docker secret path must stay within the configured root")
    if len(path.parts) > 8:
        raise SecretProviderError("docker secret path is too deep")


def _bounded_secret(value: str, *, source: str) -> str:
    if not isinstance(value, str) or not value:
        raise SecretProviderError(f"{source} secret is empty")
    if len(value.encode("utf-8")) > MAX_SECRET_VALUE_BYTES:
        raise SecretProviderError(f"{source} secret exceeds the size limit")
    return value


class EnvironmentSecretProvider:
    name = "env"

    def resolve(self, reference: SecretReference) -> str:
        if reference.provider != self.name:
            raise SecretProviderError("environment provider received a different reference")
        value = os.environ.get(reference.key)
        if value is None:
            raise SecretProviderError(f"environment secret '{reference.key}' is not configured")
        return _bounded_secret(value, source="environment")


class DockerSecretProvider:
    name = "docker"

    def __init__(self, root: Path, *, max_bytes: int = MAX_SECRET_VALUE_BYTES) -> None:
        self.root = Path(root).expanduser()
        self.max_bytes = max_bytes
        if self.max_bytes < 1:
            raise ValueError("docker secret max_bytes must be positive")

    def resolve(self, reference: SecretReference) -> str:
        if reference.provider != self.name:
            raise SecretProviderError("Docker provider received a different reference")
        _validate_docker_key(reference.key)
        root = self.root.resolve()
        candidate = (root / Path(reference.key)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise SecretProviderError(
                "docker secret path must stay within the configured root"
            ) from exc
        try:
            size = candidate.stat().st_size
            if size > self.max_bytes:
                raise SecretProviderError("docker secret exceeds the size limit")
            with candidate.open("rb") as handle:
                payload = handle.read(self.max_bytes + 1)
            if len(payload) > self.max_bytes:
                raise SecretProviderError("docker secret exceeds the size limit")
            value = payload.decode("utf-8").rstrip("\r\n")
        except FileNotFoundError as exc:
            raise SecretProviderError(
                f"docker secret '{reference.key}' is not configured"
            ) from exc
        except UnicodeDecodeError as exc:
            raise SecretProviderError("docker secret is not valid UTF-8") from exc
        except OSError as exc:
            raise SecretProviderError("docker secret could not be read") from exc
        return _bounded_secret(value, source="Docker")


class ExternalSecretProvider:
    """Adapter seam for a cloud/external secret service.

    The application does not perform arbitrary network lookups. Deployments
    inject a resolver with their provider SDK, while the same bounded URI and
    audit behavior is shared by every caller.
    """

    name = "external"

    def __init__(self, resolver: Callable[[str], str] | None = None) -> None:
        self._resolver = resolver

    def resolve(self, reference: SecretReference) -> str:
        if reference.provider != self.name:
            raise SecretProviderError("external provider received a different reference")
        if self._resolver is None:
            raise SecretProviderError("external secret provider is not configured")
        try:
            value = self._resolver(reference.key)
        except SecretProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            raise SecretProviderError("external secret provider failed") from exc
        return _bounded_secret(value, source="external")


class SecretProviderChain:
    """Resolve references through a fixed, explicitly configured provider set."""

    def __init__(self, providers: Iterable[SecretProvider]) -> None:
        self._providers = {provider.name: provider for provider in providers}

    def validate(self, value: str) -> str:
        """Validate a literal or reference without resolving external state."""
        if not isinstance(value, str):
            raise SecretProviderError("secret value must be a string")
        # Empty mapping entries were accepted by the legacy SecretBox path and
        # are useful for optional process environment variables. They are not
        # provider references and remain empty at runtime.
        if not value:
            return ""
        if value.startswith("plain:"):
            return value[len("plain:") :]
        if "://" in value:
            return SecretReference.parse(value).uri
        if len(value.encode("utf-8")) > MAX_SECRET_VALUE_BYTES:
            raise SecretProviderError("secret exceeds the size limit")
        return value

    def resolve(self, value: str) -> str:
        validated = self.validate(value)
        if validated.startswith("plain:"):
            return validated[len("plain:") :]
        if "://" not in validated:
            return validated
        reference = SecretReference.parse(validated)
        provider = self._providers.get(reference.provider)
        if provider is None:
            raise SecretProviderError(f"secret provider '{reference.provider}' is not configured")
        return provider.resolve(reference)

    def resolve_mapping(self, values: dict[str, str]) -> dict[str, str]:
        return {key: self.resolve(value) for key, value in values.items()}

    def validate_mapping(self, values: dict[str, str]) -> dict[str, str]:
        return {key: self.validate(value) for key, value in values.items()}

    def source(self, value: str) -> str:
        """Return a safe source label for API presentation and audit UI."""
        if not value:
            return "none"
        if "://" not in value:
            return "stored"
        try:
            return SecretReference.parse(value).provider
        except SecretProviderError:
            return "stored"


class SecretResolver:
    """Combine encrypted persistence with reference resolution at runtime."""

    def __init__(self, secret_box: SecretBox, providers: SecretProviderChain | None = None) -> None:
        self.secret_box = secret_box
        self.providers = providers or SecretProviderChain(())

    def encrypt(self, value: str) -> str:
        return self.secret_box.encrypt(self.providers.validate(value)) if value else ""

    def encrypt_mapping(self, values: dict[str, str]) -> str:
        return self.secret_box.encrypt_mapping(self.providers.validate_mapping(values))

    def encrypt_reference(self, value: str) -> str:
        reference = SecretReference.parse(value)
        return self.secret_box.encrypt(reference.uri)

    def decrypt(self, stored: str | None) -> str:
        if not stored:
            return ""
        return self.providers.resolve(self.secret_box.decrypt(stored))

    def decrypt_mapping(self, stored: str | None) -> dict[str, str]:
        values = self.secret_box.decrypt_mapping(stored)
        return self.providers.resolve_mapping(values)

    def raw_mapping(self, stored: str | None) -> dict[str, str]:
        return self.secret_box.decrypt_mapping(stored)

    def validate_stored(self, stored: str | None) -> str:
        return self.providers.validate(self.secret_box.decrypt(stored or "")) if stored else ""

    def validate_stored_mapping(self, stored: str | None) -> dict[str, str]:
        return self.providers.validate_mapping(self.secret_box.decrypt_mapping(stored))

    def source(self, stored: str | None) -> str:
        if not stored:
            return "none"
        return self.providers.source(self.secret_box.decrypt(stored))

    def source_mapping(self, stored: str | None) -> dict[str, str]:
        return self.source_values(self.secret_box.decrypt_mapping(stored))

    def source_values(self, values: dict[str, str]) -> dict[str, str]:
        return {key: self.providers.source(value) for key, value in values.items()}

    def masked_mapping(self, stored: str | None) -> tuple[dict[str, str], dict[str, str]]:
        values = self.secret_box.decrypt_mapping(stored)
        return (
            {key: "********" for key in values},
            self.source_values(values),
        )


def build_secret_provider_chain(
    docker_root: Path,
    *,
    external_resolver: Callable[[str], str] | None = None,
    max_bytes: int = MAX_SECRET_VALUE_BYTES,
) -> SecretProviderChain:
    return SecretProviderChain(
        (
            EnvironmentSecretProvider(),
            DockerSecretProvider(docker_root, max_bytes=max_bytes),
            ExternalSecretProvider(external_resolver),
        )
    )


__all__ = [
    "DockerSecretProvider",
    "EnvironmentSecretProvider",
    "ExternalSecretProvider",
    "SecretProviderChain",
    "SecretProviderError",
    "SecretReference",
    "SecretResolver",
    "build_secret_provider_chain",
]
