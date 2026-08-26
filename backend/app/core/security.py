"""Fernet encryption and password hashing helpers for AgentCanvas."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings

logger = logging.getLogger(__name__)

_PLAIN_PREFIX = "plain:"

# D3 identity: a stdlib PBKDF2-HMAC-SHA256 scheme so local user passwords never
# travel as plaintext without adding a dependency. ``iterations`` is stored in
# the hash so it can be raised independently of existing rows.
PBKDF2_ALGO = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 210_000
PBKDF2_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Return a self-describing ``pbkdf2_sha256$iter$salt$hash`` string."""
    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{PBKDF2_ALGO}${PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify of a stored password hash, rejecting malformed rows."""
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != PBKDF2_ALGO:
        return False
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
    except ValueError:
        return False
    if iterations < 100_000 or not salt or not expected:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(derived, expected)


class SecretBox:
    """Encrypt/decrypt short strings and JSON objects with a required Fernet key."""

    def __init__(self, secret_key: str) -> None:
        if not secret_key:
            raise RuntimeError("secret encryption key is required")
        try:
            self._fernet = Fernet(secret_key.encode())
        except ValueError as exc:
            raise RuntimeError("SECRET_KEY is not a valid Fernet key") from exc

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, stored: str) -> str:
        if not stored:
            return ""
        if stored.startswith(_PLAIN_PREFIX):
            return stored[len(_PLAIN_PREFIX) :]
        try:
            return self._fernet.decrypt(stored.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("failed to decrypt secret; SECRET_KEY may be incorrect") from exc

    def encrypt_mapping(self, values: dict[str, Any]) -> str:
        if not values:
            return ""
        normalized = {str(key): str(value) for key, value in values.items()}
        return self.encrypt(json.dumps(normalized, ensure_ascii=False, sort_keys=True))

    def decrypt_mapping(self, stored: str | None) -> dict[str, str]:
        if not stored:
            return {}
        try:
            parsed = json.loads(self.decrypt(stored))
        except json.JSONDecodeError as exc:
            raise RuntimeError("stored secret mapping is not valid JSON") from exc
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in parsed.items()
        ):
            raise RuntimeError("stored secret mapping has an invalid shape")
        return dict(parsed)


def _local_key(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return path.read_text(encoding="ascii").strip()

    generated = Fernet.generate_key()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="ascii").strip()
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(generated)
    logger.warning("SECRET_KEY not set; generated local encryption key at %s", path)
    return generated.decode()


def create_secret_box(settings: Settings) -> SecretBox:
    if settings.secret_key:
        return SecretBox(settings.secret_key)
    if settings.is_production:
        raise RuntimeError("production requires a valid SECRET_KEY")
    return SecretBox(_local_key(settings.data_dir / ".agentcanvas.key"))
