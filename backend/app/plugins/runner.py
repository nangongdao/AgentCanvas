"""Bounded JSONL subprocess execution for node plugins."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from app.core.sandbox import (
    PreparedCommand,
    ProcessCleanupSandbox,
    SandboxBackend,
    SandboxProfile,
    profile_from_permissions,
)
from app.plugins.protocol import PluginDescriptor, PluginEvent, PluginRequest, PluginResponse


class PluginProcessError(RuntimeError):
    """Raised when a plugin violates the process or protocol contract."""


@dataclass(frozen=True)
class PluginRunResult:
    output: Any
    events: tuple[PluginEvent, ...]


class PluginProcessRunner:
    """Run one plugin request in a fresh process with explicit lifecycle cleanup.

    The subprocess runs under a ``SandboxBackend`` (C8-1). On Linux with nsjail
    or bubblewrap installed the plugin's declared permissions become mandatory
    OS-level enforcement — an undeclared network/filesystem access is denied by
    the namespace/mount configuration, not merely audited. On Windows or images
    without the wrapper tool the backend degrades to process cleanup (empty env,
    piped stdio, byte/timeout caps) and surfaces as degraded in readiness. The
    JSONL protocol, byte limits, and event caps are independent of the sandbox
    and unchanged from the pre-C8-1 path.
    """

    def __init__(
        self,
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
        self.max_input_bytes = max_input_bytes
        self.max_output_bytes = max_output_bytes
        self.max_events = max_events
        self.sandbox = sandbox if sandbox is not None else ProcessCleanupSandbox()
        self.cpu_time_seconds = cpu_time_seconds
        self.address_space_mb = address_space_mb
        self.file_size_mb = file_size_mb
        self.process_count = process_count
        self.open_files = open_files

    def sandbox_status(self) -> dict[str, Any]:
        """Expose the active sandbox backend for readiness/UI."""
        return self.sandbox.describe()

    def _profile_for(self, descriptor: PluginDescriptor) -> SandboxProfile:
        return profile_from_permissions(
            descriptor.manifest.permissions,
            cpu_time_seconds=self.cpu_time_seconds,
            address_space_mb=self.address_space_mb,
            file_size_mb=self.file_size_mb,
            process_count=self.process_count,
            open_files=self.open_files,
        )

    async def run(self, descriptor: PluginDescriptor, request: PluginRequest) -> PluginRunResult:
        encoded = self._encode_request(request)
        if len(encoded) > self.max_input_bytes:
            raise PluginProcessError("plugin input exceeds the configured byte limit")

        entrypoint = descriptor.entrypoint
        if not entrypoint.is_file():
            raise PluginProcessError("plugin entrypoint is missing")
        timeout = descriptor.manifest.timeout_seconds
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        # The sandbox wraps the interpreter+entrypoint argv. OS-level backends
        # prepend nsjail/bwrap (network namespace, read-only rootfs, rlimits);
        # the cleanup backend returns the argv unchanged. The base environment is
        # the deny-by-default minimal env in both cases — the sandbox does not
        # widen it, only (when enforcing) further constrains the namespace.
        base_env = {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        prepared: PreparedCommand = self.sandbox.prepare(
            argv=[sys.executable, str(entrypoint)],
            env=base_env,
            cwd=str(descriptor.directory),
            profile=self._profile_for(descriptor),
        )

        process = await asyncio.create_subprocess_exec(
            *prepared.runner_argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(descriptor.directory),
            env=prepared.runner_env,
            creationflags=creationflags,
            limit=self.max_output_bytes + 1,
        )
        try:
            async with asyncio.timeout(timeout):
                assert process.stdin is not None
                assert process.stdout is not None
                process.stdin.write(encoded)
                await process.stdin.drain()
                process.stdin.close()
                first_line = await process.stdout.readline()
                trailing = await process.stdout.read(self.max_output_bytes + 1)
                await process.wait()
            if process.returncode != 0:
                raise PluginProcessError("plugin process exited unsuccessfully")
            raw = first_line + trailing
            if len(raw) > self.max_output_bytes:
                raise PluginProcessError("plugin output exceeds the configured byte limit")
            if trailing.strip():
                raise PluginProcessError("plugin emitted more than one JSONL response")
            response = self._decode_response(raw, request, descriptor)
            if len(response.events) > self.max_events:
                raise PluginProcessError("plugin emitted too many events")
            if not response.ok:
                raise PluginProcessError(response.error or "plugin returned an error")
            return PluginRunResult(output=response.output, events=tuple(response.events))
        except TimeoutError as exc:
            raise PluginProcessError("plugin process timed out") from exc
        except ValueError as exc:
            raise PluginProcessError("plugin output exceeds the configured line limit") from exc
        finally:
            await self._stop(process)

    @staticmethod
    def _encode_request(request: PluginRequest) -> bytes:
        try:
            return (
                json.dumps(
                    request.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError) as exc:
            raise PluginProcessError("plugin request is not JSON serializable") from exc

    @staticmethod
    def _decode_response(
        raw: bytes,
        request: PluginRequest,
        descriptor: PluginDescriptor,
    ) -> PluginResponse:
        if not raw.strip():
            raise PluginProcessError("plugin returned an empty response")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginProcessError("plugin returned an invalid JSONL response") from exc
        if not isinstance(payload, dict):
            raise PluginProcessError("plugin returned an invalid JSONL response")
        if payload.get("protocol_version") != descriptor.manifest.protocol_version:
            raise PluginProcessError("plugin protocol version is incompatible")
        if payload.get("request_id") != request.request_id:
            raise PluginProcessError("plugin response request_id does not match")
        try:
            response = PluginResponse.model_validate(payload)
        except ValueError as exc:
            raise PluginProcessError("plugin returned an invalid response contract") from exc
        return response

    @staticmethod
    async def _stop(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except (TimeoutError, ProcessLookupError):
            return
