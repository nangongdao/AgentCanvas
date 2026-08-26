"""Validation for attaching MCP configurations to approved catalog manifests."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from app.db.models import McpCatalogVersion, McpServer
from app.schemas.api import McpCatalogManifest, McpCatalogPermissions


class McpCatalogPolicyError(ValueError):
    """Raised when a server configuration exceeds its approved manifest."""


def validate_catalog_binding(row: McpServer, version: McpCatalogVersion) -> McpCatalogManifest:
    """Validate transport and declared command/filesystem/network capabilities."""
    manifest = McpCatalogManifest.model_validate(version.manifest_json)
    if row.transport != manifest.transport:
        raise McpCatalogPolicyError(
            f"catalog transport {manifest.transport} does not match server transport {row.transport}"
        )

    permissions = manifest.permissions
    if row.transport == "stdio":
        command = Path(row.command or "").name.casefold()
        allowed_commands = {Path(value).name.casefold() for value in permissions.commands}
        if not command or command not in allowed_commands:
            raise McpCatalogPolicyError(
                f"stdio command '{command or '<missing>'}' is outside the catalog command grant"
            )
        script = next(
            (
                str(value)
                for value in (row.args_json or [])
                if not str(value).startswith("-")
                and Path(str(value)).suffix.casefold() in {".py", ".js", ".mjs", ".cjs"}
            ),
            "",
        )
        roots = tuple(Path(root).expanduser().resolve() for root in permissions.filesystem)
        if not script or not roots:
            raise McpCatalogPolicyError("stdio catalog binding requires a script and filesystem grant")
        script_path = Path(script).expanduser().resolve()
        if not any(script_path.is_relative_to(root) for root in roots):
            raise McpCatalogPolicyError("MCP script is outside the catalog filesystem grant")
        return manifest

    host = (urlparse(row.url or "").hostname or "").casefold()
    allowed_hosts = {value.casefold() for value in permissions.network}
    if not host or host not in allowed_hosts:
        raise McpCatalogPolicyError(
            f"MCP URL host '{host or '<missing>'}' is outside the catalog network grant"
        )
    return manifest


def catalog_version_is_usable(version: McpCatalogVersion) -> bool:
    """Superseded versions remain usable by existing bindings until upgraded."""
    return version.status in {"approved", "superseded"}


def catalog_version_diff(
    previous: McpCatalogVersion | None,
    target: McpCatalogVersion,
) -> dict[str, object]:
    """Return a bounded, review-friendly diff between two immutable versions."""
    target_manifest = McpCatalogManifest.model_validate(target.manifest_json)
    previous_manifest = (
        McpCatalogManifest.model_validate(previous.manifest_json) if previous else None
    )
    source_changed = previous is not None and previous.source_ref != target.source_ref
    transport_changed = bool(
        previous_manifest is not None
        and previous_manifest.transport != target_manifest.transport
    )
    changed_fields: list[str] = []
    if previous is None:
        changed_fields.extend(["source_ref", "transport", "permissions"])
    else:
        if source_changed:
            changed_fields.append("source_ref")
        if transport_changed:
            changed_fields.append("transport")
        if previous_manifest is not None and previous_manifest.permissions != target_manifest.permissions:
            changed_fields.append("permissions")

    def added(field_name: str) -> list[str]:
        target_values = list(getattr(target_manifest.permissions, field_name))
        previous_keys = {
            value.casefold()
            for value in (
                getattr(previous_manifest.permissions, field_name)
                if previous_manifest
                else []
            )
        }
        return [value for value in target_values if value.casefold() not in previous_keys]

    def removed(field_name: str) -> list[str]:
        target_keys = {
            value.casefold() for value in getattr(target_manifest.permissions, field_name)
        }
        previous_values = list(
            getattr(previous_manifest.permissions, field_name) if previous_manifest else []
        )
        return [value for value in previous_values if value.casefold() not in target_keys]

    return {
        "entry_id": target.entry_id,
        "from_version_id": previous.id if previous else None,
        "from_version": previous.version if previous else None,
        "to_version_id": target.id,
        "to_version": target.version,
        "source_changed": source_changed,
        "transport_changed": transport_changed,
        "permission_added": McpCatalogPermissions(
            network=added("network"),
            filesystem=added("filesystem"),
            commands=added("commands"),
        ),
        "permission_removed": McpCatalogPermissions(
            network=removed("network"),
            filesystem=removed("filesystem"),
            commands=removed("commands"),
        ),
        "changed_fields": changed_fields,
    }
