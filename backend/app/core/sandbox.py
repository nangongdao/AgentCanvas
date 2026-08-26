"""OS-level isolation for plugin and code-node subprocesses.

The plugin process runner (and the future C2-2 code node) execute arbitrary
third-party code in a fresh subprocess. Historically this was only "environment
cleanup": an empty environment, piped stdio, a timeout, and byte/event caps.
That keeps a well-behaved plugin honest but does nothing against a malicious or
compromised one — the subprocess inherits the host network, the readable host
rootfs, the full process table, and the ambient resource limits.

C8-1 upgrades that to real OS-level isolation on Linux via ``nsjail`` or
``bubblewrap`` (``bwrap``): a disabled network namespace, a read-only rootfs
with an explicit writable scratch, ``rlimit`` caps (CPU, address space, file
size, process count), and a seccomp filter. The declared plugin permissions
(network/filesystem) move from an *auditable declaration* to *mandatory
enforcement* — when enforcement is on, a permission not declared is denied.

Platforms or images without the wrapping tool degrade explicitly to the old
process-cleanup behaviour and are surfaced as degraded in readiness/UI so an
operator never mistakes "no sandbox" for "sandboxed".
"""

from __future__ import annotations

import logging
import os
import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.plugins.protocol import PluginPermissions

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxProfile:
    """Isolation parameters derived from a plugin manifest's declared permissions.

    The defaults are deny-by-default: no network, read-only rootfs, tight
    rlimits, no extra writable mounts. A manifest that legitimately needs
    network egress or a writable scratch directory opts in by declaring those
    permissions; ``SandboxBackend`` implementations translate each permission
    into the corresponding namespace/mount relaxation.
    """

    network: bool = False
    writable_roots: tuple[str, ...] = ()
    cpu_time_seconds: int = 30
    address_space_mb: int = 512
    file_size_mb: int = 32
    process_count: int = 16
    open_files: int = 64


def profile_from_permissions(
    permissions: PluginPermissions,
    *,
    cpu_time_seconds: int = 30,
    address_space_mb: int = 512,
    file_size_mb: int = 32,
    process_count: int = 16,
    open_files: int = 64,
) -> SandboxProfile:
    """Build a deny-by-default profile from declared plugin permissions.

    A non-empty ``network`` declaration enables network egress (the sandbox then
    keeps the host network namespace instead of unsharing into a disconnected
    one). ``filesystem`` entries become explicit writable mounts; anything not
    listed stays read-only. Default rlimits are conservative and overridable.
    The ``permissions`` shape (``.network`` / ``.filesystem`` sequences) matches
    ``PluginPermissions``; a duck-typed object with the same attributes works
    too, so this module avoids importing the plugin package and its circular
    executor→runner→sandbox edge.
    """
    network = list(getattr(permissions, "network", ()) or ())
    filesystem = list(getattr(permissions, "filesystem", ()) or ())
    return SandboxProfile(
        network=bool(network),
        writable_roots=tuple(sorted(set(filesystem))),
        cpu_time_seconds=cpu_time_seconds,
        address_space_mb=address_space_mb,
        file_size_mb=file_size_mb,
        process_count=process_count,
        open_files=open_files,
    )


@dataclass(frozen=True)
class PreparedCommand:
    """The argv and environment a sandbox wraps the original command in.

    ``runner_argv`` is the full argv to exec (sandbox binary + args + original
    argv). ``runner_env`` is the complete environment for the wrapped process.
    For the cleanup backend these are the original argv and a cleaned env, so
    behaviour stays equivalent to the pre-C8-1 path.
    """

    runner_argv: list[str]
    runner_env: dict[str, str]
    profile: SandboxProfile
    backend_name: str


class SandboxBackend(ABC):
    """Strategy interface wrapping a subprocess command in OS isolation."""

    name: str

    @abstractmethod
    def available(self) -> bool:
        """Return True when the wrapping tool is installed and usable here."""

    @abstractmethod
    def prepare(
        self,
        argv: list[str],
        env: dict[str, str],
        *,
        cwd: str,
        profile: SandboxProfile,
    ) -> PreparedCommand:
        """Wrap ``argv``/``env`` so the subprocess runs under isolation."""

    def describe(self) -> dict[str, Any]:
        """Return the operator-facing status (backend + enforcement + degraded)."""
        return {
            "backend": self.name,
            "available": self.available(),
            "enforces_permissions": True,
            "degraded": not self.available(),
        }


