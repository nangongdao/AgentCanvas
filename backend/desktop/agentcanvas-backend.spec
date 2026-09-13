# -*- mode: python ; coding: utf-8 -*-
"""AgentCanvas desktop sidecar bundle (C9 §3.3/§13.4).

Build (from backend/):

    uv run --with pyinstaller pyinstaller desktop/agentcanvas-backend.spec \
        --distpath desktop/dist --workpath desktop/build

Produces a onedir bundle at desktop/dist/agentcanvas-backend/. The shell
launches desktop/dist/agentcanvas-backend/agentcanvas-backend.exe with
APP_PROFILE=desktop, which forces VECTOR_BACKEND=sql — so the whole chromadb
dependency tree (chromadb/onnxruntime/kubernetes/numpy/grpc/tokenizers,
≈160MB) is excluded here and never imported. onedir over onefile: no
temp-extraction on every start, which is what keeps the <5s cold-start gate
reachable.
"""

import os

# PyInstaller resolves Analysis paths relative to the CWD, not the spec file;
# anchor everything to the backend root so the build works from any CWD.
BACKEND_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(BACKEND_ROOT, "desktop_sidecar.py")],
    pathex=[BACKEND_ROOT],
    binaries=[],
    # The backend reads alembic revisions and the MCP demo servers at runtime;
    # ship them inside the bundle next to the executable.
    datas=[
        (os.path.join(BACKEND_ROOT, "alembic"), "alembic"),
        (os.path.join(BACKEND_ROOT, "alembic.ini"), "."),
        (os.path.join(BACKEND_ROOT, "mcp_servers"), "mcp_servers"),
    ],
    hiddenimports=[
        # uvicorn resolves loop/protocol/lifespan implementations dynamically;
        # PyInstaller cannot see those string-based imports.
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        # aiosqlite is the desktop database driver (desktop profile pins
        # sqlite+aiosqlite) and platformdirs backs the data-root defaults.
        "aiosqlite",
    ],
    excludes=[
        # The chromadb tree — the desktop vector backend is sql.
        "chromadb",
        "onnxruntime",
        "kubernetes",
        "numpy",
        "grpc",
        "tokenizers",
        # Build/test-only surface that must never ride along.
        "pytest",
        "tkinter",
        "IPython",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="agentcanvas-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="agentcanvas-backend",
)
