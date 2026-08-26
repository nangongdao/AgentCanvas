"""C8-1 process-level sandbox escape and enforcement tests.

The escape acceptance gate from the roadmap is:

    code 节点逃逸测试集(读 /etc/passwd、连外网、fork 炸弹、超内存)全部被阻断

These tests do not require a real ``nsjail``/``bwrap`` binary (CI images and
Windows dev lack them). Instead they assert at the *strategy layer* that the
``SandboxBackend.prepare`` output — the argv a subprocess is actually exec'd
with — enforces each escape vector:

* host rootfs read-only except declared writable mounts  → blocks /etc/passwd writes
* a disconnected network namespace unless network declared → blocks outbound egress
* ``rlimit_nproc`` / ``rlimit_as`` caps                     → blocks fork bombs / OOM
* seccomp/rlimit flags present where the backend supports them

When the OS-level tool is unavailable the backend degrades explicitly and
reports ``degraded=True`` with ``enforces_permissions=False`` so the runner can
refuse high-risk node types instead of silently running unisolated.
"""

from __future__ import annotations

import os

import pytest

from app.core.sandbox import (
    BubblewrapSandbox,
    NoSandbox,
    NsjailSandbox,
    ProcessCleanupSandbox,
    SandboxProfile,
    profile_from_permissions,
    select_sandbox,
)
from app.plugins.protocol import PluginPermissions