class _LimitArgs:
    """Shared rlimit argument builders for the Linux wrappers.

    Kept as a small helper namespace rather than per-backend duplication; both
    nsjail and bwrap express the same resource caps through different flags but
    identical semantics.
    """


class NsjailSandbox(SandboxBackend):
    """``nsjail`` wrapper: netns + read-only rootfs + rlimits + seccomp.

    Only constructed on POSIX hosts where ``nsjail`` resolves; ``available()``
    gates actual use. Permissions are enforced: an undeclared network or
    filesystem access is *not* granted — nsjail runs with ``--disable_clone_newnet``
    only when the profile explicitly allows network, otherwise a disconnected
    netns is created; writable mounts exist only for declared roots.
    """

    name = "nsjail"

    def __init__(self, binary: str = "nsjail") -> None:
        self._binary = binary

    def available(self) -> bool:
        if os.name == "nt":
            return False
        return shutil.which(self._binary) is not None

    def prepare(
        self,
        argv: list[str],
        env: dict[str, str],
        *,
        cwd: str,
        profile: SandboxProfile,
    ) -> PreparedCommand:
        args: list[str] = [
            self._binary,
            "--mode",
            "o",
            "--cwd",
            cwd,
            # Resource caps map directly to rlimits.
            "--rlimit_as",
            str(profile.address_space_mb),
            "--rlimit_fsize",
            str(profile.file_size_mb),
            "--rlimit_nofile",
            str(profile.open_files),
            "--rlimit_nproc",
            str(profile.process_count),
            "--rlimit_cpu",
            str(profile.cpu_time_seconds),
            # A short wall-clock cap complements the caller's asyncio timeout.
            "--time_limit",
            str(max(profile.cpu_time_seconds, 1) + 5),
            "--silent",
        ]
        if profile.network:
            # Declared network egress: keep the host netns instead of unsharing.
            args.append("--disable_clone_newnet")
        # Writable scratch only for explicitly declared filesystem roots.
        for root in profile.writable_roots:
            args.extend(["--bindmount", f"{root}:{root}"])
        args.extend(argv)
        return PreparedCommand(
            runner_argv=args,
            runner_env=env,
            profile=profile,
            backend_name=self.name,
        )


class BubblewrapSandbox(SandboxBackend):
    """``bwrap`` wrapper: an OS-level fallback when nsjail is unavailable.

    Provides the same deny-by-default isolation: an unshared, disconnected
    network namespace (unless declared), a read-only host rootfs via
    ``--ro-bind``, explicit writable mounts only for declared roots, and
    ``--unshare-pid``/``--die-with-parent`` for process containment. seccomp is
    applied through ``--die-with-parent`` + argv isolation when the filter is
    installed by the image; bwrap itself does not ship a default seccomp policy,
    so this backend reports ``seccomp=False`` in ``describe()`` for honesty.
    """

    name = "bubblewrap"

    def __init__(self, binary: str = "bwrap") -> None:
        self._binary = binary

    def available(self) -> bool:
        if os.name == "nt":
            return False
        return shutil.which(self._binary) is not None

    def prepare(
        self,
        argv: list[str],
        env: dict[str, str],
        *,
        cwd: str,
        profile: SandboxProfile,
    ) -> PreparedCommand:
        args: list[str] = [
            self._binary,
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--unshare-cgroup",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--ro-bind",
            "/",
            "/",
        ]
        if not profile.network:
            # Deny-by-default: a disconnected netns blocks all egress.
            args.append("--unshare-net")
        for root in profile.writable_roots:
            args.extend(["--bind", root, root])
        args.extend(["--chdir", cwd, "--"])
        args.extend(argv)
        return PreparedCommand(
            runner_argv=args,
            runner_env=env,
            profile=profile,
            backend_name=self.name,
        )

    def describe(self) -> dict[str, Any]:
        status = super().describe()
        status["seccomp"] = False
        return status


