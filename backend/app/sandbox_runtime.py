"""Runtime adapter mapping ``Settings`` to a ``SandboxBackend`` selection.

Kept as a thin module (not a method on Settings) so health/meta routes and the
container share one canonical resolution path, and so the sandbox module itself
stays free of Settings coupling for unit testing.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.sandbox import SandboxBackend, select_sandbox


def select_runtime_sandbox(settings: Settings) -> SandboxBackend:
    """Resolve the active sandbox backend from application settings.

    Production never lands here with ``none`` — ``validate_runtime_settings``
    rejects it — so the only way ``NoSandbox`` is returned is in test/dev when
    the operator explicitly opts out. The enforcement warning emitted by
    ``select_sandbox`` surfaces a degraded backend in logs; readiness exposes
    the same fact via ``ServiceContainer.sandbox.describe()``.
    """
    return select_sandbox(
        settings.sandbox_backend_normalized,
        enforce_permissions=settings.sandbox_enforce_permissions,
    )


__all__ = ["select_runtime_sandbox"]
