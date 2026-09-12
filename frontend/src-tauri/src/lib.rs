//! AgentCanvas desktop shell (C9-1).
//!
//! Launch order: resolve the backend origin (reuse an explicitly provided
//! uvicorn in dev, otherwise spawn the packaged sidecar on a free loopback
//! port), poll `/readyz` off the async runtime, then open the main window.
//! A second app launch defers to the first via the single-instance plugin.

use agentcanvas_desktop_supervisor as supervisor;
use std::time::Duration;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// How long the shell waits for `/readyz` before surfacing failure. The
/// backend's readiness covers database, migrations, checkpointer, config,
/// vector store, and sandbox (C9 §5.3); the startup-window slice will render
/// the per-check detail from the probe body.
const READYZ_TIMEOUT: Duration = Duration::from_secs(15);

/// Environment variable that pins an already-running backend (dev mode):
/// `AGENTCANVAS_BACKEND_URL=http://127.0.0.1:8000`.
const DEV_BACKEND_URL_ENV: &str = "AGENTCANVAS_BACKEND_URL";

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // A second double-click must focus the existing window, never
            // start a second backend.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let handle = app.handle().clone();
            // The supervisor uses blocking IO; keep it off tauri's async
            // runtime so reqwest::blocking never panics inside a poll.
            std::thread::spawn(move || start_backend_and_open_window(handle));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running AgentCanvas");
}

fn start_backend_and_open_window(app: tauri::AppHandle) {
    let reuse = std::env::var(DEV_BACKEND_URL_ENV).ok();
    let mut sidecar = None;
    let backend_ready = match supervisor::resolve_backend_origin(reuse.as_deref()) {
        Ok(supervisor::BackendOrigin::Reused(url)) => {
            supervisor::wait_until_ready(&url, READYZ_TIMEOUT).map(|_| url)
        }
        Ok(supervisor::BackendOrigin::Spawned { port }) => {
            let origin = format!("http://127.0.0.1:{port}");
            let spec = supervisor::SidecarSpec {
                program: default_sidecar_program(),
                working_dir: std::env::current_dir()
                    .unwrap_or_else(|_| std::path::PathBuf::from(".")),
            };
            match supervisor::spawn_sidecar(&spec, port, &default_data_dir()) {
                Ok(handle) => {
                    // Keep the handle for the process lifetime: dropping it
                    // kills the backend (job object + explicit kill).
                    let outcome = supervisor::wait_until_ready(&origin, READYZ_TIMEOUT);
                    sidecar = Some(handle);
                    outcome.map(|_| origin)
                }
                Err(error) => Err(format!("failed to start the backend sidecar: {error}")),
            }
        }
        Err(error) => Err(format!("failed to reserve a port: {error}")),
    };

    if let Err(error) = backend_ready {
        // The startup-window slice replaces this with a real failure surface;
        // for now the shell still opens so the user sees the UI fail loudly
        // instead of staring at nothing.
        eprintln!("AgentCanvas: {error}");
    }
    open_main_window(app);
    // sidecar (when present) intentionally lives until process exit.
    std::mem::forget(sidecar);
}

fn default_sidecar_program() -> std::path::PathBuf {
    std::path::PathBuf::from("sidecar/agentcanvas-backend/agentcanvas-backend.exe")
}

fn default_data_dir() -> std::path::PathBuf {
    // %APPDATA%/AgentCanvas on Windows; XDG-equivalent elsewhere. The backend
    // derives every storage path from APP_DATA_DIR (C9 §13.1: zero backend
    // changes for the data layout).
    dirs_data_root().join("AgentCanvas")
}

#[cfg(windows)]
fn dirs_data_root() -> std::path::PathBuf {
    std::env::var_os("APPDATA")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from("."))
}

#[cfg(not(windows))]
fn dirs_data_root() -> std::path::PathBuf {
    std::env::var_os("XDG_DATA_HOME")
        .map(std::path::PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME")
                .map(|home| std::path::PathBuf::from(home).join(".local/share"))
        })
        .unwrap_or_else(|| std::path::PathBuf::from("."))
}

fn open_main_window(app: tauri::AppHandle) {
    // Dev loads the Vite server (build.devUrl); the packaged app loads the
    // embedded dist over the custom protocol. Either way the UI discovers
    // the backend origin at runtime (ADR 0003, verdict B').
    let url = if tauri::is_dev() {
        WebviewUrl::External("http://127.0.0.1:5173".parse().expect("dev url"))
    } else {
        WebviewUrl::default()
    };
    let _ = WebviewWindowBuilder::new(&app, "main", url)
        .title("AgentCanvas")
        .inner_size(1440.0, 900.0)
        .min_inner_size(1024.0, 640.0)
        .build();
}
