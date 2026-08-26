"""Allowlist policy for MCP stdio child processes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings


class StdioCommandDenied(ValueError):
    """Raised when a configured stdio command falls outside the allowlist."""


@dataclass(frozen=True)
class McpStdioPolicy:
    allowed_commands: frozenset[str]
    allowed_roots: tuple[Path, ...]
    allowed_packages: frozenset[str]

    @classmethod
    def from_settings(cls, settings: Settings) -> McpStdioPolicy:
        return cls(
            allowed_commands=frozenset(
                command.casefold() for command in settings.mcp_stdio_allowed_commands
            ),
            allowed_roots=tuple(root.resolve() for root in settings.mcp_stdio_allowed_roots),
            allowed_packages=frozenset(settings.mcp_stdio_allowed_packages),
        )

    def validate(self, command: str | None, args: list[str] | tuple[str, ...]) -> None:
        if not command:
            raise StdioCommandDenied("stdio transport requires a command")
        command_path = Path(command)
        command_name = command_path.name.casefold()
        normalized = str(command_path).casefold()
        if (
            command_name not in self.allowed_commands
            and normalized not in self.allowed_commands
        ):
            raise StdioCommandDenied(
                f"stdio command '{command}' is not in MCP_STDIO_ALLOWED_COMMANDS"
            )

        values = [str(value) for value in args]
        if command_name in {"python", "python.exe", "python3", "python3.exe"}:
            self._validate_script(values, {".py"}, "Python")
        elif command_name in {"node", "node.exe"}:
            self._validate_script(values, {".js", ".mjs", ".cjs"}, "Node")
        elif command_name in {"npx", "npx.cmd", "npm", "npm.cmd"}:
            self._validate_package(values)

    def _validate_script(
        self, args: list[str], suffixes: set[str], runtime: str
    ) -> None:
        if any(argument in {"-c", "-m", "--eval", "-e"} for argument in args):
            raise StdioCommandDenied(f"{runtime} inline/module execution is not allowed")
        script = next((argument for argument in args if not argument.startswith("-")), "")
        if not script:
            raise StdioCommandDenied(f"{runtime} stdio command requires a script path")
        resolved = Path(script).resolve()
        if resolved.suffix.casefold() not in suffixes:
            raise StdioCommandDenied(f"{runtime} MCP script has an unsupported extension")
        if not resolved.is_file():
            raise StdioCommandDenied(f"MCP script does not exist: {resolved}")
        if not any(resolved.is_relative_to(root) for root in self.allowed_roots):
            raise StdioCommandDenied(
                f"MCP script '{resolved}' is outside MCP_STDIO_ALLOWED_ROOTS"
            )

    def _validate_package(self, args: list[str]) -> None:
        candidates = [argument for argument in args if not argument.startswith("-")]
        if candidates and candidates[0] == "exec":
            candidates = candidates[1:]
        package = candidates[0] if candidates else ""
        if not package or package not in self.allowed_packages:
            raise StdioCommandDenied(
                f"MCP package '{package or '<missing>'}' is not in "
                "MCP_STDIO_ALLOWED_PACKAGES"
            )
