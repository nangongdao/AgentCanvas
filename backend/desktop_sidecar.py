"""Desktop sidecar entry point (C9-1).

The Tauri shell spawns this executable with exactly two injected variables —
APP_PORT (a loopback port reserved by the supervisor) and APP_DATA_DIR (the
per-user data root) — plus APP_PROFILE=desktop, which freezes every other
degradation in app.core.config (SQLite, single process, no Redis, SQL vector
backend). Keeping this entry tiny means PyInstaller's analysis surface stays
predictable and the shell's readiness probe can watch uvicorn come up.

The server is started through ``uvicorn.Config`` + ``uvicorn.Server`` rather
than ``uvicorn.run``: under the frozen build ``uvicorn.run`` exited 1
silently during application startup, while the explicit server form comes up
cleanly and reproduces across runs. ``faulthandler`` stays enabled so a
native-level failure still leaves frames behind for the operator.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import traceback


def main() -> None:
    # A frozen sidecar that dies without a traceback is undebuggable for the
    # operator (C9 §11 startup-failure risk); dump native frames on fatal
    # signals and print Python-level failures explicitly.
    faulthandler.enable()
    try:
        import uvicorn

        from app.main import create_app

        host = os.environ.get("APP_HOST", "127.0.0.1")
        port = int(os.environ.get("APP_PORT", "8000"))
        print(f"[sidecar] starting uvicorn on {host}:{port}", file=sys.stderr, flush=True)
        config = uvicorn.Config(create_app(), host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        server.run()
        print(f"[sidecar] uvicorn exited (should_exit={server.should_exit})",
              file=sys.stderr, flush=True)
    except BaseException:
        traceback.print_exc()
        sys.stderr.flush()
        raise


if __name__ == "__main__":
    main()
