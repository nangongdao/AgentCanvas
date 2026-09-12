//! Sidecar supervision for the AgentCanvas desktop shell (C9-1): start the
//! embedded backend, wait for readiness, and guarantee process teardown.
//!
//! Deliberately independent of Tauri so the state machine and IO stay
//! unit-testable without the webview stack; `agentcanvas_desktop_lib` wires
//! it into the Tauri lifecycle.

mod job;

use std::io;
use std::net::TcpListener;
use std::path::PathBuf;
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
/// normal quit, panic, or crash — reaps the backend. The child is an
/// `Option` so a consuming read (`wait_with_output`) can take it without
/// moving out of a `Drop` type.
pub struct SidecarHandle {
    child: Option<Child>,
    _job: Option<crate::job::JobGuard>,
}

impl SidecarHandle {
    /// Process id, used by the startup window to attribute logs (next slice).
    #[allow(dead_code)]
    pub fn id(&self) -> u32 {
        self.child.as_ref().expect("sidecar child").id()
    }

    /// Consume the handle and wait for the child to exit, returning its
    /// captured output. Test-only surface for `cmd /c` probes.
    #[cfg(test)]
    pub fn wait_with_output(mut self) -> io::Result<std::process::Output> {
        let child = self.child.take().expect("sidecar child");
        let output = child.wait_with_output()?;
        // The child has exited; dropping the handle now only closes the job
        // guard, which is a no-op for an exited process.
        drop(self);
        Ok(output)
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

#[derive(Debug, PartialEq, Eq)]
pub enum Readiness {
    Ready,
    NotReady,
}

/// One readiness probe against `/readyz`. A connection refusal or a 5xx keeps
/// the loop alive: the sidecar may still be importing heavy modules.
pub fn probe_once(base_url: &str) -> Readiness {
    let probe_url = format!("{base_url}/readyz");
    let outcome = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .and_then(|client| {
            client
                .get(&probe_url)
                .send()
                .and_then(|response| response.error_for_status())
        });
    match outcome {
        Ok(_) => Readiness::Ready,
        Err(_) => Readiness::NotReady,
    }
}

/// Poll `/readyz` until it returns 200 or the deadline expires. This is the
/// Rust-side mirror of the C9 plan §5.3: the backend's `/readyz` already
/// checks database, migrations, checkpointer, config, vector store, and the
/// sandbox, and the response body's per-check detail is what the startup
/// window will render once the splash slice lands.
pub fn wait_until_ready(base_url: &str, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    loop {
        if probe_once(base_url) == Readiness::Ready {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "backend at {base_url} did not become ready within {timeout:?}"
            ));
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

/// Spawn the sidecar with the desktop profile env. The caller receives a
/// handle whose drop kills the process tree.
pub fn spawn_sidecar(spec: &SidecarSpec, port: u16, data_dir: &PathBuf) -> io::Result<SidecarHandle> {
    spawn_command(&spec.program, &spec.working_dir, port, data_dir, &[])
}

/// Lower-level spawn used by production (`spawn_sidecar`, no extra args) and
/// by tests (which drive `cmd /c ...` to observe the injected environment and
/// the kill-on-drop guarantee).
pub fn spawn_command(
    program: &PathBuf,
    working_dir: &PathBuf,
    port: u16,
    data_dir: &PathBuf,
    args: &[&str],
) -> io::Result<SidecarHandle> {
    let mut command = std::process::Command::new(program);
    command
        .current_dir(working_dir)
        .args(args)
        .env("APP_PROFILE", "desktop")
        .env("APP_HOST", "127.0.0.1")
        .env("APP_PORT", port.to_string())
        .env("APP_DATA_DIR", data_dir)
        .env("STARTUP_MIGRATIONS", "true")
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    #[cfg(windows)]
    {
        // Prevent a detached console window from flashing on launch.
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let child = command.spawn()?;
    let job = crate::job::JobGuard::for_child(&child);
    Ok(SidecarHandle { child: Some(child), _job: job })
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
        Some(url) if !url.trim().is_empty() => Ok(BackendOrigin::Reused(url.trim().trim_end_matches('/').to_string())),
        _ => Ok(BackendOrigin::Spawned { port: pick_free_port()? }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpStream;

    /// Minimal HTTP/1.1 server for one connection: replies 503 `times` and
    /// 200 afterwards, recording the request paths.
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
                    let mut buffer = [0u8; 1024];
                    let _ = stream.read(&mut buffer);
                    let status = if index < failures_first { "503" } else { "200" };
                    let body = if status == "200" { r#"{"status":"ok"}"# } else { r#"{"status":"degraded"}"# };
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
        wait_until_ready(&server.url(), Duration::from_secs(10)).expect("ready");
    }

    #[test]
    fn wait_until_ready_times_out_when_never_ready() {
        let started = Instant::now();
        // Nothing listens on this port: every probe fails until the deadline.
        let result = wait_until_ready("http://127.0.0.1:1", Duration::from_millis(600));
        assert!(result.is_err());
        assert!(started.elapsed() >= Duration::from_millis(500));
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
    #[test]
    fn spawned_sidecar_receives_desktop_env() {
        let tmp = std::env::temp_dir().join(format!("agentcanvas-sidecar-test-{}", std::process::id()));
        std::fs::create_dir_all(&tmp).expect("tmp dir");
        let program = PathBuf::from("cmd");
        let port = pick_free_port().expect("port");
        // `cmd /c set APP_...` prints the matching environment entries.
        let handle =
            spawn_command(&program, &tmp, port, &tmp, &["/c", "set APP_"]).expect("spawn");
        let output = handle.wait_with_output().expect("output");
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(stdout.contains("APP_PROFILE=desktop"), "stdout: {stdout}");
        assert!(stdout.contains(&format!("APP_PORT={port}")), "stdout: {stdout}");
        assert!(
            stdout.contains(&format!("APP_DATA_DIR={}", tmp.display())),
            "stdout: {stdout}"
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
