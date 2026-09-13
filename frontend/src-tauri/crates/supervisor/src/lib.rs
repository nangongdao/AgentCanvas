//! Sidecar supervision for the AgentCanvas desktop shell (C9-1): start the
//! embedded backend, wait for readiness, and guarantee process teardown.
//!
//! Deliberately independent of Tauri so the state machine and IO stay
//! unit-testable without the webview stack; `agentcanvas_desktop_lib` wires
//! it into the Tauri lifecycle.

mod job;

use std::io;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Child;
use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub struct SidecarSpec {
    /// Path of the packaged backend executable (PyInstaller onedir entry).
    pub program: PathBuf,
    /// Working directory for the sidecar process (the bundle's data root).
    pub working_dir: PathBuf,
}

/// A spawned sidecar plus the Windows job object that guarantees it dies with
/// the shell. Dropping the guard terminates the child, so every exit path —
/// normal quit, panic, or crash — reaps the backend.
pub struct SidecarHandle {
    child: Option<Child>,
    _job: Option<crate::job::JobGuard>,
    /// File the sidecar's stdout/stderr stream into (`<data>/logs/sidecar.log`);
    /// the failure surface points the operator here.
    pub log_path: PathBuf,
}

impl SidecarHandle {
    /// Process id, used by the startup window to attribute logs.
    #[allow(dead_code)]
    pub fn id(&self) -> u32 {
        self.child.as_ref().expect("sidecar child").id()
    }

    /// Poll whether the child has exited without blocking.
    pub fn try_wait(&mut self) -> io::Result<Option<std::process::ExitStatus>> {
        self.child.as_mut().expect("sidecar child").try_wait()
    }
}

impl Drop for SidecarHandle {
    fn drop(&mut self) {
        // Dropping the job guard terminates the job on Windows first; the
        // explicit kill keeps non-Windows platforms correct.
        if let Some(child) = self.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// Reserve an ephemeral loopback port for the sidecar. Binding port 0 and
/// reading the assigned port avoids clashes with a concurrently running dev
/// server on 8000.
pub fn pick_free_port() -> io::Result<u16> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    // Dropping the listener releases the port; the sidecar rebinds it. The
    // tiny race window is acceptable — readiness probing catches failure.
    drop(listener);
    Ok(port)
}

/// Where the sidecar's output streams land for a given data directory.
pub fn sidecar_log_path(data_dir: &Path) -> PathBuf {
    data_dir.join("logs").join("sidecar.log")
}

/// One `/readyz` observation, reduced to what the startup window renders.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadyzSnapshot {
    /// `"ready"` once every blocking check passes, else `"unavailable"`.
    pub status: String,
    /// Check name → state (e.g. `("migrations", "ready")`), in report order.
    pub checks: Vec<(String, String)>,
}

impl ReadyzSnapshot {
    fn from_body(body: &serde_json::Value) -> Option<Self> {
        let status = body.get("status")?.as_str()?.to_string();
        let mut checks = Vec::new();
        if let Some(map) = body.get("checks").and_then(|c| c.as_object()) {
            for (name, value) in map {
                if let Some(state) = value.get("state").and_then(|s| s.as_str()) {
                    checks.push((name.clone(), state.to_string()));
                }
            }
        }
        Some(Self { status, checks })
    }
}

/// Why the sidecar is not serving. `ChildExited` fails fast instead of
/// polling a process that is already gone (e.g. a migration failure).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StartupFailure {
    Timeout { base_url: String, last: Option<ReadyzSnapshot> },
    ChildExited { code: Option<i32> },
}

impl std::fmt::Display for StartupFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StartupFailure::Timeout { base_url, last } => {
                write!(f, "backend at {base_url} did not become ready in time")?;
                if let Some(snapshot) = last {
                    write!(f, "; last status: {}", snapshot.status)?;
                }
                Ok(())
            }
            StartupFailure::ChildExited { code } => {
                write!(f, "backend process exited early (code {})", code.unwrap_or(-1))
            }
        }
    }
}