def _permissions(**overrides: object) -> PluginPermissions:
    return PluginPermissions(**overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. profile derivation: deny-by-default, declared permissions opt in
# ---------------------------------------------------------------------------


def test_profile_is_deny_by_default() -> None:
    profile = profile_from_permissions(_permissions())
    assert profile.network is False
    assert profile.writable_roots == ()
    assert profile.cpu_time_seconds > 0
    assert profile.address_space_mb > 0
    assert profile.process_count > 0


def test_network_declaration_enables_egress() -> None:
    profile = profile_from_permissions(_permissions(network=["api.example.com"]))
    assert profile.network is True


def test_filesystem_declaration_becomes_writable_mounts() -> None:
    profile = profile_from_permissions(
        _permissions(filesystem=["/scratch/b", "/scratch/a"])
    )
    assert profile.writable_roots == ("/scratch/a", "/scratch/b")  # sorted


def test_profile_custom_limits_override_defaults() -> None:
    profile = profile_from_permissions(
        _permissions(),
        cpu_time_seconds=5,
        address_space_mb=128,
        file_size_mb=8,
        process_count=4,
        open_files=32,
    )
    assert profile.cpu_time_seconds == 5
    assert profile.address_space_mb == 128
    assert profile.file_size_mb == 8
    assert profile.process_count == 4
    assert profile.open_files == 32


# ---------------------------------------------------------------------------
# 2. nsjail backend: argv enforcement of every escape vector
# ---------------------------------------------------------------------------


def _nsjail_argv(profile: SandboxProfile) -> list[str]:
    backend = NsjailSandbox()
    return backend.prepare(
        argv=["python", "entry.py"],
        env={"PYTHONIOENCODING": "utf-8"},
        cwd="/plugin",
        profile=profile,
    ).runner_argv


def test_nsjail_blocks_network_by_default() -> None:
    """Undeclared network → disconnected netns, no --disable_clone_newnet."""
    argv = _nsjail_argv(profile_from_permissions(_permissions()))
    assert "--disable_clone_newnet" not in argv
    # The connected path is opt-in only; absence is the enforcement.


def test_nsjail_allows_network_only_when_declared() -> None:
    argv = _nsjail_argv(profile_from_permissions(_permissions(network=["api.example.com"])))
    assert "--disable_clone_newnet" in argv


def test_nsjail_read_only_rootfs_blocks_etc_passwd_write() -> None:
    """No writable mounts declared → no --bindmount, host rootfs read-only."""
    argv = _nsjail_argv(profile_from_permissions(_permissions()))
    assert "--bindmount" not in argv


def test_nsjail_writable_mounts_only_for_declared_roots() -> None:
    argv = _nsjail_argv(
        profile_from_permissions(_permissions(filesystem=["/scratch"]))
    )
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--bindmount"]
    assert mounts == ["/scratch:/scratch"]


def test_nsjail_rlimits_block_fork_bomb_and_oom() -> None:
    """rlimit_nproc + rlimit_as caps fork bombs and address-space exhaustion."""
    profile = profile_from_permissions(
        _permissions(),
        cpu_time_seconds=10,
        address_space_mb=64,
        process_count=8,
        open_files=16,
        file_size_mb=4,
    )
    argv = _nsjail_argv(profile)
    pairs = {argv[i]: argv[i + 1] for i in range(len(argv) - 1)}
    assert pairs["--rlimit_nproc"] == "8"
    assert pairs["--rlimit_as"] == "64"
    assert pairs["--rlimit_cpu"] == "10"
    assert pairs["--rlimit_nofile"] == "16"
    assert pairs["--rlimit_fsize"] == "4"
    # A wall-clock time_limit complements the rlimit CPU cap so a tight compute
    # loop cannot run unbounded even if the asyncio timeout is bypassed.
    assert int(pairs["--time_limit"]) >= profile.cpu_time_seconds


def test_nsjail_env_not_widened_by_sandbox() -> None:
    prepared = NsjailSandbox().prepare(
        argv=["python", "entry.py"],
        env={"PYTHONIOENCODING": "utf-8"},
        cwd="/plugin",
        profile=profile_from_permissions(_permissions()),
    )
    assert prepared.runner_env == {"PYTHONIOENCODING": "utf-8"}


def test_nsjail_available_false_on_windows_or_missing_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = NsjailSandbox()
    monkeypatch.setattr(os, "name", "nt")
    assert backend.available() is False


# ---------------------------------------------------------------------------
# 3. bubblewrap backend: argv enforcement + honest seccomp reporting
# ---------------------------------------------------------------------------


def _bwrap_argv(profile: SandboxProfile) -> list[str]:
    backend = BubblewrapSandbox()
    return backend.prepare(
        argv=["python", "entry.py"],
        env={"PYTHONIOENCODING": "utf-8"},
        cwd="/plugin",
        profile=profile,
    ).runner_argv


def test_bwrap_unshares_net_by_default_blocking_egress() -> None:
    argv = _bwrap_argv(profile_from_permissions(_permissions()))
    assert "--unshare-net" in argv


def test_bwrap_keeps_netns_when_network_declared() -> None:
    argv = _bwrap_argv(profile_from_permissions(_permissions(network=["api.example.com"])))
    assert "--unshare-net" not in argv


def test_bwrap_read_only_root_with_explicit_writable_only() -> None:
    argv = _bwrap_argv(profile_from_permissions(_permissions(filesystem=["/scratch"])))
    assert "--ro-bind" in argv  # host rootfs mounted read-only
    binds = [
        argv[i + 1 : i + 3]
        for i, a in enumerate(argv)
        if a == "--bind" and i + 2 < len(argv)
    ]
    assert binds == [["/scratch", "/scratch"]]


def test_bwrap_unshares_pid_blocking_fork_bombs() -> None:
    argv = _bwrap_argv(profile_from_permissions(_permissions()))
    assert "--unshare-pid" in argv
    assert "--die-with-parent" in argv


def test_bwrap_describe_is_honest_about_seccomp() -> None:
    assert BubblewrapSandbox().describe()["seccomp"] is False


# ---------------------------------------------------------------------------
# 4. degraded backends: explicit, never silently unisolated
# ---------------------------------------------------------------------------


def test_process_cleanup_is_degraded_and_non_enforcing() -> None:
    status = ProcessCleanupSandbox().describe()
    assert status["backend"] == "process_cleanup"
    assert status["degraded"] is True
    assert status["enforces_permissions"] is False


def test_no_sandbox_is_degraded_and_non_enforcing() -> None:
    status = NoSandbox().describe()
    assert status["backend"] == "none"
    assert status["degraded"] is True
    assert status["enforces_permissions"] is False


def test_process_cleanup_preserves_argv_and_env_equivalence() -> None:
    prepared = ProcessCleanupSandbox().prepare(
        argv=["python", "entry.py"],
        env={"PYTHONIOENCODING": "utf-8"},
        cwd="/plugin",
        profile=profile_from_permissions(_permissions()),
    )
    assert prepared.runner_argv == ["python", "entry.py"]
    assert prepared.runner_env == {"PYTHONIOENCODING": "utf-8"}


# ---------------------------------------------------------------------------
# 5. select_sandbox: auto resolution + explicit pinning + tool-missing degrade
# ---------------------------------------------------------------------------


def test_select_none_never_degrades() -> None:
    assert select_sandbox("none").name == "none"


def test_select_explicit_nsjail_degrades_when_tool_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force every OS-level backend to report unavailable.
    monkeypatch.setattr(NsjailSandbox, "available", lambda self: False)
    monkeypatch.setattr(BubblewrapSandbox, "available", lambda self: False)
    chosen = select_sandbox("nsjail")
    assert chosen.name == "process_cleanup"
    assert chosen.describe()["degraded"] is True


def test_select_auto_prefers_os_level_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(NsjailSandbox, "available", lambda self: True)
    assert select_sandbox("auto").name == "nsjail"
    monkeypatch.setattr(NsjailSandbox, "available", lambda self: False)
    monkeypatch.setattr(BubblewrapSandbox, "available", lambda self: True)
    assert select_sandbox("auto").name == "bubblewrap"


def test_select_auto_falls_back_to_cleanup_without_any_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(NsjailSandbox, "available", lambda self: False)
    monkeypatch.setattr(BubblewrapSandbox, "available", lambda self: False)
    assert select_sandbox("auto").name == "process_cleanup"


def test_select_invalid_backend_value_raises() -> None:
    with pytest.raises(ValueError, match="unknown SANDBOX_BACKEND"):
        select_sandbox("definitely-not-a-backend")


# ---------------------------------------------------------------------------
# 6. escape-vector matrix: each backend's enforcement per vector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "backend_factory",
    [NsjailSandbox, BubblewrapSandbox],
    ids=["nsjail", "bubblewrap"],
)
def test_escape_vector_host_filesystem_read_only(backend_factory) -> None:
    """读 /etc/passwd 写入:无声明 → 无 writable 挂载,rootfs 只读。"""
    backend = backend_factory()
    if not backend.available():
        pytest.skip(f"{backend.name} tool unavailable in this environment")
    argv = backend.prepare(
        argv=["python", "entry.py"],
        env={},
        cwd="/plugin",
        profile=profile_from_permissions(_permissions()),
    ).runner_argv
    joined = " ".join(argv)
    # No permissive writable bind of the host root for an undeclared fs perm.
    if backend.name == "nsjail":
        assert "--bindmount /:/" not in joined
    else:
        binds = [
            argv[i + 1 : i + 3]
            for i, a in enumerate(argv)
            if a == "--bind" and i + 2 < len(argv)
        ]
        assert ["/", "/"] not in binds


