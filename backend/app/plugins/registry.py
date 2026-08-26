"""Discovery and registration of versioned node plugin manifests."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.sandbox import ProcessCleanupSandbox, SandboxBackend
from app.engine.nodes import clear_external_executors, register_executor_instance
from app.plugins.executor import PluginNodeExecutor
from app.plugins.protocol import PluginDescriptor, PluginManifest
from app.plugins.runner import PluginProcessRunner

MAX_MANIFEST_BYTES = 128 * 1024


class PluginLoadError(RuntimeError):
    """Raised when a plugin cannot be loaded without weakening the contract."""


class PluginRegistry:
    def __init__(
        self,
        root_dir: Path,
        *,
        max_input_bytes: int = 64 * 1024,
        max_output_bytes: int = 64 * 1024,
        max_events: int = 32,
        sandbox: SandboxBackend | None = None,
        cpu_time_seconds: int = 30,
        address_space_mb: int = 512,
        file_size_mb: int = 32,
        process_count: int = 16,
        open_files: int = 64,
    ) -> None:
        self.root_dir = root_dir.expanduser().resolve()
        self.sandbox = sandbox if sandbox is not None else ProcessCleanupSandbox()
        self.runner = PluginProcessRunner(
            max_input_bytes=max_input_bytes,
            max_output_bytes=max_output_bytes,
            max_events=max_events,
            sandbox=self.sandbox,
            cpu_time_seconds=cpu_time_seconds,
            address_space_mb=address_space_mb,
            file_size_mb=file_size_mb,
            process_count=process_count,
            open_files=open_files,
        )
        self._descriptors: dict[str, PluginDescriptor] = {}

    def load(self) -> tuple[PluginDescriptor, ...]:
        # A new app/test container may point at a different plugin root. Clear
        # only dynamically registered executors; built-in nodes stay untouched.
        clear_external_executors()
        if not self.root_dir.exists():
            return ()
        if not self.root_dir.is_dir():
            raise PluginLoadError(f"plugin root is not a directory: {self.root_dir}")

        discovered: dict[str, PluginDescriptor] = {}
        for manifest_path in sorted(self.root_dir.glob("*/manifest.json")):
            if not manifest_path.is_file():
                continue
            if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
                raise PluginLoadError(f"plugin manifest is too large: {manifest_path}")
            descriptor = self._read_descriptor(manifest_path)
            node_type = descriptor.manifest.node_type
            if node_type in discovered:
                raise PluginLoadError(f"duplicate plugin node type: {node_type}")
            if descriptor.manifest.plugin_id in {
                item.manifest.plugin_id for item in discovered.values()
            }:
                raise PluginLoadError(f"duplicate plugin id: {descriptor.manifest.plugin_id}")
            discovered[node_type] = descriptor

        for node_type, descriptor in discovered.items():
            register_executor_instance(
                node_type,
                PluginNodeExecutor(descriptor, self.runner),
            )
        self._descriptors = discovered
        return tuple(discovered.values())

    def descriptors(self) -> tuple[PluginDescriptor, ...]:
        return tuple(self._descriptors[key] for key in sorted(self._descriptors))

    def _read_descriptor(self, manifest_path: Path) -> PluginDescriptor:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = PluginManifest.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise PluginLoadError(f"invalid plugin manifest: {manifest_path}") from exc

        plugin_dir = manifest_path.parent.resolve()
        if plugin_dir.parent != self.root_dir:
            raise PluginLoadError("plugin manifest must be directly under the plugin root")
        entrypoint = (plugin_dir / manifest.entrypoint).resolve()
        try:
            entrypoint.relative_to(plugin_dir)
        except ValueError as exc:
            raise PluginLoadError("plugin entrypoint escapes its plugin directory") from exc
        if not entrypoint.is_file():
            raise PluginLoadError(f"plugin entrypoint is missing: {entrypoint}")
        return PluginDescriptor(manifest=manifest, directory=plugin_dir)


__all__ = ["PluginLoadError", "PluginRegistry"]
