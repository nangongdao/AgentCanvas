"""Backward-compatibility checks for OpenAPI and JSON Schema contracts."""

from __future__ import annotations

from typing import Any


class ContractCompatibilityError(ValueError):
    """Raised when a current schema breaks an accepted contract baseline."""


def check_openapi_compatibility(
    baseline: dict[str, Any], current: dict[str, Any]
) -> None:
    """Reject removed HTTP surfaces and newly required request inputs."""
    old_paths = baseline.get("paths", {})
    new_paths = current.get("paths", {})
    for path, old_path_item in old_paths.items():
        if path not in new_paths:
            raise ContractCompatibilityError(f"openapi: removed path {path}")
        new_path_item = new_paths[path]
        for method in _HTTP_METHODS:
            if method not in old_path_item:
                continue
            if method not in new_path_item:
                raise ContractCompatibilityError(
                    f"openapi: removed operation {method.upper()} {path}"
                )
            _check_operation_compatibility(
                path,
                method,
                old_path_item,
                new_path_item,
                old_path_item[method],
                new_path_item[method],
            )

    old_schemas = baseline.get("components", {}).get("schemas", {})
    new_schemas = current.get("components", {}).get("schemas", {})
    for name, old_schema in old_schemas.items():
        if name not in new_schemas:
            raise ContractCompatibilityError(f"openapi: removed component schema {name}")
        check_schema_compatibility(
            old_schema,
            new_schemas[name],
            contract=f"openapi component {name}",
        )


_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def _check_operation_compatibility(
    path: str,
    method: str,
    old_path_item: dict[str, Any],
    new_path_item: dict[str, Any],
    old_operation: dict[str, Any],
    new_operation: dict[str, Any],
) -> None:
    old_parameters = _parameter_map(old_path_item, old_operation)
    new_parameters = _parameter_map(new_path_item, new_operation)
    for key, old_parameter in old_parameters.items():
        if key not in new_parameters:
            raise ContractCompatibilityError(
                f"openapi: removed parameter {key[0]}.{key[1]} from {method.upper()} {path}"
            )
        if not old_parameter.get("required") and new_parameters[key].get("required"):
            raise ContractCompatibilityError(
                f"openapi: newly required parameter {key[0]}.{key[1]} on {method.upper()} {path}"
            )
        _check_media_schema(
            old_parameter,
            new_parameters[key],
            contract=f"openapi parameter {key[0]}.{key[1]} on {method.upper()} {path}",
        )
    for key, new_parameter in new_parameters.items():
        if key not in old_parameters and new_parameter.get("required"):
            raise ContractCompatibilityError(
                f"openapi: new required parameter {key[0]}.{key[1]} on {method.upper()} {path}"
            )

    old_body = old_operation.get("requestBody", {})
    new_body = new_operation.get("requestBody", {})
    if old_body and not new_body:
        raise ContractCompatibilityError(
            f"openapi: removed request body from {method.upper()} {path}"
        )
    if not old_body.get("required") and new_body.get("required"):
        raise ContractCompatibilityError(
            f"openapi: newly required request body on {method.upper()} {path}"
        )
    _check_content_compatibility(
        old_body.get("content", {}),
        new_body.get("content", {}),
        contract=f"openapi request {method.upper()} {path}",
    )

    old_responses = old_operation.get("responses", {})
    new_responses = new_operation.get("responses", {})
    for status, old_response in old_responses.items():
        if status not in new_responses:
            raise ContractCompatibilityError(
                f"openapi: removed response {status} from {method.upper()} {path}"
            )
        _check_content_compatibility(
            old_response.get("content", {}),
            new_responses[status].get("content", {}),
            contract=f"openapi response {method.upper()} {path} {status}",
        )


def _check_media_schema(
    baseline: dict[str, Any], current: dict[str, Any], *, contract: str
) -> None:
    old_schema = baseline.get("schema")
    new_schema = current.get("schema")
    if isinstance(old_schema, dict) and isinstance(new_schema, dict):
        check_schema_compatibility(old_schema, new_schema, contract=contract)


