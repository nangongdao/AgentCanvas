"""Versioned, process-isolated node plugin SDK."""

from app.plugins.executor import PluginNodeExecutor
from app.plugins.protocol import (
    PLUGIN_API_VERSION,
    PLUGIN_PROTOCOL_VERSION,
    PluginDescriptor,
    PluginEvent,
    PluginManifest,
    PluginPermissions,
    PluginRequest,
    PluginResponse,
    PluginUiHints,
)
from app.plugins.registry import PluginLoadError, PluginRegistry
from app.plugins.runner import PluginProcessError, PluginProcessRunner, PluginRunResult

__all__ = [
    "PLUGIN_API_VERSION",
    "PLUGIN_PROTOCOL_VERSION",
    "PluginDescriptor",
    "PluginEvent",
    "PluginLoadError",
    "PluginManifest",
    "PluginNodeExecutor",
    "PluginPermissions",
    "PluginProcessError",
    "PluginProcessRunner",
    "PluginRegistry",
    "PluginRequest",
    "PluginResponse",
    "PluginRunResult",
    "PluginUiHints",
]
