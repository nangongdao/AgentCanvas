"""Demo MCP server: sandboxed filesystem (stdio transport).

All operations are restricted to the workspace directory
(``AGENTCANVAS_WORKSPACE`` env var, default ``./data/workspace``). Paths are
resolved and prefix-checked to prevent directory traversal.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("filesystem")

_WORKSPACE = Path(
    os.environ.get("AGENTCANVAS_WORKSPACE", "./data/workspace")
).resolve()
_WORKSPACE.mkdir(parents=True, exist_ok=True)

_MAX_READ_BYTES = 256 * 1024


def _safe_path(relative: str) -> Path:
    """Resolve a path inside the sandbox; reject traversal attempts."""
    candidate = (_WORKSPACE / relative).resolve()
    if candidate != _WORKSPACE and _WORKSPACE not in candidate.parents:
        raise PermissionError(f"path escapes sandbox: {relative}")
    return candidate


@mcp.tool()
def list_files(directory: str = ".") -> str:
    """List files and directories under a sandbox-relative directory."""
    root = _safe_path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {directory}")
    lines = []
    for entry in sorted(root.iterdir()):
        kind = "dir " if entry.is_dir() else "file"
        size = entry.stat().st_size if entry.is_file() else 0
        lines.append(f"{kind}  {entry.relative_to(_WORKSPACE)}  {size}B")
    return "\n".join(lines) or "(empty)"


@mcp.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 text file (max 256KB) from the sandbox."""
    target = _safe_path(path)
    if not target.is_file():
        raise FileNotFoundError(f"not a file: {path}")
    if target.stat().st_size > _MAX_READ_BYTES:
        raise ValueError(f"file too large (> {_MAX_READ_BYTES} bytes): {path}")
    return target.read_text(encoding="utf-8", errors="replace")


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write UTF-8 text to a file in the sandbox (creates parent dirs)."""
    target = _safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content.encode('utf-8'))} bytes to {path}"


if __name__ == "__main__":
    mcp.run()
