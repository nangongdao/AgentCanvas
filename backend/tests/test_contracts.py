"""Versioned public contract generation and compatibility behavior."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.contracts import (
    API_CONTRACT_BASELINE_VERSION,
    API_CONTRACT_VERSION,
    DSL_CONTRACT_VERSION,
    EVENT_CONTRACT_VERSION,
    ContractCompatibilityError,
    ContractDriftError,
    build_contract_artifacts,
    check_contract_files,
    check_openapi_compatibility,
    check_schema_compatibility,
    initialize_contract_baseline,
    write_contract_files,
)
from app.core.config import Settings
from app.main import create_app
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType, ExecutionEvent


def _app(tmp_path):
    return create_app(Settings(data_dir=tmp_path, environment="test"))


def test_openapi_publishes_versioned_dsl_and_event_contracts(tmp_path) -> None:
    schema = _app(tmp_path).openapi()

    assert schema["info"]["version"] == API_CONTRACT_VERSION
    assert schema["x-agentcanvas-contracts"] == {
        "api": API_CONTRACT_VERSION,
        "dsl": DSL_CONTRACT_VERSION,
        "event": EVENT_CONTRACT_VERSION,
    }
    assert "WorkflowDSL" in schema["components"]["schemas"]
    assert "ExecutionEvent" in schema["components"]["schemas"]
    assert schema["components"]["schemas"]["ExecutionEvent"]["properties"][
        "event_type"
    ]["$ref"] == "#/components/schemas/ExecutionEventEventType"
    assert "$defs" not in schema["components"]["schemas"]["ExecutionEvent"]
    assert "ExecutionEventEventType" in schema["components"]["schemas"]


def test_dsl_and_events_reject_or_identify_contract_versions() -> None:
    assert WorkflowDSL().version == DSL_CONTRACT_VERSION
    with pytest.raises(ValidationError, match="version"):
        WorkflowDSL.model_validate({"version": "2.0"})

    event = ExecutionEvent("execution-1", EventType.NODE_STARTED)
    assert event.to_dict()["schema_version"] == EVENT_CONTRACT_VERSION


def test_contract_artifacts_are_deterministic_and_backend_owned(tmp_path) -> None:
    first = build_contract_artifacts(_app(tmp_path))
    second = build_contract_artifacts(_app(tmp_path))

    assert first == second
    assert first["openapi"]["components"]["schemas"]["WorkflowDSL"]["title"] == first[
        "dsl"
    ]["title"]
    assert first["openapi"]["components"]["schemas"]["ExecutionEvent"]["title"] == first[
        "event"
    ]["title"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda schema: schema["properties"].pop("payload"), "removed property payload"),
        (
            lambda schema: schema["$defs"]["EventType"]["enum"].pop(),
            "removed enum value",
        ),
        (
            lambda schema: schema.setdefault("required", []).append("node_id"),
            "new required property node_id",
        ),
    ],
)
def test_schema_compatibility_rejects_breaking_changes(tmp_path, mutation, message) -> None:
    baseline = build_contract_artifacts(_app(tmp_path))["event"]
    current = deepcopy(baseline)
    mutation(current)

    with pytest.raises(ContractCompatibilityError, match=message):
        check_schema_compatibility(baseline, current, contract="event")


def test_schema_compatibility_allows_additive_optional_fields(tmp_path) -> None:
    baseline = build_contract_artifacts(_app(tmp_path))["event"]
    current = deepcopy(baseline)
    current["properties"]["trace_id"] = {"anyOf": [{"type": "string"}, {"type": "null"}]}

    check_schema_compatibility(baseline, current, contract="event")


def test_contract_file_check_detects_generated_drift(tmp_path) -> None:
    root = tmp_path / "contracts"
    app = _app(tmp_path)
    write_contract_files(app, root=root)
    initialize_contract_baseline(app, root=root)
    check_contract_files(app, root=root)

    event_path = root / "current" / "execution-event.schema.json"
    event_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ContractDriftError, match="execution-event.schema.json"):
        check_contract_files(app, root=root)


def test_contract_baseline_cannot_be_replaced_within_the_current_major(tmp_path) -> None:
    root = tmp_path / "contracts"
    app = _app(tmp_path)
    initialize_contract_baseline(app, root=root)

    assert (root / "baselines" / API_CONTRACT_BASELINE_VERSION).is_dir()
    with pytest.raises(ContractDriftError, match="change the API contract major"):
        initialize_contract_baseline(app, root=root)


def test_contract_file_check_detects_manifest_drift(tmp_path) -> None:
    root = tmp_path / "contracts"
    app = _app(tmp_path)
    write_contract_files(app, root=root)
    initialize_contract_baseline(app, root=root)
    (root / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ContractDriftError, match="manifest.json"):
        check_contract_files(app, root=root)


def test_openapi_compatibility_rejects_removed_operations_and_required_inputs(tmp_path) -> None:
    baseline = build_contract_artifacts(_app(tmp_path))["openapi"]

    without_path = deepcopy(baseline)
    without_path["paths"].pop("/api/workflows")
    with pytest.raises(ContractCompatibilityError, match="removed path /api/workflows"):
        check_openapi_compatibility(baseline, without_path)

    without_operation = deepcopy(baseline)
    without_operation["paths"]["/api/workflows"].pop("get")
    with pytest.raises(ContractCompatibilityError, match="removed operation GET"):
        check_openapi_compatibility(baseline, without_operation)

    required_parameter = deepcopy(baseline)
    required_parameter["paths"]["/api/workflows"]["get"].setdefault("parameters", []).append(
        {
            "name": "tenant",
            "in": "query",
            "required": True,
            "schema": {"type": "string"},
        }
    )
    with pytest.raises(ContractCompatibilityError, match="new required parameter query.tenant"):
        check_openapi_compatibility(baseline, required_parameter)


def test_openapi_compatibility_checks_parameter_and_request_body_schemas(tmp_path) -> None:
    baseline = build_contract_artifacts(_app(tmp_path))["openapi"]

    narrowed_parameter = deepcopy(baseline)
    parameters = narrowed_parameter["paths"]["/api/workflows"]["get"]["parameters"]
    limit_schema = next(item["schema"] for item in parameters if item["name"] == "limit")
    limit_schema["maximum"] = 100
    with pytest.raises(ContractCompatibilityError, match="narrowed maximum"):
        check_openapi_compatibility(baseline, narrowed_parameter)

    narrowed_body = deepcopy(baseline)
    body_schema = narrowed_body["paths"]["/api/workflows"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    body_schema["$ref"] = "#/components/schemas/WorkflowUpdate"
    with pytest.raises(ContractCompatibilityError, match="openapi request"):
        check_openapi_compatibility(baseline, narrowed_body)


def test_openapi_compatibility_rejects_removed_response_fields(tmp_path) -> None:
    baseline = build_contract_artifacts(_app(tmp_path))["openapi"]
    current = deepcopy(baseline)
    current["components"]["schemas"]["WorkflowOut"]["properties"].pop("name")

    with pytest.raises(ContractCompatibilityError, match="removed property name"):
        check_openapi_compatibility(baseline, current)
