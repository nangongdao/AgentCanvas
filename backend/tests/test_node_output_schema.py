"""C2-8: node ``output_schema`` metadata published via ``/api/node-types``.

The data-flow pane renders an output-shape preview from this declarative
schema, falling back to the live run snapshot for user-defined outputs. These
tests pin which node types expose a schema and the shape of the static ones so
the editor can rely on the contract.
"""

from __future__ import annotations

from app.engine.nodes import list_node_types

# Node types whose output is fully user-defined (code source, child subgraph,
# or referenced workflow) intentionally leave ``output_schema`` empty; the pane
# falls back to the live run snapshot for these.
_DYNAMIC_OUTPUT_TYPES = frozenset({"code"})


def test_every_node_type_has_required_metadata_fields() -> None:
    for row in list_node_types():
        assert row["type"], row
        assert "label" in row
        assert isinstance(row["config_schema"], dict)
        # output_schema is optional but, when present, must be a JSON object schema.
        if "output_schema" in row:
            assert isinstance(row["output_schema"], dict)


def test_static_output_types_expose_a_schema() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    expected_static = {
        "agent",
        "rag",
        "http",
        "tool",
        "condition",
        "switch",
        "human",
        "start",
        "end",
        "iteration",
        "subworkflow",
    }
    for node_type in expected_static:
        assert "output_schema" in rows[node_type], f"{node_type} missing output_schema"
        schema = rows[node_type]["output_schema"]
        assert schema.get("type") == "object"
        props = schema.get("properties", {})
        assert "output" in props, f"{node_type} output_schema must expose an `output` field"


def test_dynamic_output_types_have_no_schema() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    for node_type in _DYNAMIC_OUTPUT_TYPES:
        assert "output_schema" not in rows[node_type], (
            f"{node_type} output is user-defined and must not declare a static schema"
        )


def test_switch_output_schema_enum_matches_runtime_merge_strategy() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    schema = rows["switch"]["output_schema"]
    merge = schema["properties"]["merge_strategy"]
    assert merge["enum"] == ["last", "first", "error", "collect"]


def test_agent_output_schema_describes_meta_usage_and_tool_trace() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    schema = rows["agent"]["output_schema"]
    props = schema["properties"]
    assert set(props) >= {"output", "text", "citations", "meta"}
    meta_props = props["meta"]["properties"]
    assert set(meta_props) >= {"usage", "tool_trace", "citations"}


def test_rag_output_schema_describes_chunks_and_citations() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    schema = rows["rag"]["output_schema"]
    props = schema["properties"]
    assert set(props) >= {"output", "text", "query", "chunks", "citations"}
    assert props["chunks"]["type"] == "array"


def test_http_output_schema_describes_status_and_extraction() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    schema = rows["http"]["output_schema"]
    props = schema["properties"]
    assert props["status_code"]["type"] == "integer"
    assert {"output", "url", "method"} <= set(props)


def test_tool_output_schema_describes_text_and_meta() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    schema = rows["tool"]["output_schema"]
    props = schema["properties"]
    assert {"output", "text", "meta"} <= set(props)
    meta_props = props["meta"]["properties"]
    assert {"server_id", "tool_name"} <= set(meta_props)


def test_human_output_schema_describes_decision_fields() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    schema = rows["human"]["output_schema"]
    props = schema["properties"]
    assert {"output", "approved", "decision"} <= set(props)
    assert props["approved"]["type"] == "boolean"


def test_start_and_end_allow_additional_properties() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    for node_type in ("start", "end"):
        schema = rows[node_type]["output_schema"]
        assert schema.get("type") == "object"
        # start/end outputs are user-shaped; the schema must not constrain
        # additional keys so arbitrary input/output templates are valid.
        assert schema.get("additionalProperties") is True


def test_iteration_output_schema_describes_rollup_counts() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    schema = rows["iteration"]["output_schema"]
    props = schema["properties"]
    assert {"results", "failures", "items_total", "items_succeeded", "items_failed"} <= set(props)
    for count_field in ("items_total", "items_succeeded", "items_failed"):
        assert props[count_field]["type"] == "integer"


def test_subworkflow_output_schema_is_open() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    schema = rows["subworkflow"]["output_schema"]
    assert schema.get("type") == "object"
    # Output is mapped from the referenced workflow's final_output, which is
    # user-defined, so additional properties must be allowed.
    assert schema.get("additionalProperties") is True


def test_condition_output_schema_records_branch() -> None:
    rows = {row["type"]: row for row in list_node_types()}
    schema = rows["condition"]["output_schema"]
    props = schema["properties"]
    assert {"output", "branch"} <= set(props)
    assert props["output"]["type"] == "string"
    assert props["branch"]["type"] == "string"