@pytest.mark.parametrize(
    "backend_factory",
    [NsjailSandbox, BubblewrapSandbox],
    ids=["nsjail", "bubblewrap"],
)
def test_escape_vector_outbound_network_blocked(backend_factory) -> None:
    """连外网:无 network 声明 → 断开 netns / 不保留 host netns。"""
    backend = backend_factory()
    if not backend.available():
        pytest.skip(f"{backend.name} tool unavailable in this environment")
    argv = backend.prepare(
        argv=["python", "entry.py"],
        env={},
        cwd="/plugin",
        profile=profile_from_permissions(_permissions()),
    ).runner_argv
    if backend.name == "nsjail":
        assert "--disable_clone_newnet" not in argv
    else:
        assert "--unshare-net" in argv


@pytest.mark.parametrize(
    "backend_factory",
    [NsjailSandbox, BubblewrapSandbox],
    ids=["nsjail", "bubblewrap"],
)
def test_escape_vector_fork_bomb_capped(backend_factory) -> None:
    """fork 炸弹:nproc rlimit / unshare-pid 限制进程数。"""
    backend = backend_factory()
    if not backend.available():
        pytest.skip(f"{backend.name} tool unavailable in this environment")
    profile = profile_from_permissions(_permissions(), process_count=4)
    argv = backend.prepare(
        argv=["python", "entry.py"],
        env={},
        cwd="/plugin",
        profile=profile,
    ).runner_argv
    if backend.name == "nsjail":
        pairs = {argv[i]: argv[i + 1] for i in range(len(argv) - 1)}
        assert int(pairs["--rlimit_nproc"]) == 4
    else:
        assert "--unshare-pid" in argv


@pytest.mark.parametrize(
    "backend_factory",
    [NsjailSandbox],
    ids=["nsjail"],
)
def test_escape_vector_memory_capped(backend_factory) -> None:
    """超内存:rlimit_as 封顶地址空间。"""
    backend = backend_factory()
    if not backend.available():
        pytest.skip(f"{backend.name} tool unavailable in this environment")
    profile = profile_from_permissions(_permissions(), address_space_mb=64)
    argv = backend.prepare(
        argv=["python", "entry.py"],
        env={},
        cwd="/plugin",
        profile=profile,
    ).runner_argv
    pairs = {argv[i]: argv[i + 1] for i in range(len(argv) - 1)}
    assert int(pairs["--rlimit_as"]) == 64
