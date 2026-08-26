"""Sandboxed subprocess execution for the Code node (C2-2).

Runs user-supplied Python source in a fresh process wrapped by the C8-1
``SandboxBackend``. The contract is intentionally minimal and mirrors the
plugin JSONL protocol's spirit but is purpose-built for inline code:

* the rendered ``inputs`` mapping is JSON-encoded on stdin
* the source reads it (via ``json.load(sys.stdin)`` convention) and writes a
  single JSON value to stdout
* stdout is parsed and returned as the node output

The sandbox enforces deny-by-default network/filesystem and rlimit caps; the
caller cannot relax them except through the declared ``CodeConfig`` opt-ins,
which are translated into a ``SandboxProfile`` here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.sandbox import (
    NoSandbox,
    PreparedCommand,
    SandboxBackend,
    SandboxProfile,
)

logger = logging.getLogger(__name__)

# A small harness wraps user source so the stdin JSON object is exposed as
# ``inputs`` and the user's ``output`` binding is serialized to stdout. The
# user source is exec'd in a namespace that already has ``inputs`` and ends
# with ``output`` in scope; if the source never sets ``output`` it is None.
_HARNESS_PREFIX = "import json, sys as _sys\ninputs = json.load(_sys.stdin)\n"
_HARNESS_SUFFIX = (
    "\noutput = globals().get('output')\n"
    "_sys.stdout.write(json.dumps(output, ensure_ascii=False, separators=(',', ':')))\n"
    "_sys.stdout.flush()\n"
)

MAX_OUTPUT_BYTES = 256 * 1024
MAX_SOURCE_BYTES = 64 * 1024


class CodeExecutionError(RuntimeError):
    """Raised when sandboxed code fails, times out, or exceeds limits."""


@dataclass(frozen=True)
class CodeRunResult:
    output: Any
    stdout_preview: str


def _build_profile(
    *,
    memory_limit_mb: int,
    process_count: int,
    allow_network: bool,
    allow_filesystem: list[str],
    cpu_time_seconds: int = 30,
) -> SandboxProfile:
    return SandboxProfile(
        network=allow_network,
        writable_roots=tuple(sorted(set(allow_filesystem))),
        cpu_time_seconds=cpu_time_seconds,
        address_space_mb=memory_limit_mb,
        file_size_mb=32,
        process_count=process_count,
        open_files=64,
    )


async def run_code(
    source: str,
    inputs: dict[str, Any],
    *,
    timeout_seconds: float,
    memory_limit_mb: int,
    process_count: int,
    allow_network: bool,
    allow_filesystem: list[str],
    sandbox: SandboxBackend | None,
) -> CodeRunResult:
    """Execute rendered source under the sandbox and return its parsed output.

    ``sandbox=None`` defaults to the no-isolation test backend — but the node
    executor refuses to run when the active backend is ``NoSandbox`` unless the
    process explicitly allowed it, so production never lands here unsandboxed.
    """
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise CodeExecutionError("code source exceeds the configured byte limit")

    backend = sandbox if sandbox is not None else NoSandbox()
    profile = _build_profile(
        memory_limit_mb=memory_limit_mb,
        process_count=process_count,
        allow_network=allow_network,
        allow_filesystem=allow_filesystem,
        cpu_time_seconds=max(int(timeout_seconds) + 5, 5),
    )
    status = backend.describe()
    if status.get("degraded") and status.get("backend") == "none":
        # NoSandbox is only reachable in tests; the node executor gates this.
        logger.warning("code node running under NoSandbox (no OS isolation)")

    rendered = _HARNESS_PREFIX + source + _HARNESS_SUFFIX

    # Write source to a temp file the subprocess executes. The file lives under
    # the host fs; under an OS-level sandbox it is bind-mounted read-only into
    # the child. delete=False is required because the child reads the path
    # after the handle is closed; cleanup is best-effort in the finally block.
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — see comment above
        mode="w",
        suffix=".py",
        encoding="utf-8",
        delete=False,
    )
    try:
        tmp.write(rendered)
        tmp.flush()
        tmp.close()
        script_path = tmp.name

        base_env = {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        prepared: PreparedCommand = backend.prepare(
            argv=[sys.executable, script_path],
            env=base_env,
            cwd=str(Path(script_path).parent),
            profile=profile,
        )

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = await asyncio.create_subprocess_exec(
            *prepared.runner_argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(script_path).parent),
            env=prepared.runner_env,
            creationflags=creationflags,
            limit=MAX_OUTPUT_BYTES + 1,
        )

        stdin_payload = json.dumps(inputs, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            async with asyncio.timeout(timeout_seconds):
                assert process.stdin is not None
                assert process.stdout is not None
                assert process.stderr is not None
                process.stdin.write(stdin_payload)
                await process.stdin.drain()
                process.stdin.close()
                stdout_bytes, stderr_bytes = await process.communicate()
        except TimeoutError as exc:
            raise CodeExecutionError("code node timed out") from exc
        finally:
            await _terminate(process)

        if process.returncode != 0:
            err = stderr_bytes.decode("utf-8", errors="replace")[:1000]
            raise CodeExecutionError(f"code process exited {process.returncode}: {err}")

        if len(stdout_bytes) > MAX_OUTPUT_BYTES:
            raise CodeExecutionError("code output exceeds the configured byte limit")

        text = stdout_bytes.decode("utf-8", errors="replace")
        try:
            output = json.loads(text) if text.strip() else None
        except json.JSONDecodeError as exc:
            raise CodeExecutionError(
                f"code output was not valid JSON: {exc}; stdout preview: {text[:400]!r}"
            ) from exc
        return CodeRunResult(output=output, stdout_preview=text[:1000])
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)


async def _terminate(process: asyncio.subprocess.Process) -> None:
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


__all__ = [
    "CodeExecutionError",
    "CodeRunResult",
    "MAX_OUTPUT_BYTES",
    "MAX_SOURCE_BYTES",
    "run_code",
]
