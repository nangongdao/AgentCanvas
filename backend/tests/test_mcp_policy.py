"""MCP stdio process policy tests."""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from app.core.config import BACKEND_DIR, Settings
from app.core.mcp_policy import McpStdioPolicy, StdioCommandDenied
from app.main import create_app
from tests.test_auth import ADMIN_TOKEN, auth_settings, bearer


def policy(tmp_path, *, packages: tuple[str, ...] = ()) -> McpStdioPolicy:
    return McpStdioPolicy.from_settings(
        Settings(
            data_dir=tmp_path,
            mcp_stdio_allowed_commands=("python.exe", "python", "npx"),
            mcp_stdio_allowed_roots=(tmp_path,),
            mcp_stdio_allowed_packages=packages,
        )
    )


def test_python_script_must_exist_under_allowed_root(tmp_path) -> None:
    script = tmp_path / "server.py"
    script.write_text("print('server')", encoding="utf-8")
    command_policy = policy(tmp_path)

    command_policy.validate(sys.executable, [str(script)])

    with pytest.raises(StdioCommandDenied, match="inline/module"):
        command_policy.validate(sys.executable, ["-c", "print(1)"])
    outside = tmp_path.parent / "outside.py"
    outside.write_text("print('outside')", encoding="utf-8")
    with pytest.raises(StdioCommandDenied, match="outside"):
        command_policy.validate(sys.executable, [str(outside)])


def test_command_and_npx_package_require_explicit_allowlist(tmp_path) -> None:
    command_policy = policy(tmp_path, packages=("@example/mcp",))

    command_policy.validate("npx", ["-y", "@example/mcp"])

    with pytest.raises(StdioCommandDenied, match="ALLOWED_PACKAGES"):
        command_policy.validate("npx", ["other-package"])
    with pytest.raises(StdioCommandDenied, match="ALLOWED_COMMANDS"):
        command_policy.validate("powershell.exe", ["-Command", "Get-ChildItem"])


def test_stdio_policy_is_enforced_by_api(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        denied = client.post(
            "/api/mcp/servers",
            headers=bearer(ADMIN_TOKEN),
            json={
                "name": "Denied shell",
                "transport": "stdio",
                "command": "powershell.exe",
                "args": ["-Command", "Get-ChildItem"],
            },
        )
        assert denied.status_code == 422
        assert "MCP_STDIO_ALLOWED_COMMANDS" in denied.text

        allowed = client.post(
            "/api/mcp/servers",
            headers=bearer(ADMIN_TOKEN),
            json={
                "name": "Allowed calculator",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(BACKEND_DIR / "mcp_servers" / "calculator.py")],
            },
        )
        assert allowed.status_code == 201, allowed.text