def _check_content_compatibility(
    baseline: dict[str, Any], current: dict[str, Any], *, contract: str
) -> None:
    for media_type, old_media in baseline.items():
        if media_type not in current:
            raise ContractCompatibilityError(
                f"{contract}: removed media type {media_type}"
            )
        _check_media_schema(old_media, current[media_type], contract=contract)


def _parameter_map(
    path_item: dict[str, Any], operation: dict[str, Any]
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for parameter in [*path_item.get("parameters", []), *operation.get("parameters", [])]:
        if isinstance(parameter, dict) and "in" in parameter and "name" in parameter:
            result[(str(parameter["in"]), str(parameter["name"]))] = parameter
    return result


def check_schema_compatibility(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    contract: str,
) -> None:
    """Reject schema changes that narrow accepted or exposed values."""
    _check_schema_node(baseline, current, contract=contract, path="$")


def _check_schema_node(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    contract: str,
    path: str,
) -> None:
    _check_reference_and_type(baseline, current, contract=contract, path=path)
    _check_enum_and_const(baseline, current, contract=contract, path=path)
    _check_narrowed_constraints(baseline, current, contract=contract, path=path)
    _check_schema_alternatives(baseline, current, contract=contract, path=path)
    _check_object_schema(baseline, current, contract=contract, path=path)

    old_items = baseline.get("items")
    new_items = current.get("items")
    if isinstance(old_items, dict) and isinstance(new_items, dict):
        _check_schema_node(old_items, new_items, contract=contract, path=f"{path}[]")
    elif not isinstance(old_items, dict) and isinstance(new_items, dict):
        raise ContractCompatibilityError(f"{contract}: added item constraint at {path}")


def _check_reference_and_type(
    baseline: dict[str, Any], current: dict[str, Any], *, contract: str, path: str
) -> None:
    old_ref = baseline.get("$ref")
    new_ref = current.get("$ref")
    if old_ref is not None and new_ref is not None and new_ref != old_ref:
        raise ContractCompatibilityError(
            f"{contract}: changed reference at {path} from {old_ref!r} to {new_ref!r}"
        )
    if old_ref is None and new_ref is not None:
        raise ContractCompatibilityError(f"{contract}: added reference constraint at {path}")

    old_type = baseline.get("type")
    new_type = current.get("type")
    if old_type is not None and new_type is not None and new_type != old_type:
        raise ContractCompatibilityError(
            f"{contract}: changed type at {path} from {old_type!r} to {new_type!r}"
        )
    if old_type is None and new_type is not None:
        raise ContractCompatibilityError(
            f"{contract}: added type constraint {new_type!r} at {path}"
        )


def _check_enum_and_const(
    baseline: dict[str, Any], current: dict[str, Any], *, contract: str, path: str
) -> None:
    old_enum = baseline.get("enum")
    new_enum = current.get("enum")
    if isinstance(new_enum, list):
        if not isinstance(old_enum, list):
            raise ContractCompatibilityError(f"{contract}: added enum constraint at {path}")
        removed = [value for value in old_enum if value not in new_enum]
        if removed:
            raise ContractCompatibilityError(
                f"{contract}: removed enum value {removed[0]!r} at {path}"
            )

    old_const = baseline.get("const", _MISSING)
    new_const = current.get("const", _MISSING)
    if new_const is not _MISSING and old_const is _MISSING:
        raise ContractCompatibilityError(f"{contract}: added const constraint at {path}")
    if old_const is not _MISSING and new_const is not _MISSING and new_const != old_const:
        raise ContractCompatibilityError(
            f"{contract}: changed const at {path} from {old_const!r} to {new_const!r}"
        )


def _check_object_schema(
    baseline: dict[str, Any], current: dict[str, Any], *, contract: str, path: str
) -> None:
    old_properties = baseline.get("properties", {})
    new_properties = current.get("properties", {})
    if isinstance(old_properties, dict):
        if not isinstance(new_properties, dict):
            raise ContractCompatibilityError(f"{contract}: removed properties at {path}")
        for name, old_property in old_properties.items():
            if name not in new_properties:
                raise ContractCompatibilityError(
                    f"{contract}: removed property {name} at {path}"
                )
            if isinstance(old_property, dict) and isinstance(new_properties[name], dict):
                _check_schema_node(
                    old_property,
                    new_properties[name],
                    contract=contract,
                    path=f"{path}.{name}",
                )

    added_required = sorted(
        set(current.get("required", [])) - set(baseline.get("required", []))
    )
    if added_required:
        raise ContractCompatibilityError(
            f"{contract}: new required property {added_required[0]} at {path}"
        )

    old_definitions = baseline.get("$defs", {})
    new_definitions = current.get("$defs", {})
    if isinstance(old_definitions, dict):
        if not isinstance(new_definitions, dict):
            raise ContractCompatibilityError(f"{contract}: removed definitions at {path}")
        for name, old_definition in old_definitions.items():
            if name not in new_definitions:
                raise ContractCompatibilityError(
                    f"{contract}: removed definition {name} at {path}"
                )
            if isinstance(old_definition, dict) and isinstance(new_definitions[name], dict):
                _check_schema_node(
                    old_definition,
                    new_definitions[name],
                    contract=contract,
                    path=f"{path}.$defs.{name}",
                )


_MISSING = object()
_LOWER_BOUNDS = ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties")
_UPPER_BOUNDS = ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties")


def _check_narrowed_constraints(
    baseline: dict[str, Any], current: dict[str, Any], *, contract: str, path: str
) -> None:
    for keyword in _LOWER_BOUNDS:
        old_value = baseline.get(keyword)
        new_value = current.get(keyword)
        if new_value is not None and (old_value is None or new_value > old_value):
            raise ContractCompatibilityError(f"{contract}: narrowed {keyword} at {path}")
    for keyword in _UPPER_BOUNDS:
        old_value = baseline.get(keyword)
        new_value = current.get(keyword)
        if new_value is not None and (old_value is None or new_value < old_value):
            raise ContractCompatibilityError(f"{contract}: narrowed {keyword} at {path}")

    for keyword in ("pattern", "format", "multipleOf"):
        old_value = baseline.get(keyword, _MISSING)
        new_value = current.get(keyword, _MISSING)
        if new_value is not _MISSING and new_value != old_value:
            raise ContractCompatibilityError(
                f"{contract}: changed {keyword} constraint at {path}"
            )
    if current.get("additionalProperties") is False and baseline.get(
        "additionalProperties"
    ) is not False:
        raise ContractCompatibilityError(
            f"{contract}: disallowed additional properties at {path}"
        )
    if current.get("uniqueItems") is True and baseline.get("uniqueItems") is not True:
        raise ContractCompatibilityError(f"{contract}: required unique items at {path}")


def _check_schema_alternatives(
    baseline: dict[str, Any], current: dict[str, Any], *, contract: str, path: str
) -> None:
    for keyword in ("anyOf", "oneOf"):
        old_options = baseline.get(keyword)
        new_options = current.get(keyword)
        if not isinstance(old_options, list):
            if isinstance(new_options, list):
                raise ContractCompatibilityError(
                    f"{contract}: added {keyword} constraint at {path}"
                )
            continue
        if not isinstance(new_options, list):
            continue
        for index, old_option in enumerate(old_options):
            if not isinstance(old_option, dict):
                continue
            if not any(
                isinstance(new_option, dict)
                and _schema_node_is_compatible(
                    old_option,
                    new_option,
                    contract=contract,
                    path=f"{path}.{keyword}[{index}]",
                )
                for new_option in new_options
            ):
                raise ContractCompatibilityError(
                    f"{contract}: removed or narrowed {keyword} option {index} at {path}"
                )


def _schema_node_is_compatible(
    baseline: dict[str, Any], current: dict[str, Any], *, contract: str, path: str
) -> bool:
    try:
        _check_schema_node(baseline, current, contract=contract, path=path)
    except ContractCompatibilityError:
        return False
    return True
