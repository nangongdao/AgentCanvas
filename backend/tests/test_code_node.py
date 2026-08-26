"""Code node (C2-2) execution and sandbox contract tests."""

from __future__ import annotations

import pytest

from app.core.sandbox import (
    BubblewrapSandbox,
    NoSandbox,
    NsjailSandbox,
    ProcessCleanupSandbox,
    SandboxProfile,
    profile_from_permissions,
)
from app.engine.compiler import WorkflowCompiler
from app.engine.nodes import list_node_types
from app.engine.nodes.base import CompileContext, get_executor
from app.engine.nodes.code_runner import CodeExecutionError, run_code
from app.plugins.protocol import PluginPermissions
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType
from tests.conftest import FakeEmitter, make_ctx


def _ctx(emitter: FakeEmitter) -> CompileContext:
    ctx = make_ctx(emitter)
    # Tests run without nsjail/bwrap; NoSandbox matches the test-only opt-in
    # and the sandbox escape vectors are covered in test_sandbox.py at the
    # argv layer. Here we verify the code contract, not OS isolation.
    object.__setattr__(ctx, "sandbox", NoSandbox())
    return ctx


def _code_workflow(source: str, *, inputs: dict[str, str] | None = None) -> WorkflowDSL:
    return WorkflowDSL.model_validate(
        {
            "name": "code workflow",
            "nodes": [
                {"id": "s", "type": "start", "config": {}},
                {
                    "id": "c",
                    "type": "code",
                    "config": {
                        "language": "python",
                        "source": source,
                        "inputs": inputs or {},
                        "timeout_seconds": 5.0,
                        "memory_limit_mb": 64,
                        "process_count": 8,
                        "allow_network": False,
                        "allow_filesystem": [],
                    },
                },
                {"id": "e", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "sc", "source": "s", "target": "c"},
                {"id": "ce", "source": "c", "target": "e"},
            ],
        }
    )


@pytest.mark.asyncio
async def test_code_node_runs_and_returns_output() -> None:
    emitter = FakeEmitter()
    graph = WorkflowCompiler().compile(
        _code_workflow('output = {"sum": inputs["a"] + inputs["b"]}', inputs={"a": "{{input.a}}", "b": "{{input.b}}"}),
        _ctx(emitter),
    )
    result = await graph.ainvoke(
        {"inputs": {"a": 1, "b": 2}, "node_outputs": {}, "loop_counts": {}, "messages": [], "route": None, "error": None, "final_output": None},
        config={"recursion_limit": 20},
    )
    assert result["node_outputs"]["c"]["output"] == {"sum": 3}


@pytest.mark.asyncio
async def test_code_node_injects_template_inputs() -> None:
    emitter = FakeEmitter()
    graph = WorkflowCompiler().compile(
        _code_workflow(
            'output = {"doubled": inputs["x"] * 2}',
            inputs={"x": "{{input.value}}"},
        ),
        _ctx(emitter),
    )
    result = await graph.ainvoke(
        {"inputs": {"value": 21}, "node_outputs": {}, "loop_counts": {}, "messages": [], "route": None, "error": None, "final_output": None},
        config={"recursion_limit": 20},
    )
    assert result["node_outputs"]["c"]["output"] == {"doubled": 42}


@pytest.mark.asyncio
async def test_code_node_emits_lifecycle_events() -> None:
    emitter = FakeEmitter()
    graph = WorkflowCompiler().compile(
        _code_workflow('output = {"ok": True}'),
        _ctx(emitter),
    )
    await graph.ainvoke(
        {"inputs": {}, "node_outputs": {}, "loop_counts": {}, "messages": [], "route": None, "error": None, "final_output": None},
        config={"recursion_limit": 20},
    )
    streaming = [
        e for e in emitter.events
        if e["type"] == EventType.NODE_STREAMING
        and str(e["payload"].get("kind", "")).startswith("code")
    ]
    kinds = [e["payload"]["kind"] for e in streaming]
    assert "code_started" in kinds
    assert "code_finished" in kinds
    started = next(e for e in streaming if e["payload"]["kind"] == "code_started")
    assert started["payload"]["language"] == "python"
    assert started["payload"]["allow_network"] is False
    assert started["payload"]["allow_filesystem"] == []


@pytest.mark.asyncio
async def test_code_node_timeout_enforced() -> None:
    with pytest.raises(Exception):  # noqa: B017 — could be CodeExecutionError or wrapped
        await run_code(
            "import time\ntime.sleep(10)\noutput = 1",
            {},
            timeout_seconds=0.3,
            memory_limit_mb=64,
            process_count=8,
            allow_network=False,
            allow_filesystem=[],
            sandbox=NoSandbox(),
        )


