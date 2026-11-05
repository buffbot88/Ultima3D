//! Blender background-worker client: persistent `blender --background`,
//! JSON-lines RPC where responses are `@JSON@`-prefixed. Mirrors
//! Blender background-worker client: one in-flight call at a time, hung workers are
//! killed past the deadline so the next call restarts fresh.
//!
//! Pipe I/O blocks, so every call runs on a blocking thread behind an
//! `Arc<std::sync::Mutex<...>>` (the guard crosses the thread boundary,
//! which `tokio::sync::Mutex`'s borrowed guard cannot do).

use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use serde_json::Value;

const PREFIX: &str = "@JSON@";

/// How many stderr lines are kept for post-mortem error text.
const STDERR_TAIL_LINES: usize = 12;
/// How far up from the executable's directory to look for `blender/worker.py`.
const WORKER_SEARCH_DEPTH: usize = 4;

/// Locate the geometry worker script without depending on the client's cwd.
///
/// The MCP client may launch this binary from any directory (Freebuff starts it
/// in the user's current project), so a cwd-relative path silently pointed at a
/// `blender/worker.py` that does not exist and every call failed with a Blender
/// exit whose stderr was discarded. Resolution order:
///   1. the executable's own directory and its ancestors (`target/release` ->
///      `rust` -> repo root), which works for any checkout layout;
///   2. the current working directory, which is what the docs describe.
/// The cwd candidate is returned even when nothing exists, so the failure names
/// the path that was tried instead of surfacing as a dead Blender.
pub fn resolve_worker_py(exe_dir: Option<&Path>, cwd: &Path) -> PathBuf {
    if let Some(dir) = exe_dir {
        for ancestor in dir.ancestors().take(WORKER_SEARCH_DEPTH + 1) {
            let candidate = ancestor.join("blender").join("worker.py");
            if candidate.is_file() {
                return candidate;
            }
        }
    }
    cwd.join("blender").join("worker.py")
}

/// Death/timeout text: the last stderr lines, or a plain note when empty.
fn tail_text(tail: &VecDeque<String>) -> String {
    if tail.is_empty() {
        return "(blender wrote no stderr)".to_string();
    }
    let lines: Vec<&str> = tail.iter().map(String::as_str).collect();
    format!("last stderr:\n{}", lines.join("\n"))
}

#[derive(Clone)]
pub struct Worker {
    inner: Arc<Mutex<WorkerInner>>,
    blender: PathBuf,
    worker_py: PathBuf,
    timeout: Duration,
}

struct WorkerInner {
    id: u64,
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    stdout: Option<BufReader<ChildStdout>>,
    /// Bounded copy of the worker's stderr, kept so a death can be explained.
    stderr_tail: Arc<Mutex<VecDeque<String>>>,
}

impl Worker {
    pub fn new(blender: PathBuf, worker_py: PathBuf, timeout_secs: u64) -> Self {
        Self {
            inner: Arc::new(Mutex::new(WorkerInner {
                id: 0,
                child: None,
                stdin: None,
                stdout: None,
                stderr_tail: Arc::new(Mutex::new(VecDeque::new())),
            })),
            blender,
            worker_py,
            timeout: Duration::from_secs(timeout_secs),
        }
    }

    pub async fn call(&self, op: &str, params: Value) -> Result<Value> {
        let inner = Arc::clone(&self.inner);
        let blender = self.blender.clone();
        let worker_py = self.worker_py.clone();
        let op = op.to_string();
        let op_label = op.clone();
        let outcome = tokio::time::timeout(self.timeout, async move {
            tokio::task::spawn_blocking(move || {
                let mut guard = inner.lock().map_err(|_| anyhow!("worker lock poisoned"))?;
                guard.ensure(&blender, &worker_py)?;
                guard.roundtrip(&op, params)
            })
            .await
            .map_err(|e| anyhow!("worker task panicked: {e}"))?
        })
        .await;
        match outcome {
            Ok(r) => r,
            Err(_) => {
                self.kill();
                Err(anyhow!(
                    "{op_label} timed out; the hung worker was terminated, so its scene/recipe \
                     state is lost and the next call restarts fresh"
                ))
            }
        }
    }

