"""Phase 4 node plugin SDK and process-boundary tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import BACKEND_DIR
from app.engine.compiler import WorkflowCompiler
from app.engine.nodes import external_node_types, list_node_types
from app.engine.nodes.base import get_executor
from app.plugins import (
    PluginDescriptor,
    PluginLoadError,
    PluginManifest,
    PluginProcessError,
    PluginProcessRunner,
    PluginRegistry,
    PluginRequest,
)
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType
from tests.conftest import FakeEmitter, make_ctx


def _echo_descriptor() -> PluginDescriptor:
    registry = PluginRegistry(BACKEND_DIR / "plugins")
    descriptors = registry.load()
    return next(item for item in descriptors if item.manifest.node_type == "plugin.echo")


def _workflow() -> WorkflowDSL:
    return WorkflowDSL.model_validate(
        {
            "name": "plugin workflow",
            "nodes": [
                {"id": "s", "type": "start", "config": {}},
                {
                    "id": "p",
                    "type": "plugin.echo",
                    "config": {"message": "hello", "include_input": True},
                },
                {"id": "e", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "sp", "source": "s", "target": "p"},
                {"id": "pe", "source": "p", "target": "e"},
            ],
        }
    )


@pytest.mark.asyncio
async def test_echo_plugin_runs_in_compiler_and_emits_contract_events() -> None:
    emitter = FakeEmitter()
    descriptor = _echo_descriptor()
    ctx = make_ctx(emitter)
    graph = WorkflowCompiler().compile(_workflow(), ctx)
    result = await graph.ainvoke(
        {
            "inputs": {"user_query": "world"},
            "node_outputs": {},
            "loop_counts": {},
            "messages": [],
            "route": None,
            "error": None,
            "final_output": None,
        },
        config={"recursion_limit": 20},
    )

    assert result["node_outputs"]["p"]["output"] == {"text": "hello: world"}
    assert result["node_outputs"]["p"]["meta"]["plugin_id"] == "builtin.echo"
    plugin_events = [
        event
        for event in emitter.events
        if event["type"] == EventType.NODE_STREAMING
        and str(event.get("payload", {}).get("kind", "")).startswith("plugin")
    ]
    assert plugin_events[0]["payload"]["kind"] == "plugin_started"
    assert plugin_events[0]["payload"]["permissions"] == {
        "network": [],
        "filesystem": [],
        "commands": [],
    }
    assert any(event["payload"]["kind"] == "plugin_event" for event in plugin_events)
    assert descriptor.manifest.version == "1.0.0"


def test_plugin_metadata_and_dynamic_dsl_round_trip() -> None:
    plugin = next(item for item in list_node_types() if item["type"] == "plugin.echo")
    assert plugin["plugin"]["permissions"] == {"network": [], "filesystem": [], "commands": []}
    dsl = _workflow()
    assert dsl.nodes[1].type == "plugin.echo"
    assert dsl.model_dump(mode="json")["nodes"][1]["type"] == "plugin.echo"
    assert get_executor("plugin.echo").node_type == "plugin.echo"


@pytest.mark.asyncio
async def test_plugin_runner_rejects_timeout_and_protocol_violation(tmp_path: Path) -> None:
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import sys, time\n"
        "request = sys.stdin.readline()\n"
        "time.sleep(0.2)\n"
        "print('{}')\n",
        encoding="utf-8",
    )
    manifest = PluginManifest(
        plugin_id="test.timeout",
        version="1.0.0",
        node_type="plugin.timeout",
        label="Timeout",
        entrypoint="worker.py",
        timeout_seconds=0.05,
    )
    descriptor = PluginDescriptor(manifest=manifest, directory=tmp_path)
    request = PluginRequest(request_id="a" * 16, node_type="plugin.timeout")
    with pytest.raises(PluginProcessError, match="timed out"):
        await PluginProcessRunner().run(descriptor, request)

    bad_worker = tmp_path / "bad.py"
    bad_worker.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'protocol_version':'9.0','request_id':request['request_id'],'ok':True}))\n",
        encoding="utf-8",
    )
    bad_manifest = manifest.model_copy(
        update={
            "plugin_id": "test.bad",
            "node_type": "plugin.bad",
            "entrypoint": "bad.py",
            "timeout_seconds": 3.0,
        }
    )
    with pytest.raises(PluginProcessError, match="protocol version"):
        await PluginProcessRunner().run(
            PluginDescriptor(manifest=bad_manifest, directory=tmp_path), request.model_copy(
                update={"node_type": "plugin.bad"}
            )
        )


def test_plugin_registry_rejects_entrypoint_escape(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "escape"
    plugin_dir.mkdir()
    (tmp_path / "worker.py").write_text("", encoding="utf-8")
    (plugin_dir / "manifest.json").write_text(
        json.dumps(
            {
                "plugin_id": "test.escape",
                "version": "1.0.0",
                "node_type": "plugin.escape",
                "label": "Escape",
                "entrypoint": "../worker.py",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PluginLoadError, match="escapes"):
        PluginRegistry(tmp_path).load()


def test_plugin_registry_replaces_previous_root_without_stale_executors(tmp_path: Path) -> None:
    first = PluginRegistry(BACKEND_DIR / "plugins")
    first.load()
    assert external_node_types() == ("plugin.echo",)

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    PluginRegistry(empty_root).load()
    assert external_node_types() == ()


def test_unknown_plugin_type_is_rejected_before_compile() -> None:
    dsl = WorkflowDSL.model_validate(
        {
            "nodes": [
                {"id": "s", "type": "start", "config": {}},
                {"id": "p", "type": "plugin.missing", "config": {}},
                {"id": "e", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "sp", "source": "s", "target": "p"},
                {"id": "pe", "source": "p", "target": "e"},
            ],
        }
    )
    with pytest.raises(ValueError, match="unknown node type 'plugin.missing'"):
        WorkflowCompiler().compile(dsl, make_ctx(FakeEmitter()))