@pytest.mark.asyncio
async def test_code_node_invalid_json_output_raises() -> None:
    with pytest.raises(CodeExecutionError, match="not valid JSON"):
        await run_code(
            "import sys\nsys.stdout.write('not json')\n",
            {},
            timeout_seconds=5,
            memory_limit_mb=64,
            process_count=8,
            allow_network=False,
            allow_filesystem=[],
            sandbox=NoSandbox(),
        )


@pytest.mark.asyncio
async def test_code_node_runtime_error_surfaces_exit_code() -> None:
    with pytest.raises(CodeExecutionError, match="exited"):
        await run_code(
            "raise RuntimeError('boom')\n",
            {},
            timeout_seconds=5,
            memory_limit_mb=64,
            process_count=8,
            allow_network=False,
            allow_filesystem=[],
            sandbox=NoSandbox(),
        )


@pytest.mark.asyncio
async def test_code_node_empty_output_is_none() -> None:
    result = await run_code(
        "# no output set\npass\n",
        {},
        timeout_seconds=5,
        memory_limit_mb=64,
        process_count=8,
        allow_network=False,
        allow_filesystem=[],
        sandbox=NoSandbox(),
    )
    assert result.output is None


@pytest.mark.asyncio
async def test_code_node_inputs_byte_limit_rejects_oversized() -> None:
    # Each rendered input value is snapshotted; a huge mapping must be rejected
    # before the subprocess is spawned.
    big = {"k" + str(i): "v" * 1000 for i in range(200)}
    from app.engine.nodes.code import _render_inputs  # noqa: PLC0415

    with pytest.raises(CodeExecutionError, match="exceed"):
        _render_inputs(big, {})


def test_code_node_registered_and_metadata_exposed() -> None:
    types = {t["type"] for t in list_node_types()}
    assert "code" in types
    executor = get_executor("code")
    meta = executor.metadata()
    assert meta["label"] == "Code"
    # config_schema is generated from CodeConfig via Pydantic
    assert "source" in meta["config_schema"]["properties"]
    assert "allow_network" in meta["config_schema"]["properties"]


def test_code_config_defaults_deny_network_and_filesystem() -> None:
    from app.schemas.dsl import CodeConfig

    cfg = CodeConfig(source="output = 1")
    assert cfg.allow_network is False
    assert cfg.allow_filesystem == []
    assert cfg.timeout_seconds == 10.0
    assert cfg.memory_limit_mb == 256


def test_code_config_rejects_empty_source() -> None:
    from pydantic import ValidationError

    from app.schemas.dsl import CodeConfig

    with pytest.raises(ValidationError):
        CodeConfig(source="")


def test_code_config_rejects_oversized_source() -> None:
    from pydantic import ValidationError

    from app.schemas.dsl import CodeConfig

    with pytest.raises(ValidationError):
        CodeConfig(source="x" * (32 * 1024 + 1))


# ---------------------------------------------------------------------------
# Sandbox contract: the Code node profile denies by default and the OS-level
# backends enforce each escape vector at the argv layer (mirrors C8-1 tests).
# ---------------------------------------------------------------------------


def test_code_profile_defaults_deny_network_and_filesystem() -> None:
    profile = profile_from_permissions(PluginPermissions())
    assert profile.network is False
    assert profile.writable_roots == ()


@pytest.mark.parametrize(
    "backend_cls",
    [NsjailSandbox, BubblewrapSandbox],
    ids=["nsjail", "bubblewrap"],
)
def test_code_node_escape_vector_network_blocked(backend_cls) -> None:
    backend = backend_cls()
    if not backend.available():
        pytest.skip(f"{backend.name} unavailable")
    profile = SandboxProfile(network=False, writable_roots=())
    argv = backend.prepare(
        argv=["python", "script.py"],
        env={},
        cwd="/tmp",
        profile=profile,
    ).runner_argv
    if backend.name == "nsjail":
        assert "--disable_clone_newnet" not in argv
    else:
        assert "--unshare-net" in argv


@pytest.mark.parametrize(
    "backend_cls",
    [NsjailSandbox, BubblewrapSandbox],
    ids=["nsjail", "bubblewrap"],
)
def test_code_node_escape_vector_filesystem_read_only(backend_cls) -> None:
    backend = backend_cls()
    if not backend.available():
        pytest.skip(f"{backend.name} unavailable")
    profile = SandboxProfile(network=False, writable_roots=())
    argv = backend.prepare(
        argv=["python", "script.py"],
        env={},
        cwd="/tmp",
        profile=profile,
    ).runner_argv
    # No writable bind of the host root for an undeclared fs permission.
    if backend.name == "nsjail":
        assert "--bindmount /:/" not in " ".join(argv)
    else:
        binds = [
            argv[i + 1 : i + 3]
            for i, a in enumerate(argv)
            if a == "--bind" and i + 2 < len(argv)
        ]
        assert ["/", "/"] not in binds


def test_code_node_process_cleanup_degrades_explicitly() -> None:
    status = ProcessCleanupSandbox().describe()
    assert status["degraded"] is True
    assert status["enforces_permissions"] is False