    /// Kill the worker now (used after a timeout so state can't linger).
    pub fn kill(&self) {
        if let Ok(mut guard) = self.inner.lock() {
            guard.kill();
        }
    }

    pub fn is_running(&self) -> bool {
        self.inner.lock().map(|mut g| g.alive()).unwrap_or(false)
    }
}

impl WorkerInner {
    fn alive(&mut self) -> bool {
        self.child
            .as_mut()
            .is_some_and(|c| c.try_wait().unwrap_or(None).is_none())
    }

    fn ensure(&mut self, blender: &std::path::Path, worker_py: &std::path::Path) -> Result<()> {
        if self.alive() {
            return Ok(());
        }
        self.kill();
        if !blender.is_file() {
            anyhow::bail!(
                "blender not found at {}; set ULTIMA3D_BLENDER",
                blender.display()
            );
        }
        // Fail before spawning Blender: a missing script is a configuration
        // error, not a crash, and must say so.
        if !worker_py.is_file() {
            anyhow::bail!(
                "worker script not found at {}; run the server with cwd at the Ultima3D \
                 checkout root, or set ULTIMA3D_WORKER to its absolute path",
                worker_py.display()
            );
        }
        let tail = Arc::clone(&self.stderr_tail);
        if let Ok(mut guard) = tail.lock() {
            guard.clear();
        }
        let mut child = Command::new(blender)
            .args(["--background", "--factory-startup", "--python"])
            .arg(worker_py)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            // Captured, never null: a death the model cannot read is a death it
            // cannot fix.
            .stderr(Stdio::piped())
            .spawn()
            .context("spawning blender worker")?;
        let stdin = child.stdin.take().context("worker stdin")?;
        let stdout = child
            .stdout
            .take()
            .map(BufReader::new)
            .context("worker stdout")?;
        if let Some(stderr) = child.stderr.take() {
            std::thread::Builder::new()
                .name("ultima3d-stderr".to_string())
                .spawn(move || {
                    for line in BufReader::new(stderr).lines() {
                        let Ok(line) = line else { break };
                        if let Ok(mut guard) = tail.lock() {
                            if guard.len() == STDERR_TAIL_LINES {
                                guard.pop_front();
                            }
                            guard.push_back(line);
                        }
                    }
                })
                .ok();
        }
        self.child = Some(child);
        self.stdin = Some(stdin);
        self.stdout = Some(stdout);
        // First request confirms liveness (mirrors server.py _start). If the
        // worker dies during startup, say that, and include what it printed.
        let stderr_tail = Arc::clone(&self.stderr_tail);
        let started = self.roundtrip("ping", Value::Object(Default::default()));
        if let Err(err) = started {
            let tail = stderr_tail.lock().map(|g| tail_text(&g)).unwrap_or_default();
            self.kill();
            return Err(anyhow!("blender worker failed to start: {err}\n{tail}"));
        }
        Ok(())
    }