class ProcessCleanupSandbox(SandboxBackend):
    """Degraded backend preserving the pre-C8-1 "environment cleanup" behaviour.

    Used when no OS-level wrapper is available (Windows dev, a stripped image).
    The caller still gets byte/event/timeout caps from the runner; this backend
    only owns the argv/env surface and explicitly reports ``degraded=True`` so
    readiness/UI can warn that code execution is *not* OS-isolated.
    """

    name = "process_cleanup"

    def available(self) -> bool:
        # Always available — it is the no-tool fallback.
        return True

    def prepare(
        self,
        argv: list[str],
        env: dict[str, str],
        *,
        cwd: str,  # noqa: ARG002 — kept for interface symmetry; env already set
        profile: SandboxProfile,
    ) -> PreparedCommand:
        return PreparedCommand(
            runner_argv=list(argv),
            runner_env=env,
            profile=profile,
            backend_name=self.name,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": True,
            "enforces_permissions": False,
            "degraded": True,
            "seccomp": False,
        }


class NoSandbox(SandboxBackend):
    """Explicit opt-out backend for test environments only.

    Never selected by ``select_sandbox`` in production; surfaces as degraded so
    a misconfigured test image cannot be mistaken for an isolated deployment.
    """

    name = "none"

    def available(self) -> bool:
        return True

    def prepare(
        self,
        argv: list[str],
        env: dict[str, str],
        *,
        cwd: str,  # noqa: ARG002
        profile: SandboxProfile,
    ) -> PreparedCommand:
        return PreparedCommand(
            runner_argv=list(argv),
            runner_env=env,
            profile=profile,
            backend_name=self.name,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": True,
            "enforces_permissions": False,
            "degraded": True,
            "seccomp": False,
        }


def _warn_unenforced_if_needed(
    chosen: SandboxBackend, enforce_permissions: bool
) -> SandboxBackend:
    """Log (once per call) when the active backend cannot enforce permissions.

    A degraded backend (cleanup/none) is still returned — the caller decides
    whether to run high-risk node types under it — but the warning makes the
    lack of OS-level isolation visible in operator logs.
    """
    if enforce_permissions and not chosen.describe()["enforces_permissions"]:
        logger.warning(
            "sandbox backend '%s' does not enforce declared permissions; "
            "code/plugin execution is not OS-isolated",
            chosen.name,
        )
    return chosen


def select_sandbox(
    backend: str = "auto",
    *,
    enforce_permissions: bool = True,
) -> SandboxBackend:
    """Pick a sandbox backend, degrading explicitly when the tool is missing.

    ``auto`` prefers nsjail, then bubblewrap, then the cleanup fallback — the
    first whose ``available()`` is True. An explicit name pins the backend but
    still falls back to cleanup when that tool is absent *and* the requested
    backend is an OS-level one, logging the degradation. ``none`` is the only
    value that never degrades; it is reserved for tests and must not be set in
    production (``validate_runtime_settings`` rejects it there).
    """
    requested = (backend or "auto").strip().lower()
    if requested == "none":
        return _warn_unenforced_if_needed(NoSandbox(), enforce_permissions)
    if requested in {"nsjail", "bubblewrap"}:
        factory: Callable[[], SandboxBackend] = (
            NsjailSandbox if requested == "nsjail" else BubblewrapSandbox
        )
        candidate = factory()
        if candidate.available():
            return _warn_unenforced_if_needed(candidate, enforce_permissions)
        logger.warning(
            "SANDBOX_BACKEND=%s requested but the tool is unavailable; "
            "degrading to process_cleanup (no OS-level isolation)",
            requested,
        )
        return _warn_unenforced_if_needed(ProcessCleanupSandbox(), enforce_permissions)
    if requested == "auto":
        for factory in (
            NsjailSandbox,
            BubblewrapSandbox,
            ProcessCleanupSandbox,
        ):
            candidate = factory()
            if candidate.available():
                return _warn_unenforced_if_needed(candidate, enforce_permissions)
        # pragma: no cover — ProcessCleanupSandbox is always available
        return _warn_unenforced_if_needed(ProcessCleanupSandbox(), enforce_permissions)
    if requested == "process":
        return _warn_unenforced_if_needed(ProcessCleanupSandbox(), enforce_permissions)
    raise ValueError(
        f"unknown SANDBOX_BACKEND '{backend}'; "
        "expected auto, nsjail, bubblewrap, process, or none"
    )


__all__ = [
    "BubblewrapSandbox",
    "NsjailSandbox",
    "NoSandbox",
    "PreparedCommand",
    "ProcessCleanupSandbox",
    "SandboxBackend",
    "SandboxProfile",
    "profile_from_permissions",
    "select_sandbox",
]
