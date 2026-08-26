"""Generate and verify versioned AgentCanvas public contract artifacts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import TypeAdapter

from app.contract_compatibility import (
    check_openapi_compatibility,
    check_schema_compatibility,
)
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import ExecutionEvent

API_CONTRACT_VERSION = "1.0.0"
API_CONTRACT_BASELINE_VERSION = "1.0.0"
DSL_CONTRACT_VERSION = "1.0"
EVENT_CONTRACT_VERSION = "1.0"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPOSITORY_ROOT / "contracts"
CURRENT_ARTIFACTS = {
    "openapi": "openapi.json",
    "dsl": "workflow-dsl.schema.json",
    "event": "execution-event.schema.json",
}


class ContractDriftError(ValueError):
    """Raised when checked-in generated contracts differ from backend sources."""


def _dsl_schema() -> dict[str, Any]:
    return WorkflowDSL.model_json_schema(mode="validation")


def _event_schema() -> dict[str, Any]:
    return TypeAdapter(ExecutionEvent).json_schema(mode="validation")


def _contract_openapi(app: FastAPI) -> dict[str, Any]:
    schema = get_openapi(
        title=app.title,
        version=API_CONTRACT_VERSION,
        openapi_version=app.openapi_version,
        summary=app.summary,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
        servers=app.servers,
        separate_input_output_schemas=app.separate_input_output_schemas,
    )
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    _install_openapi_contract_schema(schemas, "WorkflowDSL", _dsl_schema())
    _install_openapi_contract_schema(schemas, "ExecutionEvent", _event_schema())
    schema["x-agentcanvas-contracts"] = {
        "api": API_CONTRACT_VERSION,
        "dsl": DSL_CONTRACT_VERSION,
        "event": EVENT_CONTRACT_VERSION,
    }
    return schema


def install_contract_openapi(app: FastAPI) -> None:
    """Install the versioned OpenAPI builder while retaining FastAPI caching."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = _contract_openapi(app)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


def build_contract_artifacts(app: FastAPI) -> dict[str, dict[str, Any]]:
    """Build deterministic JSON-compatible artifacts from backend sources."""
    return {
        "openapi": deepcopy(app.openapi()),
        "dsl": _dsl_schema(),
        "event": _event_schema(),
    }


def _install_openapi_contract_schema(
    components: dict[str, Any], name: str, schema: dict[str, Any]
) -> None:
    """Promote standalone definitions to OpenAPI components and rewrite refs."""
    root = deepcopy(schema)
    definitions = root.pop("$defs", {})

    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (
                    f"#/components/schemas/{name}{item.removeprefix('#/$defs/')}"
                    if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/")
                    else rewrite(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    components[name] = rewrite(root)
    for definition_name, definition in definitions.items():
        components[f"{name}{definition_name}"] = rewrite(definition)


def write_contract_files(app: FastAPI, *, root: Path = CONTRACT_ROOT) -> None:
    """Write current generated artifacts and their version manifest."""
    artifacts = build_contract_artifacts(app)
    current = root / "current"
    current.mkdir(parents=True, exist_ok=True)
    for name, filename in CURRENT_ARTIFACTS.items():
        (current / filename).write_text(_json_text(artifacts[name]), encoding="utf-8")
    (root / "manifest.json").write_text(_json_text(_contract_manifest()), encoding="utf-8")


def initialize_contract_baseline(
    app: FastAPI,
    *,
    root: Path = CONTRACT_ROOT,
) -> None:
    """Create the immutable compatibility baseline for the current API major."""
    baseline = root / "baselines" / API_CONTRACT_BASELINE_VERSION
    if baseline.exists():
        raise ContractDriftError(
            f"contract baseline {API_CONTRACT_BASELINE_VERSION} already exists; "
            "change the API contract major and its baseline version explicitly"
        )
    baseline.mkdir(parents=True, exist_ok=True)
    artifacts = build_contract_artifacts(app)
    for name, filename in CURRENT_ARTIFACTS.items():
        (baseline / filename).write_text(_json_text(artifacts[name]), encoding="utf-8")


def check_contract_files(app: FastAPI, *, root: Path = CONTRACT_ROOT) -> None:
    """Check generated-file drift and compatibility with the accepted baseline."""
    artifacts = build_contract_artifacts(app)
    current = root / "current"
    for name, filename in CURRENT_ARTIFACTS.items():
        path = current / filename
        expected = _json_text(artifacts[name])
        actual = path.read_text(encoding="utf-8") if path.exists() else ""
        if actual != expected:
            raise ContractDriftError(
                f"generated contract drift: {filename}; run the contract generator"
            )

    manifest_path = root / "manifest.json"
    expected_manifest = _json_text(_contract_manifest())
    actual_manifest = (
        manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else ""
    )
    if actual_manifest != expected_manifest:
        raise ContractDriftError(
            "generated contract drift: manifest.json; run the contract generator"
        )

    baseline = root / "baselines" / API_CONTRACT_BASELINE_VERSION
    baseline_artifacts = {
        name: json.loads((baseline / filename).read_text(encoding="utf-8"))
        for name, filename in CURRENT_ARTIFACTS.items()
    }
    check_openapi_compatibility(baseline_artifacts["openapi"], artifacts["openapi"])
    check_schema_compatibility(baseline_artifacts["dsl"], artifacts["dsl"], contract="dsl")
    check_schema_compatibility(
        baseline_artifacts["event"], artifacts["event"], contract="event"
    )


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _contract_manifest() -> dict[str, Any]:
    return {
        "versions": {
            "api": API_CONTRACT_VERSION,
            "dsl": DSL_CONTRACT_VERSION,
            "event": EVENT_CONTRACT_VERSION,
        },
        "current": {
            name: f"current/{filename}" for name, filename in CURRENT_ARTIFACTS.items()
        },
        "baseline": f"baselines/{API_CONTRACT_BASELINE_VERSION}",
    }