/// One `/readyz` probe: `Some(snapshot)` on any HTTP response (200 or 503 —
/// the body carries the per-check report either way), `None` when the
/// backend is not accepting connections yet.
pub fn probe_once(base_url: &str) -> Option<ReadyzSnapshot> {
    let probe_url = format!("{base_url}/readyz");
    let outcome = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .and_then(|client| client.get(&probe_url).send());
    match outcome {
        Ok(response) => response
            .json::<serde_json::Value>()
            .ok()
            .as_ref()
            .and_then(ReadyzSnapshot::from_body),
        Err(_) => None,
    }
}

/// Poll `/readyz` until every blocking check passes, the deadline expires, or
/// the child exits early. This is the Rust-side mirror of the C9 plan §5.3:
/// the backend's `/readyz` already reports database, migrations, checkpointer,
/// config, vector store, and sandbox, and the snapshot stream is what the
/// startup window renders. `on_progress` fires once per successful probe;
/// `child_running` should report `false` once the sidecar process is gone so
/// a migration failure fails fast instead of polling a dead process.
pub fn wait_until_ready_with_progress(
    base_url: &str,
    timeout: Duration,
    mut on_progress: impl FnMut(ReadyzSnapshot),
    mut child_running: impl FnMut() -> bool,
) -> Result<ReadyzSnapshot, StartupFailure> {
    let deadline = Instant::now() + timeout;
    let mut last = None;
    loop {
        if let Some(snapshot) = probe_once(base_url) {
            if snapshot.status == "ready" {
                return Ok(snapshot);
            }
            on_progress(snapshot.clone());
            last = Some(snapshot);
        }
        if !child_running() {
            return Err(StartupFailure::ChildExited { code: None });
        }
        if Instant::now() >= deadline {
            return Err(StartupFailure::Timeout {
                base_url: base_url.to_string(),
                last,
            });
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

/// Blocking convenience wrapper for callers that have no child to monitor.
pub fn wait_until_ready(
    base_url: &str,
    timeout: Duration,
) -> Result<ReadyzSnapshot, StartupFailure> {
    wait_until_ready_with_progress(base_url, timeout, |_| {}, || true)
}

/// Spawn the sidecar with the desktop profile env. The caller receives a
/// handle whose drop kills the process tree.
pub fn spawn_sidecar(spec: &SidecarSpec, port: u16, data_dir: &Path) -> io::Result<SidecarHandle> {
    spawn_command(&spec.program, &spec.working_dir, port, data_dir, &[])
}

/// Lower-level spawn used by production (`spawn_sidecar`, no extra args) and
/// by tests (which drive `cmd /c ...` to observe the injected environment and
/// the kill-on-drop guarantee).
pub fn spawn_command(
    program: &Path,
    working_dir: &Path,
    port: u16,
    data_dir: &Path,
    args: &[&str],
) -> io::Result<SidecarHandle> {
    let log_path = sidecar_log_path(data_dir);
    if let Some(parent) = log_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let log_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)?;
    let stderr_file = log_file.try_clone()?;
    let mut command = std::process::Command::new(program);
    command
        .current_dir(working_dir)
        .args(args)
        .env("APP_PROFILE", "desktop")
        .env("APP_HOST", "127.0.0.1")
        .env("APP_PORT", port.to_string())
        .env("APP_DATA_DIR", data_dir)
        .env("STARTUP_MIGRATIONS", "true")
        .stdout(log_file)
        .stderr(stderr_file);
    #[cfg(windows)]
    {
        // Prevent a detached console window from flashing on launch.
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let child = command.spawn()?;
    let job = crate::job::JobGuard::for_child(&child);
    Ok(SidecarHandle {
        child: Some(child),
        _job: job,
        log_path,
    })
}

/// Resolve the backend origin for this launch. `AGENTCANVAS_BACKEND_URL`
/// (dev mode) reuses an already-running uvicorn; otherwise the sidecar is
/// spawned on a freshly reserved loopback port.
pub enum BackendOrigin {
    Reused(String),
    Spawned { port: u16 },
}

pub fn resolve_backend_origin(reuse: Option<&str>) -> io::Result<BackendOrigin> {
    match reuse {
        Some(url) if !url.trim().is_empty() => Ok(BackendOrigin::Reused(
            url.trim().trim_end_matches('/').to_string(),
        )),
        _ => Ok(BackendOrigin::Spawned { port: pick_free_port()? }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpStream;

    /// Minimal HTTP/1.1 server for one connection: replies 503 `times` with a
    /// checks-bearing body, then 200 with `"ready"`.
    struct FakeReadyz {
        port: u16,
        handle: Option<std::thread::JoinHandle<()>>,
    }

    impl FakeReadyz {
        fn start(failures_first: usize) -> Self {
            let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind");
            let port = listener.local_addr().expect("addr").port();
            let handle = std::thread::spawn(move || {
                for index in 0..(failures_first + 1) {
                    let Ok((mut stream, _)) = listener.accept() else {
                        return;
                    };
                    let mut buffer = [0u8; 2048];
                    let _ = stream.read(&mut buffer);
                    let (status, body) = if index < failures_first {
                        (
                            "503",
                            r#"{"status":"unavailable","checks":{"database":{"state":"starting"},"migrations":{"state":"starting"}}}"#,
                        )
                    } else {
                        (
                            "200",
                            r#"{"status":"ready","checks":{"database":{"state":"ready"},"migrations":{"state":"ready"}}}"#,
                        )
                    };
                    let response = format!(
                        "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                        body.len()
                    );
                    let _ = stream.write_all(response.as_bytes());
                    let _ = stream.flush();
                }
            });
            Self {
                port,
                handle: Some(handle),
            }
        }

        fn url(&self) -> String {
            format!("http://127.0.0.1:{}", self.port)
        }
    }

    impl Drop for FakeReadyz {
        fn drop(&mut self) {
            // Unblock the accept loop.
            if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", self.port)) {
                let _ = stream.write_all(b"quit");
            }
            if let Some(handle) = self.handle.take() {
                let _ = handle.join();
            }
        }
    }

    #[test]
    fn picks_distinct_free_ports() {
        let first = pick_free_port().expect("first port");
        let second = pick_free_port().expect("second port");
        assert_ne!(first, 0);
        assert_ne!(second, 0);
    }

    #[test]
    fn wait_until_ready_succeeds_after_transient_failures() {
        let server = FakeReadyz::start(2);
        let snapshot = wait_until_ready(&server.url(), Duration::from_secs(10)).expect("ready");
        assert_eq!(snapshot.status, "ready");
        assert!(snapshot
            .checks
            .iter()
            .any(|(name, state)| name == "migrations" && state == "ready"));
    }

    #[test]
    fn wait_until_ready_times_out_when_never_ready() {
        let started = Instant::now();
        // Nothing listens on this port: every probe fails until the deadline.
        let result = wait_until_ready("http://127.0.0.1:1", Duration::from_millis(600));
        match result.expect_err("must time out") {
            StartupFailure::Timeout { base_url, last } => {
                assert_eq!(base_url, "http://127.0.0.1:1");
                assert_eq!(last, None);
            }
            other => panic!("expected timeout, got {other:?}"),
        }
        assert!(started.elapsed() >= Duration::from_millis(500));
    }

    #[test]
    fn progress_callback_receives_check_snapshots() {
        let server = FakeReadyz::start(1);
        let seen: std::sync::Mutex<Vec<ReadyzSnapshot>> = std::sync::Mutex::new(Vec::new());
        {
            let seen = &seen;
            let snapshot = wait_until_ready_with_progress(
                &server.url(),
                Duration::from_secs(10),
                |snapshot| seen.lock().expect("lock").push(snapshot),
                || true,
            )
            .expect("ready");
            assert_eq!(snapshot.status, "ready");
        }
        let seen = seen.into_inner().expect("unlock");
        assert_eq!(seen.len(), 1);
        assert_eq!(seen[0].status, "unavailable");
        assert!(seen[0]
            .checks
            .iter()
            .any(|(name, state)| name == "database" && state == "starting"));
    }

    #[test]
    fn resolve_reuses_explicit_backend_url() {
        match resolve_backend_origin(Some("http://127.0.0.1:8000/")).expect("origin") {
            BackendOrigin::Reused(url) => assert_eq!(url, "http://127.0.0.1:8000"),
            BackendOrigin::Spawned { .. } => panic!("must reuse"),
        }
    }

    #[test]
    fn resolve_spawns_when_no_url_given() {
        match resolve_backend_origin(None).expect("origin") {
            BackendOrigin::Reused(_) => panic!("must spawn"),
            BackendOrigin::Spawned { port } => assert_ne!(port, 0),
        }
    }

    #[cfg(windows)]
    fn wait_for_exit(handle: &mut SidecarHandle) -> std::process::ExitStatus {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(status) = handle.try_wait().expect("try_wait") {
                return status;
            }
            assert!(Instant::now() < deadline, "child did not exit in time");
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    #[cfg(windows)]
    #[test]
    fn spawned_sidecar_receives_desktop_env_and_logs_to_file() {
        let tmp = std::env::temp_dir().join(format!(
            "agentcanvas-sidecar-test-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&tmp).expect("tmp dir");
        let program = PathBuf::from("cmd");
        let port = pick_free_port().expect("port");
        // `cmd /c set APP_...` prints the matching environment entries.
        let mut handle =
            spawn_command(&program, &tmp, port, &tmp, &["/c", "set APP_"]).expect("spawn");
        let expected_log = sidecar_log_path(&tmp);
        assert_eq!(handle.log_path, expected_log);
        let status = wait_for_exit(&mut handle);
        assert!(status.success());
        let logged = std::fs::read_to_string(&expected_log).expect("log file");
        assert!(logged.contains("APP_PROFILE=desktop"), "log: {logged}");
        assert!(logged.contains(&format!("APP_PORT={port}")), "log: {logged}");
        assert!(
            logged.contains(&format!("APP_DATA_DIR={}", tmp.display())),
            "log: {logged}"
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[cfg(windows)]
    #[test]
    fn dropping_the_handle_kills_the_child() {
        let tmp = std::env::temp_dir();
        let program = PathBuf::from("cmd");
        let port = pick_free_port().expect("port");
        let handle = spawn_command(
            &program,
            &tmp,
            port,
            &tmp,
            &["/c", "ping -n 30 127.0.0.1 > nul"],
        )
        .expect("spawn");
        let id = handle.id();
        // Windows job object + explicit kill: the child must be gone shortly
        // after the handle drops even though ping would run for ~30s.
        drop(handle);
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            match kill_checker::is_process_alive(id) {
                Some(false) => break,
                Some(true) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(100))
                }
                other => panic!("process {id} still alive after drop: {other:?}"),
            }
        }
    }

    #[cfg(windows)]
    #[test]
    fn child_exit_fails_the_wait_fast() {
        let tmp = std::env::temp_dir();
        let program = PathBuf::from("cmd");
        let port = pick_free_port().expect("port");
        // The child exits immediately with a distinctive code; nothing ever
        // listens on the port, so only the child-exit monitor can end the
        // wait before the timeout.
        let mut handle =
            spawn_command(&program, &tmp, port, &tmp, &["/c", "exit 3"]).expect("spawn");
        let started = Instant::now();
        let result = wait_until_ready_with_progress(
            "http://127.0.0.1:1",
            Duration::from_secs(30),
            |_| {},
            || handle.try_wait().expect("try_wait").is_none(),
        );
        match result.expect_err("must fail fast") {
            StartupFailure::ChildExited { .. } => {}
            other => panic!("expected child exit, got {other:?}"),
        }
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "fast fail took {:?}",
            started.elapsed()
        );
    }

    #[cfg(windows)]
    mod kill_checker {
        // Lightweight liveness probe for tests via `tasklist`.
        pub fn is_process_alive(pid: u32) -> Option<bool> {
            let output = std::process::Command::new("tasklist")
                .args(["/FI", &format!("PID eq {pid}")])
                .output()
                .ok()?;
            let stdout = String::from_utf8_lossy(&output.stdout);
            Some(stdout.contains(&pid.to_string()))
        }
    }
}