    fn kill(&mut self) {
        if let Some(stdin) = self.stdin.take() {
            drop(stdin);
        }
        if let Some(stdout) = self.stdout.take() {
            drop(stdout);
        }
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    fn roundtrip(&mut self, op: &str, params: Value) -> Result<Value> {
        self.id += 1;
        let rid = self.id;
        let line = serde_json::json!({"id": rid, "op": op, "params": params});
        let stdin = self.stdin.as_mut().context("worker not running")?;
        writeln!(stdin, "{}", serde_json::to_string(&line)?)?;
        stdin.flush()?;
        let stdout = self.stdout.as_mut().context("worker not running")?;
        let mut buf = String::new();
        loop {
            buf.clear();
            let n = stdout.read_line(&mut buf)?;
            if n == 0 {
                // `try_wait` reaps the exit code, which says whether Blender
                // crashed or was killed; the tail says why.
                let status = self
                    .child
                    .as_mut()
                    .and_then(|c| c.try_wait().ok().flatten())
                    .map(|s| format!(" ({s})"))
                    .unwrap_or_default();
                let tail = self
                    .stderr_tail
                    .lock()
                    .map(|g| tail_text(&g))
                    .unwrap_or_default();
                self.kill();
                anyhow::bail!("blender worker died{status}; {tail}");
            }
            let trimmed = buf.trim();
            if !trimmed.starts_with(PREFIX) {
                continue; // Blender progress/log noise on stdout
            }
            let resp: Value = serde_json::from_str(&trimmed[PREFIX.len()..])?;
            if resp.get("id").and_then(Value::as_u64) != Some(rid) {
                continue; // stale response from a crashed previous call
            }
            if resp.get("ok").and_then(Value::as_bool) != Some(true) {
                let err = resp
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown");
                let trace = resp.get("trace").and_then(Value::as_str).unwrap_or("");
                anyhow::bail!("{op} failed: {err}\n{trace}");
            }
            return Ok(resp.get("result").cloned().unwrap_or(Value::Null));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// A unique scratch directory per test, removed on drop.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new(label: &str) -> Self {
            let dir = std::env::temp_dir().join(format!(
                "ultima3d-worker-test-{label}-{}-{:?}",
                std::process::id(),
                std::thread::current().id()
            ));
            fs::create_dir_all(&dir).expect("scratch dir");
            Self(dir)
        }

        fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn touch_worker(root: &Path) -> PathBuf {
        let worker = root.join("blender").join("worker.py");
        fs::create_dir_all(worker.parent().unwrap()).unwrap();
        fs::write(&worker, "# worker").unwrap();
        worker
    }

    #[test]
    fn worker_resolves_from_exe_ancestors_not_the_clients_cwd() {
        // Layout: <root>/blender/worker.py and <root>/rust/target/release/server.exe
        let root = Scratch::new("exe-root");
        let expected = touch_worker(root.path());
        let exe_dir = root.path().join("rust").join("target").join("release");
        fs::create_dir_all(&exe_dir).unwrap();
        let foreign_cwd = Scratch::new("foreign-cwd");

        let resolved = resolve_worker_py(Some(&exe_dir), foreign_cwd.path());

        assert_eq!(resolved, expected);
    }

    #[test]
    fn worker_falls_back_to_cwd_when_the_exe_has_no_checkout() {
        let root = Scratch::new("cwd-root");
        let expected = touch_worker(root.path());
        let exe_dir = Scratch::new("bare-exe");

        let resolved = resolve_worker_py(Some(exe_dir.path()), root.path());

        assert_eq!(resolved, expected);
    }

    #[test]
    fn missing_worker_reports_the_path_it_tried() {
        let exe_dir = Scratch::new("no-exe-checkout");
        let cwd = Scratch::new("empty-cwd");

        let resolved = resolve_worker_py(Some(exe_dir.path()), cwd.path());

        // Never a silent None: the caller bails with this path in the message.
        assert_eq!(resolved, cwd.path().join("blender").join("worker.py"));
        assert!(!resolved.is_file());
    }

    #[test]
    fn stderr_tail_is_bounded_and_readable() {
        let mut tail = VecDeque::new();
        assert_eq!(tail_text(&tail), "(blender wrote no stderr)");

        for i in 0..STDERR_TAIL_LINES {
            tail.push_back(format!("line {i}"));
        }
        let text = tail_text(&tail);
        assert!(text.starts_with("last stderr:\n"));
        assert!(text.contains("line 0"));
        assert!(text.contains(&format!("line {}", STDERR_TAIL_LINES - 1)));
        assert_eq!(tail.len(), STDERR_TAIL_LINES);
    }
}
