"""D4 Phase 5 secret-provider reference and rotation contracts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.core.secret_providers import (
    DockerSecretProvider,
    EnvironmentSecretProvider,
    ExternalSecretProvider,
    SecretProviderChain,
    SecretProviderError,
    SecretReference,
    SecretResolver,
)
from app.core.security import SecretBox


def test_environment_reference_resolves_without_exposing_reference_value(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    reference = SecretReference.parse("env://OPENAI_API_KEY")

    assert reference.provider == "env"
    assert reference.key == "OPENAI_API_KEY"
    assert EnvironmentSecretProvider().resolve(reference) == "env-secret"


def test_environment_reference_rejects_unsafe_names_and_missing_values() -> None:
    with pytest.raises(SecretProviderError, match="invalid environment"):
        SecretReference.parse("env://bad-name")
    with pytest.raises(SecretProviderError, match="not configured"):
        EnvironmentSecretProvider().resolve(SecretReference.parse("env://MISSING_SECRET"))


def test_docker_secret_provider_reads_bounded_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "api-key"
    secret_file.write_text("docker-secret\n", encoding="utf-8")
    provider = DockerSecretProvider(tmp_path)

    assert provider.resolve(SecretReference.parse("docker://api-key")) == "docker-secret"

    oversized = tmp_path / "oversized"
    oversized.write_text("12345", encoding="utf-8")
    with pytest.raises(SecretProviderError, match="size limit"):
        DockerSecretProvider(tmp_path, max_bytes=4).resolve(
            SecretReference.parse("docker://oversized")
        )


def test_docker_secret_provider_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret"
    outside.write_text("do-not-read", encoding="utf-8")
    provider = DockerSecretProvider(tmp_path)

    with pytest.raises(SecretProviderError, match="within"):
        provider.resolve(SecretReference.parse("docker://../outside-secret"))

    with pytest.raises(SecretProviderError, match="within"):
        SecretReference.parse("docker:///absolute-secret")

    with pytest.raises(SecretProviderError, match="within"):
        SecretReference.parse("docker://C:drive-relative-secret")


def test_external_provider_is_injected_and_reference_is_bounded() -> None:
    calls: list[str] = []

    def resolve_external(key: str) -> str:
        calls.append(key)
        return "external-secret"

    provider = ExternalSecretProvider(resolve_external)

    assert provider.resolve(SecretReference.parse("external://vault/team/api-key")) == (
        "external-secret"
    )
    assert calls == ["vault/team/api-key"]

    with pytest.raises(SecretProviderError, match="too long"):
        SecretReference.parse("external://" + "x" * 502)


def test_chain_rejects_unconfigured_provider_and_accepts_plain_legacy_values() -> None:
    chain = SecretProviderChain((EnvironmentSecretProvider(),))

    assert chain.resolve("plain:legacy-secret") == "legacy-secret"
    with pytest.raises(SecretProviderError, match="not configured"):
        chain.resolve("docker://missing")
    with pytest.raises(SecretProviderError, match="unsupported"):
        chain.resolve("unknown://secret")


def test_secret_reference_never_uses_process_environment_as_a_fallback(monkeypatch) -> None:
    monkeypatch.setenv("MISSING_SECRET", "must-not-be-used")
    chain = SecretProviderChain((EnvironmentSecretProvider(),))

    with pytest.raises(SecretProviderError):
        chain.resolve("external://MISSING_SECRET")

    assert os.environ["MISSING_SECRET"] == "must-not-be-used"


def test_secret_resolver_uses_only_the_explicit_external_adapter() -> None:
    calls: list[str] = []

    def resolve_external(key: str) -> str:
        calls.append(key)
        return "rotated"

    resolver = SecretResolver(
        SecretBox(Fernet.generate_key().decode()),
        SecretProviderChain((ExternalSecretProvider(resolve_external),)),
    )

    stored = resolver.encrypt("external://team/model")
    assert resolver.decrypt(stored) == "rotated"
    assert calls == ["team/model"]


def test_secret_resolver_preserves_empty_mapping_entries() -> None:
    resolver = SecretResolver(SecretBox(Fernet.generate_key().decode()))
    stored = resolver.encrypt_mapping({"OPTIONAL_TOKEN": ""})

    assert resolver.raw_mapping(stored) == {"OPTIONAL_TOKEN": ""}
    assert resolver.decrypt_mapping(stored) == {"OPTIONAL_TOKEN": ""}
