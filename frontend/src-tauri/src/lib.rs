//! AgentCanvas desktop shell (C9-1).
//!
//! Launch order: show the splash, resolve the backend origin (reuse an
//! explicitly provided uvicorn in dev, otherwise spawn the packaged sidecar
//! on a free loopback port), stream `/readyz` per-check progress into the
//! splash, then swap to the main window — or surface the failure with the
//! sidecar log path. A second app launch defers to the first via the
//! single-instance plugin.

use agentcanvas_desktop_supervisor as supervisor;
use std::path::PathBuf;
use std::time::Duration;
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

/// How long the shell waits for `/readyz` before surfacing failure. The
/// backend's readiness covers database, migrations, checkpointer, config,
/// vector store, and sandbox (C9 §5.3), and each check's state is streamed to
/// the splash as it changes.
const READYZ_TIMEOUT: Duration = Duration::from_secs(15);

/// Environment variable that pins an already-running backend (dev mode):
/// `AGENTCANVAS_BACKEND_URL=http://127.0.0.1:8000`.
const DEV_BACKEND_URL_ENV: &str = "AGENTCANVAS_BACKEND_URL";

/// The embedded startup window (C9 §4.1 splash): real phases from `/readyz`,
/// and on failure the diagnostics plus the sidecar log path.
const SPLASH_HTML: &str = include_str!("../splash.html");

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // A second double-click must focus the existing window, never
            // start a second backend.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            } else if let Some(window) = app.get_webview_window("splash") {
                let _ = window.set_focus();
            }
        }))
        .register_uri_scheme_protocol("splash", |_ctx, _request| {
            // The splash is embedded in the binary so it renders before any
            // backend or frontend asset exists.
            tauri::http::Response::builder()
                .header("Content-Type", "text/html; charset=utf-8")
                .header("Cache-Control", "no-store")
                .body(SPLASH_HTML.as_bytes().to_vec())
                .expect("static splash response")
        })
        .setup(|app| {
            let handle = app.handle().clone();
            build_splash_window(&handle);
            // The supervisor uses blocking IO; keep it off tauri's async
            // runtime so reqwest::blocking never panics inside a poll.
            std::thread::spawn(move || start_backend_and_swap_windows(handle));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running AgentCanvas");
}

fn emit_splash(app: &tauri::AppHandle, event: &str, payload: serde_json::Value) {
    if let Err(error) = app.emit(event, payload) {
        eprintln!("AgentCanvas: splash emit failed: {error}");
    }
}

fn start_backend_and_swap_windows(app: tauri::AppHandle) {
    let reuse = std::env::var(DEV_BACKEND_URL_ENV).ok();
    let mut sidecar: Option<supervisor::SidecarHandle> = None;
    let mut log_path: Option<PathBuf> = None;

    let started: Result<String, String> = match supervisor::resolve_backend_origin(reuse.as_deref())
    {
        Ok(supervisor::BackendOrigin::Reused(url)) => supervisor::wait_until_ready(&url, READYZ_TIMEOUT)
            .map(|_| url)
            .map_err(|failure| failure.to_string()),
        Ok(supervisor::BackendOrigin::Spawned { port }) => {
            let origin = format!("http://127.0.0.1:{port}");
            let spec = supervisor::SidecarSpec {
                program: default_sidecar_program(),
                working_dir: std::env::current_dir()
                    .unwrap_or_else(|_| PathBuf::from(".")),
            };
            match supervisor::spawn_sidecar(&spec, port, &default_data_dir()) {
                Ok(mut handle) => {
                    log_path = Some(handle.log_path.clone());
                    emit_splash(
                        &app,
                        "splash:progress",
                        serde_json::json!({ "phase": "runtime-started" }),
                    );
                    let result = supervisor::wait_until_ready_with_progress(
                        &origin,
                        READYZ_TIMEOUT,
                        |snapshot| {
                            emit_splash(
                                &app,
                                "splash:progress",
                                serde_json::json!({
                                    "phase": "checks",
                                    "status": snapshot.status,
                                    "checks": snapshot.checks,
                                }),
                            );
                        },
                        || {
                            handle
                                .try_wait()
                                .map(|exited| exited.is_none())
                                .unwrap_or(false)
                        },
                    );
                    // The handle stays owned by this thread for the process
                    // lifetime: dropping it would kill the backend.
                    sidecar = Some(handle);
                    result.map(|_| origin).map_err(|failure| failure.to_string())
                }
                Err(error) => Err(format!("无法启动后端运行时: {error}")),
            }
        }
        Err(error) => Err(format!("无法申请本地端口: {error}")),
    };

    match started {
        Ok(_) => {
            emit_splash(&app, "splash:ready", serde_json::json!({}));
            open_main_window(&app);
        }
        Err(message) => {
            // The splash stays up as the failure surface: diagnostics plus the
            // sidecar log path; the main window is not opened on a dead
            // backend (C9 §4.1 startup-UI acceptance).
            eprintln!("AgentCanvas: {message}");
            emit_splash(
                &app,
                "splash:failure",
                serde_json::json!({
                    "error": message,
                    "logPath": log_path.map(|path| path.display().to_string()),
                }),
            );
        }
    }
    // sidecar (when present) intentionally lives until process exit; dropping
    // it kills the backend (job object + explicit kill).
    std::mem::forget(sidecar);
}

fn default_sidecar_program() -> PathBuf {
    PathBuf::from("sidecar/agentcanvas-backend/agentcanvas-backend.exe")
}

fn default_data_dir() -> PathBuf {
    // %APPDATA%/AgentCanvas on Windows; XDG-equivalent elsewhere. The backend
    // derives every storage path from APP_DATA_DIR (C9 §13.1: zero backend
    // changes for the data layout).
    dirs_data_root().join("AgentCanvas")
}

#[cfg(windows)]
fn dirs_data_root() -> PathBuf {
    std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

#[cfg(not(windows))]
fn dirs_data_root() -> PathBuf {
    std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/share"))
        })
        .unwrap_or_else(|| PathBuf::from("."))
}

fn build_splash_window(app: &tauri::AppHandle) {
    let url: tauri::Url = "http://splash.localhost/index.html"
        .parse()
        .expect("splash url");
    let _ = WebviewWindowBuilder::new(app, "splash", WebviewUrl::External(url))
        .title("AgentCanvas")
        .inner_size(480.0, 340.0)
        .resizable(false)
        .build();
}

fn close_splash(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("splash") {
        let _ = window.close();
    }
}

fn open_main_window(app: &tauri::AppHandle) {
    // Dev loads the Vite server (build.devUrl); the packaged app loads the
    // embedded dist over the custom protocol. Either way the UI discovers
    // the backend origin at runtime (ADR 0003, verdict B').
    let url = if tauri::is_dev() {
        WebviewUrl::External("http://127.0.0.1:5173".parse().expect("dev url"))
    } else {
        WebviewUrl::default()
    };
    let _ = WebviewWindowBuilder::new(app, "main", url)
        .title("AgentCanvas")
        .inner_size(1440.0, 900.0)
        .min_inner_size(1024.0, 640.0)
        .build();
    close_splash(app);
}
