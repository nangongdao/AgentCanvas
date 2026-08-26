"""MCP hub: owner-task connections, lazy connect + idle reaper, tools cache.

Named ``mcphub`` (not ``mcp``) to avoid shadowing the official SDK package.
"""

from app.mcphub.connection import McpConnection, McpConnectionError, McpServerSpec
from app.mcphub.manager import McpManager, McpServerScopeError, spec_from_row

__all__ = [
    "McpConnection",
    "McpConnectionError",
    "McpServerSpec",
    "McpManager",
    "McpServerScopeError",
    "spec_from_row",
]
