//! Live-Blender backend: persistent TCP socket to the user's open Blender
//! (the addon from blender/addon.py), plus the asset-library tools that
//! drive it.
//!
//! Divergences from the Python backend:
//! - Telemetry counters/decorators are not sent anywhere (no Supabase
//!   credentials ship with the port); `record_trajectory_feedback` appends
//!   to a local JSONL file and reports honestly. Consent state is read live
//!   from the addon (`get_telemetry_consent`) instead of a local cache.
//! - Screenshots are saved to disk like the Python default (`file` mode) or
//!   returned inline (`BLENDER_MCP_IMAGE_OUTPUT=inline`); the ImageMagick
//!   downscale step is skipped, the size note is kept.
//! - Safe mode is enforced by safe_mode.rs (tree-sitter); see its docs for
//!   the (minor) rule differences vs the original Python validator.

use std::io::{Read, Write};
use std::net::{Shutdown, TcpStream};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{anyhow, Result};
use serde_json::Value;

pub const EXPECTED_ADDON_PROTOCOL_VERSION: i64 = 10;
const SOCKET_TIMEOUT: Duration = Duration::from_secs(180);

fn cli_flag(name: &str) -> Option<String> {
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        if a == name {
            return args.next();
        }
        let prefix = format!("{name}=");
        if let Some(v) = a.strip_prefix(&prefix) {
            return Some(v.to_string());
        }
    }
    None
}

pub fn resolve_connection() -> (String, u16) {
    let host = cli_flag("--host")
        .or_else(|| std::env::var("BLENDER_HOST").ok())
        .unwrap_or_else(|| "localhost".to_string());
    let port = cli_flag("--port")
        .and_then(|v| v.parse().ok())
        .or_else(|| {
            std::env::var("BLENDER_PORT")
                .ok()
                .and_then(|v| v.parse().ok())
        })
        .unwrap_or(9876);
    (host, port)
}

pub fn connection_info() -> Value {
    let (host, port) = resolve_connection();
    serde_json::json!({"ok": true, "host": host, "port": port})
}

struct ConnInner {
    host: String,
    port: u16,
    sock: Option<TcpStream>,
}

#[derive(Clone)]
pub struct LiveConn {
    inner: Arc<Mutex<ConnInner>>,
}

impl LiveConn {
    pub fn new() -> Self {
        let (host, port) = resolve_connection();
        Self {
            inner: Arc::new(Mutex::new(ConnInner {
                host,
                port,
                sock: None,
            })),
        }
    }

    /// Send a command and return the addon's `result` payload.
    /// Matches BlenderConnection.send_command: one in-flight command per
    /// socket (responses are matched purely by stream ordering), reconnect
    /// on next call after any failure, 180s read timeout.
    pub fn send_command(&self, command_type: &str, params: Value) -> Result<Value> {
        let mut guard = self
            .inner
            .lock()
            .map_err(|_| anyhow!("live connection lock poisoned"))?;
        if guard.sock.is_none() && !Self::connect_inner(&mut guard) {
            return Err(anyhow!(
                "Could not connect to Blender. Make sure the Blender addon is running."
            ));
        }
        let command = serde_json::json!({"type": command_type, "params": params});
        let bytes = serde_json::to_vec(&command)?;
        let r = Self::exchange(&mut guard, &bytes);
        if r.is_err() {
            guard.sock = None; // dead socket is recreated on the next command
        }
        r
    }

    fn connect_inner(guard: &mut ConnInner) -> bool {
        match TcpStream::connect_timeout(
            &format!("{}:{}", guard.host, guard.port).parse().unwrap(),
            Duration::from_secs(5),
        ) {
            Ok(s) => {
                let _ = s.set_read_timeout(Some(SOCKET_TIMEOUT));
                guard.sock = Some(s);
                true
            }
            Err(_) => {
                guard.sock = None;
                false
            }
        }
    }

    fn exchange(guard: &mut ConnInner, bytes: &[u8]) -> Result<Value> {
        let sock = guard
            .sock
            .as_mut()
            .ok_or_else(|| anyhow!("Not connected to Blender"))?;
        sock.write_all(bytes)
            .map_err(|e| anyhow!("Connection to Blender lost: {e}"))?;
        let _ = sock.shutdown(Shutdown::Write);
        let mut buf: Vec<u8> = vec![];
        let mut tmp = [0u8; 8192];
        loop {
            let n = sock.read(&mut tmp);
            match n {
                Ok(0) => {
                    if buf.is_empty() {
                        return Err(anyhow!("Connection closed before receiving any data"));
                    }
                    break;
                }
                Ok(k) => {
                    buf.extend_from_slice(&tmp[..k]);
                    // Complete JSON object received?
                    if serde_json::from_slice::<Value>(&buf).is_ok() {
                        break;
                    }
                }
                Err(e)
                    if e.kind() == std::io::ErrorKind::TimedOut
                        || e.kind() == std::io::ErrorKind::WouldBlock =>
                {
                    if buf.is_empty() {
                        return Err(anyhow!("Timeout waiting for Blender response - try simplifying your request. If Blender is running headless (blender -b), commands never execute; run Blender with a GUI instead"));
                    }
                    break;
                }
                Err(e) => return Err(anyhow!("Connection to Blender lost: {e}")),
            }
            if buf.len() > 512 * 1024 * 1024 {
                return Err(anyhow!("response from Blender exceeds 512 MiB"));
            }
        }
        let response: Value = serde_json::from_slice(&buf)
            .map_err(|e| anyhow!("Invalid response from Blender: {e}"))?;
        if response.get("status").and_then(Value::as_str) == Some("error") {
            let msg = response
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("Unknown error from Blender");
            return Err(anyhow!(msg.to_string()));
        }
        Ok(response.get("result").cloned().unwrap_or(Value::Null))
    }
}

/// Query the addon protocol version; mirrors addon_manager.handshake_addon.
pub fn handshake(conn: &LiveConn) -> Value {
    let payload = match conn.send_command("get_addon_info", Value::Null) {
        Ok(info) => info,
        Err(e) => {
            let msg = e.to_string().to_lowercase();
            let warning = if msg.contains("unknown command") || msg.contains("get_addon_info") {
                "Blender addon is outdated (no get_addon_info). Run `uvx mcp-for-blender install-addon` to update it, then restart Blender or disable/enable 'Interface: MCP for Blender', then Start MCP Server. Fallbacks keep working in the meantime."
            } else {
                &format!("Addon handshake failed: {e}")
            };
            return serde_json::json!({
                "up_to_date": false, "protocol_version": Value::Null,
                "addon_version": Value::Null, "capabilities": [],
                "blender_version": Value::Null, "source": if msg.contains("unknown command") || msg.contains("get_addon_info") { "missing" } else { "error" },
                "warning": warning,
            });
        }
    };
    if !payload.is_object() {
        return serde_json::json!({
            "up_to_date": false, "protocol_version": Value::Null,
            "addon_version": Value::Null, "capabilities": [],
            "blender_version": Value::Null, "source": "error",
            "warning": "Addon returned invalid get_addon_info payload.",
        });
    }
    let protocol = payload.get("protocol_version").and_then(Value::as_i64);
    let up_to_date = protocol
        .map(|p| p >= EXPECTED_ADDON_PROTOCOL_VERSION)
        .unwrap_or(false);
    let warning = if up_to_date {
        Value::Null
    } else {
        Value::String(format!(
            "Blender addon protocol {protocol:?} is behind expected {EXPECTED_ADDON_PROTOCOL_VERSION}. Run `uvx mcp-for-blender install-addon` to update it, then restart Blender or disable/enable 'Interface: MCP for Blender', then Start MCP Server. Trajectory still works via fallbacks."))
    };
    serde_json::json!({
        "up_to_date": up_to_date,
        "protocol_version": protocol,
        "addon_version": payload.get("addon_version").cloned().unwrap_or(Value::Null),
        "capabilities": payload.get("capabilities").cloned().unwrap_or(Value::Array(vec![])),
        "blender_version": payload.get("blender_version").cloned().unwrap_or(Value::Null),
        "source": "native",
        "warning": warning,
    })
}

/// Telemetry consent, read live from the addon (None when unreachable).
pub fn telemetry_consent(conn: &LiveConn) -> Option<bool> {
    conn.send_command("get_telemetry_consent", Value::Null)
        .ok()
        .and_then(|v| v.get("consent").and_then(Value::as_bool))
        .or_else(|| {
            conn.send_command("get_telemetry_consent", Value::Null)
                .ok()
                .and_then(|v| v.as_bool())
        })
}

// -- image files ----------------------------------------------------------
// Image-file handling (minus the old ImageMagick shrink step).

const MAX_IMAGE_BYTES: u64 = 700 * 1024;
const KEEP_NEWEST: usize = 40;

pub fn image_output_mode() -> String {
    std::env::var("BLENDER_MCP_IMAGE_OUTPUT")
        .unwrap_or_else(|_| "file".to_string())
        .trim()
        .to_lowercase()
}

pub fn image_dir() -> PathBuf {
    if let Ok(d) = std::env::var("BLENDER_MCP_IMAGE_DIR") {
        let p = PathBuf::from(shellexpand_home(&d));
        return p;
    }
    std::env::temp_dir().join("mcp-for-blender-images")
}

fn shellexpand_home(d: &str) -> String {
    if let Some(rest) = d.strip_prefix('~') {
        if let Ok(home) = std::env::var(if cfg!(windows) { "USERPROFILE" } else { "HOME" }) {
            return format!("{home}{rest}");
        }
    }
    d.to_string()
}

fn human_size(n: u64) -> String {
    if n < 1024 {
        format!("{n} bytes")
    } else if n < 1024 * 1024 {
        format!("{} KB", n / 1024)
    } else {
        format!("{:.1} MB", n as f64 / (1024.0 * 1024.0))
    }
}

fn ext_for(format: &str) -> &str {
    match format
        .to_lowercase()
        .split('/')
        .next_back()
        .unwrap_or("png")
    {
        "jpeg" | "jpg" => "jpg",
        "webp" => "webp",
        "gif" => "gif",
        "bmp" => "bmp",
        "tiff" => "tiff",
        _ => "png",
    }
}

pub fn save_image(data: &[u8], image_format: &str, tool: &str) -> Result<PathBuf> {
    let directory = image_dir();
    std::fs::create_dir_all(&directory)?;
    let safe_tool: String = tool
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect();
    let stamp = chrono_stamp();
    let mut path = directory.join(format!(
        "{safe_tool}-{stamp}-{}.{}",
        std::process::id(),
        ext_for(image_format)
    ));
    let mut counter = 2;
    while path.exists() {
        path = directory.join(format!(
            "{safe_tool}-{stamp}-{counter}.{}",
            ext_for(image_format)
        ));
        counter += 1;
    }
    std::fs::write(&path, data)?;
    prune(&directory);
    Ok(path)
}

fn chrono_stamp() -> String {
    // YYYYMMDD-HHMMSS without external crates: derive from SystemTime via libc-free math is
    // overkill; use a monotonic-ish unique stamp (date precision is not load-bearing).
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Convert to a readable UTC-ish stamp: days since epoch -> yyyymmdd via civil calendar math.
    let days = (secs / 86400) as i64;
    let rem = (secs % 86400) as i64;
    let (y, m, d) = civil_from_days(days + 719468);
    format!("{y:04}{m:02}{d:02}-{rem:05}")
}

fn civil_from_days(z: i64) -> (i64, i64, i64) {
    // Howard Hinnant's civil_from_days.
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn prune(directory: &std::path::Path) {
    let entries = match std::fs::read_dir(directory) {
        Ok(e) => e,
        Err(_) => return,
    };
    let mut files: Vec<(std::time::SystemTime, PathBuf)> = vec![];
    for e in entries.flatten() {
        let p = e.path();
        if p.is_file() {
            let mtime = std::fs::metadata(&p)
                .and_then(|m| m.modified())
                .unwrap_or(std::time::UNIX_EPOCH.into());
            files.push((mtime, p));
        }
    }
    if files.len() <= KEEP_NEWEST {
        return;
    }
    files.sort_by(|a, b| b.0.cmp(&a.0));
    for (_, stale) in files.into_iter().skip(KEEP_NEWEST) {
        let _ = std::fs::remove_file(stale);
    }
}

/// Save `data` and return the note (with path) to hand back to the model.
pub fn deliver_image(data: &[u8], image_format: &str, tool: &str, detail: &str) -> Result<String> {
    let path = save_image(data, image_format, tool)?;
    let size_bytes = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
    let mut note = format!(
        "{} saved the image to {} ({}).",
        tool,
        path.display(),
        human_size(size_bytes)
    );
    if !detail.is_empty() {
        note.push(' ');
        note.push_str(detail);
    }
    note.push_str(" Open that path with your file-reading tool (for example read_files) to see it; this host does not render inline image content.");
    if size_bytes > MAX_IMAGE_BYTES {
        let parent = path.parent().unwrap_or(std::path::Path::new("."));
        let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("image");
        let small = parent.join(format!("{stem}-small.jpg"));
        note.push_str(&format!(" If your reader refuses it for size, downscale first: `magick {} -resize 1200x1200 {}`.", path.display(), small.display()));
    }
    Ok(note)
}

// -- trajectory feedback (local JSONL; mirrors the tool contract, not Supabase) --

fn feedback_path() -> PathBuf {
    let base = std::env::var("ULTIMA3D_OUT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            std::env::current_dir()
                .unwrap_or(PathBuf::from("."))
                .join("output")
        });
    base.join("trajectory_feedback.jsonl")
}

/// Append one feedback row. Returns true when recorded.
pub fn record_feedback(
    feedback: &str,
    correction_text: Option<&str>,
    step_index: Option<i64>,
    goal_text: Option<&str>,
) -> bool {
    let row = serde_json::json!({
        "feedback": feedback,
        "correction_text": correction_text,
        "step_index": step_index.unwrap_or(0),
        "goal_text": goal_text,
        "source": "agent_report",
        "event_timestamp": std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0),
    });
    let path = feedback_path();
    if let Some(parent) = path.parent() {
        if std::fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    use std::io::Write as _;
    match std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        Ok(mut f) => writeln!(f, "{row}").is_ok(),
        Err(_) => false,
    }
}

// -- Poly Pizza id coercion (server is the single source of truth) ------------

static POLYPIZZA_CATEGORIES: &[(&str, i64)] = &[
    ("Food & Drink", 0),
    ("Clutter", 1),
    ("Weapons", 2),
    ("Transport", 3),
    ("Furniture & Decor", 4),
    ("Objects", 5),
    ("Nature", 6),
    ("Animals", 7),
    ("Buildings", 8),
    ("People & Characters", 9),
    ("Scenes & Levels", 10),
    ("Other", 11),
];

static POLYPIZZA_ALIASES: &[(&str, i64)] = &[
    ("food", 0),
    ("drink", 0),
    ("drinks", 0),
    ("weapon", 2),
    ("vehicle", 3),
    ("vehicles", 3),
    ("transportation", 3),
    ("furniture", 4),
    ("decor", 4),
    ("object", 5),
    ("prop", 5),
    ("props", 5),
    ("plant", 6),
    ("plants", 6),
    ("animal", 7),
    ("building", 8),
    ("architecture", 8),
    ("buildingsarchitecture", 8),
    ("person", 9),
    ("character", 9),
    ("characters", 9),
    ("people", 9),
    ("scene", 10),
    ("scenes", 10),
    ("level", 10),
    ("levels", 10),
];

fn polypizza_normalize(value: &str) -> String {
    value
        .to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric())
        .collect()
}

pub fn polypizza_category_id(category: &Value) -> Result<Option<i64>, String> {
    if category.is_null() {
        return Ok(None);
    }
    if let Some(s) = category.as_str() {
        if s.is_empty() {
            return Ok(None);
        }
        let t = s.trim();
        if t.strip_prefix('-')
            .unwrap_or(t)
            .chars()
            .all(|c| c.is_ascii_digit())
            && !t.is_empty()
            && t != "-"
        {
            let v: i64 = t.parse().map_err(|_| {
                format!("Poly Pizza category id {t} is out of range (valid ids are 0-11)")
            })?;
            if !(0..=11).contains(&v) {
                return Err(format!(
                    "Poly Pizza category id {v} is out of range (valid ids are 0-11)"
                ));
            }
            return Ok(Some(v));
        }
        let key = polypizza_normalize(t);
        for (name, id) in POLYPIZZA_CATEGORIES {
            if polypizza_normalize(name) == key {
                return Ok(Some(*id));
            }
        }
        for (alias, id) in POLYPIZZA_ALIASES {
            if *alias == key {
                return Ok(Some(*id));
            }
        }
        let valid: Vec<&str> = POLYPIZZA_CATEGORIES.iter().map(|(n, _)| *n).collect();
        return Err(format!(
            "Unknown Poly Pizza category {s:?}. Valid categories: {}",
            valid.join(", ")
        ));
    }
    if let Some(v) = category.as_i64() {
        if !(0..=11).contains(&v) {
            return Err(format!(
                "Poly Pizza category id {v} is out of range (valid ids are 0-11)"
            ));
        }
        return Ok(Some(v));
    }
    if let Some(b) = category.as_bool() {
        let _ = b;
        return Err("Poly Pizza category must be a name or an id in 0-11".to_string());
    }
    Err("Poly Pizza category must be a name or an id in 0-11".to_string())
}

pub fn polypizza_licence_id(licence: &Value) -> Result<Option<i64>, String> {
    if licence.is_null() {
        return Ok(None);
    }
    if let Some(s) = licence.as_str() {
        if s.is_empty() {
            return Ok(None);
        }
        let t = s.trim();
        if t.strip_prefix('-')
            .unwrap_or(t)
            .chars()
            .all(|c| c.is_ascii_digit())
            && !t.is_empty()
            && t != "-"
        {
            let v: i64 = t.parse().unwrap_or(99);
            if v != 0 && v != 1 {
                return Err(format!(
                    "Poly Pizza licence id {v} is invalid (0 = CC-BY, 1 = CC0)"
                ));
            }
            return Ok(Some(v));
        }
        let key = polypizza_normalize(t);
        if key.starts_with("ccby") {
            return Ok(Some(0));
        }
        if key.starts_with("cc0") || key == "publicdomain" {
            return Ok(Some(1));
        }
        return Err(format!(
            "Unknown Poly Pizza licence {s:?}. Use 'CC0' or 'CC-BY'."
        ));
    }
    if let Some(v) = licence.as_i64() {
        if v != 0 && v != 1 {
            return Err(format!(
                "Poly Pizza licence id {v} is invalid (0 = CC-BY, 1 = CC0)"
            ));
        }
        return Ok(Some(v));
    }
    if licence.as_bool().is_some() {
        return Err("Poly Pizza licence must be 'CC0', 'CC-BY', 0 or 1".to_string());
    }
    Err("Poly Pizza licence must be 'CC0', 'CC-BY', 0 or 1".to_string())
}

pub fn polypizza_category_names() -> String {
    POLYPIZZA_CATEGORIES
        .iter()
        .map(|(n, _)| *n)
        .collect::<Vec<_>>()
        .join(", ")
}

// -- misc formatting helpers ------------------------------------------------

pub fn process_bbox(bbox: &Value) -> Result<Option<Vec<i64>>, String> {
    if bbox.is_null() {
        return Ok(None);
    }
    let arr = bbox
        .as_array()
        .ok_or_else(|| "bbox must be a list".to_string())?;
    let nums: Vec<f64> = arr
        .iter()
        .map(|v| {
            v.as_f64()
                .ok_or_else(|| "bbox entries must be numbers".to_string())
        })
        .collect::<Result<_, _>>()?;
    if nums.iter().any(|v| *v <= 0.0) {
        return Err("Incorrect number range: bbox must be bigger than zero!".to_string());
    }
    if arr.iter().all(|v| v.is_i64() || v.is_u64()) {
        return Ok(Some(nums.iter().map(|v| *v as i64).collect()));
    }
    let max = nums.iter().cloned().fold(0.0_f64, f64::max);
    if max == 0.0 {
        return Ok(Some(vec![]));
    }
    Ok(Some(
        nums.iter().map(|v| (v / max * 100.0) as i64).collect(),
    ))
}

pub fn polyhaven_credit(result: &Value) -> String {
    let authors = result
        .get("authors")
        .and_then(|a| a.as_array())
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default();
    let by = if authors.is_empty() {
        String::new()
    } else {
        format!(" by {authors}")
    };
    let url = result
        .get("url")
        .and_then(Value::as_str)
        .unwrap_or("https://polyhaven.com");
    format!("From Poly Haven{by} - {url} (CC0, free to use for anything).")
}

pub fn polyhaven_scale_note(result: &Value) -> String {
    let size = match result.get("scale_mm").and_then(|v| v.as_array()) {
        Some(s) if s.len() == 2 => s,
        _ => return String::new(),
    };
    let node = match result.get("mapping_node").and_then(Value::as_str) {
        Some(n) => n,
        None => return String::new(),
    };
    let dims: Vec<f64> = size.iter().filter_map(Value::as_f64).collect();
    if dims.len() != 2 {
        return String::new();
    }
    let (w, h) = (dims[0] / 1000.0, dims[1] / 1000.0);
    format!(" The texture covers {w} x {h} in the real world. Its '{node}' node is in POINT mode, where Scale multiplies the UV coordinates: the pattern repeats Scale times across whatever span the UVs cover. For UVs that run 0-1 across a surface, life-sized tiling is Scale = surface size in metres / {w}.")
}

pub const ULTIMA_INSTRUCTIONS: &str = "Ultima3D: procedural asset compiler (ultima3d worker) + live Blender GUI + Godot editor.\n\nProcedural tools (create_3d, refine_asset, render_asset, validate_asset, finalize_asset) build\ngame-ready GLBs in a background Blender worker. Live tools (get_scene_info, execute_blender_code,\nasset libraries) drive the user's open Blender GUI over :9876. Godot tools (godot_*) drive the\nrunning Godot editor bridge. The LLM owns intent and visual judgement; the worker measures results.";

pub const LIVE_INSTRUCTIONS: &str = "Live Blender drives the user's open Blender instance. execute_blender_code runs\narbitrary Python there, so scripts must not assume anything about the user's Blender.\nBefore writing code, call get_addon_status() for blender_version and get_scene_info() for\nexisting objects. Look shader nodes up by type, never by localized name. Never hardcode enum\nidentifiers; read them from bl_rna first. After changing anything, call get_viewport_screenshot()\nand get_scene_info() to confirm. Call the asset_creation_strategy prompt for the asset-library\nworkflow (Poly Haven, Sketchfab, Poly Pizza, BlenderKit, Hyper3D Rodin, Hunyuan3D).";

pub fn server_instructions() -> String {
    format!("{ULTIMA_INSTRUCTIONS}\n\n{LIVE_INSTRUCTIONS}")
}

// -- live tool implementations ------------------------------------------------
// Each mirrors the same-named tool of the live-Blender backend, minus the
// Supabase telemetry writes (record_*_step / record_event are no-ops here:
// consent state is still read live from the addon where the tool reports it).
// Errors are returned as text, matching the Python tools' `except ...: return`.

fn pretty(v: &Value) -> String {
    serde_json::to_string_pretty(v).unwrap_or_else(|_| v.to_string())
}

fn err_in_result(result: &Value) -> Option<String> {
    result.get("error").and_then(|e| {
        e.as_str()
            .map(|s| s.to_string())
            .or_else(|| Some(e.to_string()))
    })
}

pub fn addon_status_text() -> String {
    let conn = LiveConn::new();
    let result = handshake(&conn);
    let consent = telemetry_consent(&conn);
    let mut payload = match result.as_object() {
        Some(o) => o.clone(),
        None => return "Could not determine addon status.".to_string(),
    };
    payload.insert(
        "expected_protocol_version".to_string(),
        Value::from(EXPECTED_ADDON_PROTOCOL_VERSION),
    );
    payload.insert(
        "telemetry_consent".to_string(),
        consent.map(Value::from).unwrap_or(Value::Null),
    );
    payload.insert(
        "update_command".to_string(),
        Value::from("uvx mcp-for-blender install-addon"),
    );
    payload.insert(
        "after_install".to_string(),
        Value::from(
            "If the addon file was updated: in Blender, Preferences → Add-ons → disable/enable 'Interface: Blender MCP', or restart Blender, then Start MCP Server.",
        ),
    );
    pretty(&Value::Object(payload))
}

pub fn disable_telemetry_text() -> String {
    let conn = LiveConn::new();
    match conn.send_command(
        "set_telemetry_consent",
        serde_json::json!({"consent": false}),
    ) {
        Ok(result) => {
            if let Some(err) = err_in_result(&result) {
                return format!("Could not turn off data collection: {err}");
            }
            "Data collection is now OFF. Prompts, code, screenshots and scene data are no longer collected. Minimal anonymous usage counts (tool name, success, duration) still apply -- see the terms for details. To turn collection back on, tick 'Allow Telemetry' in Blender under Preferences > Add-ons > Blender MCP."
                .to_string()
        }
        Err(e) => format!("Error turning off data collection: {e}"),
    }
}

pub fn scene_info_text() -> String {
    let conn = LiveConn::new();
    match conn.send_command("get_scene_info", Value::Null) {
        Ok(result) => {
            if let Some(err) = err_in_result(&result) {
                return format!("Error getting scene info: {err}");
            }
            pretty(&result)
        }
        Err(e) => format!("Error getting scene info: {e}"),
    }
}

pub fn object_info_text(object_name: &str) -> String {
    let conn = LiveConn::new();
    match conn.send_command("get_object_info", serde_json::json!({"name": object_name})) {
        Ok(result) => {
            if let Some(err) = err_in_result(&result) {
                return format!("Error getting object info: {err}");
            }
            pretty(&result)
        }
        Err(e) => format!("Error getting object info: {e}"),
    }
}

/// Capture the viewport screenshot bytes. Returns (png bytes, format).
pub fn screenshot_bytes(max_size: i64) -> Result<(Vec<u8>, String), String> {
    let conn = LiveConn::new();
    let temp_path =
        std::env::temp_dir().join(format!("blender_screenshot_{}.png", std::process::id()));
    let result = conn
        .send_command(
            "get_viewport_screenshot",
            serde_json::json!({
                "max_size": max_size,
                "filepath": temp_path.to_string_lossy(),
                "format": "png",
            }),
        )
        .map_err(|e| format!("Screenshot failed: {e}"))?;
    if let Some(err) = err_in_result(&result) {
        return Err(format!("Screenshot failed: {err}"));
    }
    if !temp_path.exists() {
        return Err("Screenshot failed: Screenshot file was not created".to_string());
    }
    let bytes = std::fs::read(&temp_path).map_err(|e| format!("Screenshot failed: {e}"))?;
    let _ = std::fs::remove_file(&temp_path);
    Ok((bytes, "png".to_string()))
}

pub fn screenshot_note(max_size: i64) -> Result<String, String> {
    let (bytes, format) = screenshot_bytes(max_size)?;
    deliver_image(
        &bytes,
        &format,
        "get_viewport_screenshot",
        &format!("Blender viewport screenshot (max_size={max_size})."),
    )
    .map_err(|e| e.to_string())
}

pub fn execute_code_text(code: &str) -> String {
    if crate::safe_mode::safe_mode_enabled() {
        if let Err(v) = crate::safe_mode::validate_code(code) {
            return format!(
                "Rejected by safe mode - {v}\n\nBLENDER_MCP_SAFE_MODE is enabled: scripts may only import bpy, bmesh, mathutils, and pure-python stdlib modules. No eval/exec/open, no os/subprocess/network access, no handlers/timers/drivers, no class or property registration, and no loading of external .blend datablocks. Blender operators for rendering, saving, and import/export ARE allowed. Rewrite the script within these limits; only the user can disable safe mode."
            );
        }
    }
    let conn = LiveConn::new();
    match conn.send_command("execute_code", serde_json::json!({"code": code})) {
        Ok(result) => {
            let out = result.get("result").and_then(Value::as_str).unwrap_or("");
            format!("Code executed successfully: {out}")
        }
        Err(e) => {
            // The addon reports failures as a JSON payload; render the traceback.
            let s = e.to_string();
            if let Ok(detail) = serde_json::from_str::<Value>(&s) {
                let exc = detail
                    .get("exception_type")
                    .and_then(Value::as_str)
                    .unwrap_or("Error");
                let msg = detail.get("message").and_then(Value::as_str).unwrap_or("");
                let tb = detail
                    .get("traceback")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                return format!("Error executing code: {exc}: {msg}\n\n{tb}");
            }
            format!("Error executing code: {s}")
        }
    }
}

pub fn describe_node_text(
    bl_idname: &str,
    overrides: Option<&serde_json::Map<String, Value>>,
) -> String {
    let conn = LiveConn::new();
    let ov = overrides
        .cloned()
        .map(Value::Object)
        .unwrap_or_else(|| Value::Object(Default::default()));
    match conn.send_command(
        "describe_node_type",
        serde_json::json!({"bl_idname": bl_idname, "property_overrides": ov}),
    ) {
        Ok(result) => pretty(&result),
        Err(e) => format!("Error describing node type '{bl_idname}': {e}"),
    }
}

pub fn bpy_lookup_text(query: &str) -> String {
    let conn = LiveConn::new();
    match conn.send_command("bpy_api_lookup", serde_json::json!({"query": query})) {
        Ok(result) => pretty(&result),
        Err(e) => format!("Error looking up '{query}': {e}"),
    }
}

pub fn polyhaven_categories_text(asset_type: &str) -> String {
    let conn = LiveConn::new();
    let status = match conn.send_command("get_polyhaven_status", Value::Null) {
        Ok(s) => s,
        Err(e) => return format!("Error getting Polyhaven categories: {e}"),
    };
    if !status
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return "PolyHaven integration is disabled. Select it in the sidebar in BlenderMCP, then run it again.".to_string();
    }
    let result = match conn.send_command(
        "get_polyhaven_categories",
        serde_json::json!({"asset_type": asset_type}),
    ) {
        Ok(r) => r,
        Err(e) => return format!("Error getting Polyhaven categories: {e}"),
    };
    if let Some(err) = err_in_result(&result) {
        return format!("Error: {err}");
    }
    let mut lines: Vec<String> = vec![];
    let taxonomy = result
        .get("taxonomy")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let truncated = result
        .get("truncated")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    for t in taxonomy {
        let ty = t.get("type").and_then(Value::as_str).unwrap_or("?");
        lines.push(format!("{ty} categories:"));
        for path in t
            .get("categories")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            if let Some(p) = path.as_str() {
                lines.push(format!("  {p}"));
            }
        }
        if truncated {
            lines.push(
                "  (top two levels only - ask for a single asset type for the rest)".to_string(),
            );
        }
        lines.push(String::new());
        if let Some(attrs) = t.get("attributes").and_then(Value::as_object) {
            if !attrs.is_empty() {
                lines.push(format!("{ty} attributes:"));
                for (key, spec) in attrs {
                    let allowed = spec
                        .get("enum")
                        .and_then(Value::as_array)
                        .map(|a| {
                            a.iter()
                                .filter_map(Value::as_str)
                                .collect::<Vec<_>>()
                                .join(", ")
                        })
                        .unwrap_or_else(|| {
                            spec.get("type")
                                .and_then(Value::as_str)
                                .unwrap_or("")
                                .to_string()
                        });
                    lines.push(format!("  {key}: {allowed}"));
                    if let Some(desc) = spec.get("description").and_then(Value::as_str) {
                        lines.push(format!("    {desc}"));
                    }
                }
                lines.push(String::new());
            }
        }
    }
    lines.join("\n")
}

pub fn search_polyhaven_text(
    query: Option<&str>,
    asset_type: &str,
    category: Option<&str>,
    attributes: Option<&Value>,
    min_size_m: Option<f64>,
    limit: i64,
) -> String {
    let conn = LiveConn::new();
    let result = match conn.send_command(
        "search_polyhaven_assets",
        serde_json::json!({
            "asset_type": asset_type,
            "category": category,
            "attributes": attributes.cloned().unwrap_or(Value::Null),
            "query": query,
            "limit": limit,
            "min_size_m": min_size_m,
        }),
    ) {
        Ok(r) => r,
        Err(e) => return format!("Error searching Polyhaven assets: {e}"),
    };
    if let Some(err) = err_in_result(&result) {
        return format!("Error: {err}");
    }
    let assets = result
        .get("assets")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let total = result
        .get("total_count")
        .and_then(Value::as_i64)
        .unwrap_or(assets.len() as i64);
    let mut header = if let Some(q) = result.get("query").and_then(Value::as_str) {
        format!("{total} assets on Poly Haven match '{q}'")
    } else {
        let mut h = format!("{total} assets on Poly Haven");
        if let Some(c) = category {
            h.push_str(&format!(" in {c}"));
        }
        if let Some(a) = attributes.and_then(Value::as_object) {
            if !a.is_empty() {
                let parts: Vec<String> = a.iter().map(|(k, v)| format!("{k}={v}")).collect();
                h.push_str(&format!(" ({})", parts.join(", ")));
            }
        }
        h.push_str(", most downloaded first");
        h
    };
    if let Some(m) = min_size_m {
        header.push_str(&format!(" (at least {m}m across)"));
    }
    let returned = result
        .get("returned_count")
        .and_then(Value::as_i64)
        .unwrap_or(assets.len() as i64);
    let mut lines = vec![header, format!("Showing {returned}:",), String::new()];
    if let Some(note) = result.get("note").and_then(Value::as_str) {
        lines.insert(1, note.to_string());
    }
    for asset in &assets {
        let name = asset.get("name").and_then(Value::as_str).unwrap_or("?");
        let id = asset.get("id").and_then(Value::as_str).unwrap_or("?");
        lines.push(format!("- {name} (ID: {id})"));
        let ty = asset.get("type").and_then(Value::as_str).unwrap_or("?");
        let url = asset.get("url").and_then(Value::as_str).unwrap_or("");
        lines.push(format!("  Type: {ty}  |  {url}"));
        if let Some(authors) = asset.get("authors").and_then(Value::as_array) {
            let a: Vec<&str> = authors.iter().filter_map(Value::as_str).collect();
            if !a.is_empty() {
                lines.push(format!("  By: {}", a.join(", ")));
            }
        }
        if let Some(c) = asset.get("category").and_then(Value::as_str) {
            lines.push(format!("  Category: {c}"));
        }
        if let Some(tags) = asset.get("tags").and_then(Value::as_array) {
            let t: Vec<&str> = tags.iter().filter_map(Value::as_str).collect();
            if !t.is_empty() {
                lines.push(format!("  Tags: {}", t.join(", ")));
            }
        }
        if let Some(attrs) = asset.get("attributes").and_then(Value::as_object) {
            let parts: Vec<String> = attrs
                .iter()
                .map(|(k, v)| {
                    if let Some(arr) = v.as_array() {
                        let items: Vec<String> = arr.iter().map(|x| x.to_string()).collect();
                        format!("{k}={}", items.join("/"))
                    } else {
                        format!("{k}={v}")
                    }
                })
                .collect();
            if !parts.is_empty() {
                lines.push(format!("  Attributes: {}", parts.join(", ")));
            }
        }
        if let Some(size) = asset.get("dimensions_mm").and_then(Value::as_array) {
            let metres: Vec<String> = size
                .iter()
                .filter_map(Value::as_f64)
                .map(|v| format!("{}m", v / 1000.0))
                .collect();
            let axes = if size.len() == 3 { " (W x D x H)" } else { "" };
            lines.push(format!("  Real-world size: {}{axes}", metres.join(" x ")));
        }
        if let Some(maxr) = asset.get("max_resolution").and_then(Value::as_array) {
            let r: Vec<String> = maxr.iter().map(|v| v.to_string()).collect();
            lines.push(format!("  Up to: {}", r.join("x")));
        }
        if let Some(d) = asset.get("downloads").and_then(Value::as_i64) {
            lines.push(format!("  Downloads: {d}"));
        }
        if let Some(desc) = asset.get("description").and_then(Value::as_str) {
            lines.push(format!("  {desc}"));
        }
        lines.push(String::new());
    }
    lines.push("Assets from Poly Haven (https://polyhaven.com), free and CC0.".to_string());
    lines.join("\n")
}

/// Fetch a Poly Haven preview thumbnail. Returns (bytes, format, name, authors, url).
pub fn polyhaven_preview(
    asset_id: &str,
) -> Result<(Vec<u8>, String, String, String, String), String> {
    let conn = LiveConn::new();
    let result = conn
        .send_command(
            "get_polyhaven_asset_preview",
            serde_json::json!({"asset_id": asset_id}),
        )
        .map_err(|e| format!("Failed to get preview: {e}"))?;
    if let Some(err) = err_in_result(&result) {
        return Err(format!("Failed to get preview: {err}"));
    }
    let b64 = result
        .get("image_data")
        .and_then(Value::as_str)
        .ok_or_else(|| "Failed to get preview: no image data".to_string())?;
    use base64::Engine as _;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|e| format!("Failed to get preview: {e}"))?;
    let format = result
        .get("format")
        .and_then(Value::as_str)
        .unwrap_or("png")
        .to_string();
    let name = result
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or(asset_id)
        .to_string();
    let authors = result
        .get("authors")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(", ")
        })
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "Poly Haven".to_string());
    let url = result
        .get("url")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    Ok((bytes, format, name, authors, url))
}

pub fn polyhaven_preview_note(asset_id: &str) -> Result<String, String> {
    let (bytes, format, name, authors, _url) = polyhaven_preview(asset_id)?;
    deliver_image(
        &bytes,
        &format,
        "get_polyhaven_asset_preview",
        &format!("Poly Haven preview of '{name}' by {authors}."),
    )
    .map_err(|e| e.to_string())
}

pub fn download_polyhaven_text(
    asset_id: &str,
    asset_type: &str,
    resolution: &str,
    file_format: Option<&str>,
) -> String {
    let conn = LiveConn::new();
    let result = match conn.send_command(
        "download_polyhaven_asset",
        serde_json::json!({
            "asset_id": asset_id, "asset_type": asset_type,
            "resolution": resolution, "file_format": file_format,
        }),
    ) {
        Ok(r) => r,
        Err(e) => return format!("Error downloading Polyhaven asset: {e}"),
    };
    if let Some(err) = err_in_result(&result) {
        return format!("Error: {err}");
    }
    if result
        .get("success")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        let mut message = result
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Asset downloaded and imported successfully")
            .to_string();
        match asset_type {
            "hdris" => message.push_str(" The HDRI has been set as the world environment."),
            "textures" => {
                let material = result.get("material").and_then(Value::as_str).unwrap_or("");
                let maps: Vec<&str> = result
                    .get("maps")
                    .and_then(Value::as_array)
                    .map(|a| a.iter().filter_map(Value::as_str).collect())
                    .unwrap_or_default();
                message.push_str(&format!(" Created material '{material}' with maps: {}. Nothing is using it yet - call set_texture to apply it to an object. Saving the file before then discards it, as Blender does with any unused datablock, and it would have to be downloaded again.{}", maps.join(", "), polyhaven_scale_note(&result)));
            }
            "models" => message.push_str(" The model has been imported into the current scene."),
            _ => {}
        }
        return format!("{message}\n\n{}", polyhaven_credit(&result));
    }
    format!(
        "Failed to download asset: {}",
        result
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Unknown error")
    )
}

pub fn set_texture_text(object_name: &str, texture_id: &str) -> String {
    let conn = LiveConn::new();
    let result = match conn.send_command(
        "set_texture",
        serde_json::json!({"object_name": object_name, "texture_id": texture_id}),
    ) {
        Ok(r) => r,
        Err(e) => return format!("Error applying texture: {e}"),
    };
    if let Some(err) = err_in_result(&result) {
        return format!("Error: {err}");
    }
    if result
        .get("success")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        let material = result.get("material").and_then(Value::as_str).unwrap_or("");
        let maps: Vec<&str> = result
            .get("maps")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        let info = result.get("material_info");
        let node_count = info
            .and_then(|i| i.get("node_count"))
            .and_then(Value::as_i64)
            .unwrap_or(0);
        let has_nodes = info
            .and_then(|i| i.get("has_nodes"))
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let texture_nodes: Vec<&Value> = info
            .and_then(|i| i.get("texture_nodes"))
            .and_then(Value::as_array)
            .map(|a| a.iter().collect())
            .unwrap_or_default();
        let mut output = format!("Successfully applied texture '{texture_id}' to {object_name}.\nUsing material '{material}' with maps: {}.\n\nMaterial has nodes: {has_nodes}\nTotal node count: {node_count}\n\n", maps.join(", "));
        if texture_nodes.is_empty() {
            output.push_str("No texture nodes found in the material.\n");
        } else {
            output.push_str("Texture nodes:\n");
            for node in texture_nodes {
                let nname = node.get("name").and_then(Value::as_str).unwrap_or("?");
                let img = node.get("image").and_then(Value::as_str).unwrap_or("?");
                output.push_str(&format!("- {nname} using image: {img}\n"));
                for c in node
                    .get("connections")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default()
                {
                    output.push_str(&format!("  Connections:\n    {c}\n"));
                }
            }
        }
        return format!("{output}\n{}", polyhaven_credit(&result));
    }
    format!(
        "Failed to apply texture: {}",
        result
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Unknown error")
    )
}

/// Simple enabled/message status probes (Poly Haven, Hyper3D, Sketchfab,
/// Poly Pizza, Hunyuan3D). `extra` is the appended "good at" line.
pub fn addon_probe_text(command: &str, extra: &str, tool: &str) -> String {
    let conn = LiveConn::new();
    match conn.send_command(command, Value::Null) {
        Ok(result) => {
            let mut message = result
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if result
                .get("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false)
                && !extra.is_empty()
            {
                message.push_str(extra);
            }
            message
        }
        Err(e) => format!("Error checking {tool} status: {e}"),
    }
}

pub fn search_sketchfab_text(
    query: &str,
    categories: Option<&str>,
    count: i64,
    downloadable: bool,
) -> String {
    let conn = LiveConn::new();
    let result = match conn.send_command(
        "search_sketchfab_models",
        serde_json::json!({
            "query": query, "categories": categories,
            "count": count, "downloadable": downloadable,
        }),
    ) {
        Ok(r) => r,
        Err(e) => return format!("Error searching Sketchfab models: {e}"),
    };
    if let Some(err) = err_in_result(&result) {
        return format!("Error: {err}");
    }
    let models = result
        .get("results")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let models: Vec<&Value> = models.iter().filter(|m| !m.is_null()).collect();
    if models.is_empty() {
        return format!("No models found matching '{query}'");
    }
    let mut out = format!("Found {} models matching '{query}':\n\n", models.len());
    for model in models {
        let name = model
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("Unnamed model");
        let uid = model
            .get("uid")
            .and_then(Value::as_str)
            .unwrap_or("Unknown ID");
        out.push_str(&format!("- {name} (UID: {uid})\n"));
        let username = model
            .get("user")
            .and_then(|u| u.get("username"))
            .and_then(Value::as_str)
            .unwrap_or("Unknown author");
        out.push_str(&format!("  Author: {username}\n"));
        let label = model
            .get("license")
            .and_then(|l| l.get("label"))
            .and_then(Value::as_str)
            .unwrap_or("Unknown");
        out.push_str(&format!("  License: {label}\n"));
        let faces = model
            .get("faceCount")
            .map(|v| v.to_string())
            .unwrap_or_else(|| "Unknown".to_string());
        let dl = if model
            .get("isDownloadable")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            "Yes"
        } else {
            "No"
        };
        out.push_str(&format!("  Face count: {faces}\n  Downloadable: {dl}\n\n"));
    }
    out
}

/// Fetch a Sketchfab preview thumbnail. Returns (bytes, format, model_name, author).
pub fn sketchfab_preview(uid: &str) -> Result<(Vec<u8>, String, String, String), String> {
    let conn = LiveConn::new();
    let result = conn
        .send_command(
            "get_sketchfab_model_preview",
            serde_json::json!({"uid": uid}),
        )
        .map_err(|e| format!("Failed to get preview: {e}"))?;
    if let Some(err) = err_in_result(&result) {
        return Err(format!("Failed to get preview: {err}"));
    }
    let b64 = result
        .get("image_data")
        .and_then(Value::as_str)
        .ok_or_else(|| "Failed to get preview: no image data".to_string())?;
    use base64::Engine as _;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|e| format!("Failed to get preview: {e}"))?;
    let format = result
        .get("format")
        .and_then(Value::as_str)
        .unwrap_or("jpeg")
        .to_string();
    let name = result
        .get("model_name")
        .and_then(Value::as_str)
        .unwrap_or("Unknown")
        .to_string();
    let author = result
        .get("author")
        .and_then(Value::as_str)
        .unwrap_or("Unknown")
        .to_string();
    Ok((bytes, format, name, author))
}

pub fn sketchfab_preview_note(uid: &str) -> Result<String, String> {
    let (bytes, format, name, author) = sketchfab_preview(uid)?;
    deliver_image(
        &bytes,
        &format,
        "get_sketchfab_model_preview",
        &format!("Sketchfab preview of '{name}' by {author}."),
    )
    .map_err(|e| e.to_string())
}

pub fn download_sketchfab_text(uid: &str, target_size: f64) -> String {
    let conn = LiveConn::new();
    let result = match conn.send_command(
        "download_sketchfab_model",
        serde_json::json!({"uid": uid, "normalize_size": true, "target_size": target_size}),
    ) {
        Ok(r) => r,
        Err(e) => return format!("Error downloading Sketchfab model: {e}"),
    };
    if let Some(err) = err_in_result(&result) {
        return format!("Error: {err}");
    }
    if result
        .get("success")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        let imported: Vec<&str> = result
            .get("imported_objects")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        let mut output = format!(
            "Successfully imported model.\nCreated objects: {}\n",
            if imported.is_empty() {
                "none".to_string()
            } else {
                imported.join(", ")
            }
        );
        if let Some(dims) = result.get("dimensions").and_then(Value::as_array) {
            let d: Vec<f64> = dims.iter().filter_map(Value::as_f64).collect();
            if d.len() == 3 {
                output.push_str(&format!(
                    "Dimensions (X, Y, Z): {:.3} x {:.3} x {:.3} meters\n",
                    d[0], d[1], d[2]
                ));
            }
        }
        if let Some(bbox) = result.get("world_bounding_box").and_then(Value::as_array) {
            if bbox.len() == 2 {
                output.push_str(&format!("Bounding box: min={}, max={}\n", bbox[0], bbox[1]));
            }
        }
        if result
            .get("normalized")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            let scale = result
                .get("scale_applied")
                .and_then(Value::as_f64)
                .unwrap_or(1.0);
            output.push_str(&format!(
                "Size normalized: scale factor {scale:.6} applied (target size: {target_size}m)\n"
            ));
        }
        return output;
    }
    format!(
        "Failed to download model: {}",
        result
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Unknown error")
    )
}

pub fn search_blenderkit_text(
    query: Option<&str>,
    asset_type: &str,
    free_only: bool,
    limit: i64,
) -> String {
    let conn = LiveConn::new();
    let result = match conn.send_command(
        "search_blenderkit_assets",
        serde_json::json!({
            "query": query, "asset_type": asset_type,
            "free_only": free_only, "limit": limit,
        }),
    ) {
        Ok(r) => r,
        Err(e) => return format!("Error searching BlenderKit assets: {e}"),
    };
    if let Some(err) = err_in_result(&result) {
        return format!("Error: {err}");
    }
    let assets = result
        .get("assets")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let shown = result
        .get("query")
        .and_then(Value::as_str)
        .map(str::to_string)
        .or_else(|| query.map(str::to_string))
        .unwrap_or_default();
    if assets.is_empty() {
        return format!("No BlenderKit assets matching '{shown}'");
    }
    let total = result
        .get("total_count")
        .and_then(Value::as_i64)
        .unwrap_or(assets.len() as i64);
    let mut out = format!("{total} BlenderKit assets matching '{shown}':\n\n");
    for asset in assets.iter().filter(|a| !a.is_null()) {
        let name = asset
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("Unnamed");
        let id = asset
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("?");
        out.push_str(&format!("- {name} (id: {id})\n"));
        let ty = asset
            .get("asset_type")
            .and_then(Value::as_str)
            .unwrap_or("?");
        let free = asset
            .get("free")
            .and_then(Value::as_bool)
            .map(|f| if f { "free" } else { "paid/private" })
            .unwrap_or("?");
        let licence = asset
            .get("license")
            .and_then(Value::as_str)
            .unwrap_or("?");
        out.push_str(&format!("  Type: {ty}, {free}, licence: {licence}\n"));
        let author = asset
            .get("author")
            .and_then(Value::as_str)
            .unwrap_or("Unknown");
        out.push_str(&format!("  Author: {author}\n"));
        if let Some(url) = asset.get("url").and_then(Value::as_str) {
            out.push_str(&format!("  {url}\n"));
        }
    }
    if let Some(note) = result.get("note").and_then(Value::as_str) {
        out.push_str(&format!("\n{note}"));
    }
    out
}

/// Fetch a BlenderKit preview thumbnail. Returns (bytes, format, name, author).
pub fn blenderkit_preview(asset_id: &str) -> Result<(Vec<u8>, String, String, String), String> {
    let conn = LiveConn::new();
    let result = conn
        .send_command(
            "get_blenderkit_asset_preview",
            serde_json::json!({"asset_id": asset_id}),
        )
        .map_err(|e| format!("Failed to get preview: {e}"))?;
    if let Some(err) = err_in_result(&result) {
        return Err(format!("Failed to get preview: {err}"));
    }
    let b64 = result
        .get("image_data")
        .and_then(Value::as_str)
        .ok_or_else(|| "Failed to get preview: no image data".to_string())?;
    use base64::Engine as _;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|e| format!("Failed to get preview: {e}"))?;
    let format = result
        .get("format")
        .and_then(Value::as_str)
        .unwrap_or("jpeg")
        .to_string();
    let name = result
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("Unknown")
        .to_string();
    let author = result
        .get("author")
        .and_then(Value::as_str)
        .unwrap_or("Unknown")
        .to_string();
    Ok((bytes, format, name, author))
}

pub fn blenderkit_preview_note(asset_id: &str) -> Result<String, String> {
    let (bytes, format, name, author) = blenderkit_preview(asset_id)?;
    deliver_image(
        &bytes,
        &format,
        "get_blenderkit_asset_preview",
        &format!("BlenderKit preview of '{name}' by {author}."),
    )
    .map_err(|e| e.to_string())
}

pub fn download_blenderkit_text(
    asset_id: &str,
    asset_type: &str,
    resolution: &str,
    target_size: Option<f64>,
) -> String {
    let conn = LiveConn::new();
    let result = match conn.send_command(
        "download_blenderkit_asset",
        serde_json::json!({
            "asset_id": asset_id, "asset_type": asset_type,
            "resolution": resolution, "target_size": target_size,
        }),
    ) {
        Ok(r) => r,
        Err(e) => return format!("Error downloading BlenderKit asset: {e}"),
    };
    if let Some(err) = err_in_result(&result) {
        return format!("Error: {err}");
    }
    if result
        .get("success")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        let mut lines = vec![result
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("BlenderKit asset imported successfully.")
            .to_string()];
        if let Some(objs) = result.get("imported_objects").and_then(Value::as_array) {
            let names: Vec<&str> = objs.iter().filter_map(Value::as_str).collect();
            if !names.is_empty() {
                lines.push(format!("Created objects: {}", names.join(", ")));
            }
        }
        for key in ["material", "world", "author", "license"] {
            if let Some(v) = result.get(key).and_then(Value::as_str) {
                let label = key[..1].to_uppercase() + &key[1..];
                lines.push(format!("{label}: {v}"));
            }
        }
        return lines.join("\n");
    }
    format!(
        "Failed to download asset: {}",
        result
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Unknown error")
    )
}

pub fn search_polypizza_text(
    query: &str,
    category: &Value,
    licence: &Value,
    animated: bool,
    limit: i64,
) -> String {
    let category_id = match polypizza_category_id(category) {
        Ok(v) => v,
        Err(e) => return format!("Error: {e}"),
    };
    let licence_id = match polypizza_licence_id(licence) {
        Ok(v) => v,
        Err(e) => return format!("Error: {e}"),
    };
    if query.trim().is_empty() && category_id.is_none() && licence_id.is_none() && !animated {
        return "Error: Poly Pizza needs a search keyword or at least one filter (category, licence, or animated=True).".to_string();
    }
    let conn = LiveConn::new();
    let result = match conn.send_command(
        "search_polypizza_models",
        serde_json::json!({
            "query": query, "category": category_id,
            "licence": licence_id, "animated": animated, "limit": limit,
        }),
    ) {
        Ok(r) => r,
        Err(e) => return format!("Error searching Poly Pizza models: {e}"),
    };
    if let Some(err) = err_in_result(&result) {
        return format!("Error: {err}");
    }
    let models = result
        .get("results")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let models: Vec<&Value> = models.iter().filter(|m| !m.is_null()).collect();
    if models.is_empty() {
        let described = if query.is_empty() {
            "the requested filters".to_string()
        } else {
            query.to_string()
        };
        return format!("No models found matching '{described}'");
    }
    let total = result
        .get("total")
        .and_then(Value::as_i64)
        .unwrap_or(models.len() as i64);
    let described = if query.is_empty() {
        "the given filters".to_string()
    } else {
        query.to_string()
    };
    let mut out = format!(
        "Found {} models (of {total} total) matching '{described}':\n\n",
        models.len()
    );
    for model in models {
        let name = model
            .get("Title")
            .and_then(Value::as_str)
            .unwrap_or("Unnamed model");
        let id = model
            .get("ID")
            .map(|v| v.to_string())
            .unwrap_or_else(|| "Unknown ID".to_string());
        out.push_str(&format!("- {name} (ID: {id})\n"));
        out.push_str(&format!(
            "  Author: {}\n",
            model
                .get("Creator")
                .and_then(Value::as_str)
                .unwrap_or("Unknown author")
        ));
        out.push_str(&format!(
            "  Licence: {}\n",
            model
                .get("Licence")
                .and_then(Value::as_str)
                .unwrap_or("Unknown")
        ));
        out.push_str(&format!(
            "  Tri count: {}\n",
            model
                .get("Tri Count")
                .map(|v| v.to_string())
                .unwrap_or_else(|| "Unknown".to_string())
        ));
        out.push_str(&format!(
            "  Category: {}\n",
            model
                .get("Category")
                .and_then(Value::as_str)
                .unwrap_or("Unknown")
        ));
        out.push_str(&format!(
            "  Animated: {}\n\n",
            if model
                .get("Animated")
                .and_then(Value::as_bool)
                .unwrap_or(false)
            {
                "Yes"
            } else {
                "No"
            }
        ));
    }
    out.push_str("CC-BY models must be credited. download_polypizza_model() stores the required attribution string on the imported object as a custom property.\n");
    out
}

pub fn download_polypizza_text(model_id: &str, normalize_size: bool, target_size: f64) -> String {
    let conn = LiveConn::new();
    let result = match conn.send_command(
        "download_polypizza_model",
        serde_json::json!({"model_id": model_id, "normalize_size": normalize_size, "target_size": target_size}),
    ) {
        Ok(r) => r,
        Err(e) => return format!("Error downloading Poly Pizza model: {e}"),
    };
    if let Some(err) = err_in_result(&result) {
        return format!("Error: {err}");
    }
    if result
        .get("success")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        let imported: Vec<&str> = result
            .get("imported_objects")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        let mut output = format!(
            "Successfully imported model.\nCreated objects: {}\n",
            if imported.is_empty() {
                "none".to_string()
            } else {
                imported.join(", ")
            }
        );
        if let Some(t) = result.get("title").and_then(Value::as_str) {
            output.push_str(&format!("Title: {t}\n"));
        }
        if let Some(tri) = result.get("tri_count") {
            output.push_str(&format!("Tri count: {tri}\n"));
        }
        if let Some(dims) = result.get("dimensions").and_then(Value::as_array) {
            let d: Vec<f64> = dims.iter().filter_map(Value::as_f64).collect();
            if d.len() == 3 {
                output.push_str(&format!(
                    "Dimensions (X, Y, Z): {:.3} x {:.3} x {:.3} meters\n",
                    d[0], d[1], d[2]
                ));
            }
        }
        if let Some(bbox) = result.get("world_bounding_box").and_then(Value::as_array) {
            if bbox.len() == 2 {
                output.push_str(&format!("Bounding box: min={}, max={}\n", bbox[0], bbox[1]));
            }
        }
        if result
            .get("normalized")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            let scale = result
                .get("scale_applied")
                .and_then(Value::as_f64)
                .unwrap_or(1.0);
            output.push_str(&format!(
                "Size normalized: scale factor {scale:.6} applied (target size: {target_size}m)\n"
            ));
        }
        output.push_str(&format!(
            "Licence: {}\n",
            result
                .get("licence")
                .and_then(Value::as_str)
                .unwrap_or("Unknown")
        ));
        if let Some(attr) = result.get("attribution").and_then(Value::as_str) {
            output.push_str(&format!("Attribution: {attr}\nStored on the imported object as polypizza_attribution. Surface it to the user if the licence is CC-BY.\n"));
        }
        return output;
    }
    format!(
        "Failed to download model: {}",
        result
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Unknown error")
    )
}

pub fn rodin_text_job(text_prompt: &str, bbox: &Value) -> String {
    let bbox_condition = match process_bbox(bbox) {
        Ok(v) => v
            .map(|a| a.into_iter().map(Value::from).collect::<Vec<_>>())
            .map(Value::Array)
            .unwrap_or(Value::Null),
        Err(e) => return format!("Error generating Hyper3D task: {e}"),
    };
    let conn = LiveConn::new();
    match conn.send_command(
        "create_rodin_job",
        serde_json::json!({"text_prompt": text_prompt, "images": Value::Null, "bbox_condition": bbox_condition}),
    ) {
        Ok(result) => {
            if result.get("submit_time").and_then(Value::as_bool).unwrap_or(false) {
                let uuid = result.get("uuid").and_then(Value::as_str).unwrap_or("");
                let key = result.get("jobs").and_then(|j| j.get("subscription_key")).and_then(Value::as_str).unwrap_or("");
                return serde_json::json!({"task_uuid": uuid, "subscription_key": key}).to_string();
            }
            result.to_string()
        }
        Err(e) => format!("Error generating Hyper3D task: {e}"),
    }
}

pub fn rodin_images_job(paths: Option<&[String]>, urls: Option<&[String]>, bbox: &Value) -> String {
    if paths.is_some() && urls.is_some() {
        return "Error: Conflict parameters given!".to_string();
    }
    let images: Value = if let Some(paths) = paths {
        let mut imgs = vec![];
        for path in paths {
            if !std::path::Path::new(path).exists() {
                return "Error: not all image paths are valid!".to_string();
            }
            match std::fs::read(path) {
                Ok(bytes) => {
                    use base64::Engine as _;
                    let ext = std::path::Path::new(path)
                        .extension()
                        .and_then(|e| e.to_str())
                        .unwrap_or("");
                    imgs.push(serde_json::json!([
                        ext,
                        base64::engine::general_purpose::STANDARD.encode(&bytes)
                    ]));
                }
                Err(e) => return format!("Error generating Hyper3D task: {e}"),
            }
        }
        Value::Array(imgs)
    } else if let Some(urls) = urls {
        if urls.iter().any(|u| !u.contains("://")) {
            return "Error: not all image URLs are valid!".to_string();
        }
        Value::Array(urls.iter().map(|u| Value::from(u.clone())).collect())
    } else {
        return "Error: No image given!".to_string();
    };
    let bbox_condition = match process_bbox(bbox) {
        Ok(v) => v
            .map(|a| a.into_iter().map(Value::from).collect::<Vec<_>>())
            .map(Value::Array)
            .unwrap_or(Value::Null),
        Err(e) => return format!("Error generating Hyper3D task: {e}"),
    };
    let conn = LiveConn::new();
    match conn.send_command(
        "create_rodin_job",
        serde_json::json!({"text_prompt": Value::Null, "images": images, "bbox_condition": bbox_condition}),
    ) {
        Ok(result) => {
            if result.get("submit_time").and_then(Value::as_bool).unwrap_or(false) {
                let uuid = result.get("uuid").and_then(Value::as_str).unwrap_or("");
                let key = result.get("jobs").and_then(|j| j.get("subscription_key")).and_then(Value::as_str).unwrap_or("");
                return serde_json::json!({"task_uuid": uuid, "subscription_key": key}).to_string();
            }
            result.to_string()
        }
        Err(e) => format!("Error generating Hyper3D task: {e}"),
    }
}

pub fn poll_rodin_text(subscription_key: Option<&str>, request_id: Option<&str>) -> String {
    let conn = LiveConn::new();
    let mut kwargs = serde_json::Map::new();
    if let Some(k) = subscription_key.filter(|s| !s.is_empty()) {
        kwargs.insert("subscription_key".to_string(), Value::from(k));
    } else if let Some(r) = request_id.filter(|s| !s.is_empty()) {
        kwargs.insert("request_id".to_string(), Value::from(r));
    }
    match conn.send_command("poll_rodin_job_status", Value::Object(kwargs)) {
        Ok(r) => r.to_string(),
        Err(e) => format!("Error generating Hyper3D task: {e}"),
    }
}

pub fn import_generated_text(
    name: &str,
    task_uuid: Option<&str>,
    request_id: Option<&str>,
) -> String {
    let conn = LiveConn::new();
    let mut kwargs = serde_json::Map::new();
    kwargs.insert("name".to_string(), Value::from(name));
    if let Some(u) = task_uuid.filter(|s| !s.is_empty()) {
        kwargs.insert("task_uuid".to_string(), Value::from(u));
    } else if let Some(r) = request_id.filter(|s| !s.is_empty()) {
        kwargs.insert("request_id".to_string(), Value::from(r));
    }
    match conn.send_command("import_generated_asset", Value::Object(kwargs)) {
        Ok(r) => r.to_string(),
        Err(e) => format!("Error generating Hyper3D task: {e}"),
    }
}

pub fn generate_hunyuan_text(text_prompt: Option<&str>, image: Option<&str>) -> String {
    let conn = LiveConn::new();
    match conn.send_command(
        "create_hunyuan_job",
        serde_json::json!({"text_prompt": text_prompt, "image": image}),
    ) {
        Ok(result) => {
            if let Some(job) = result
                .get("Response")
                .and_then(|r| r.get("JobId"))
                .and_then(Value::as_str)
            {
                return serde_json::json!({"job_id": format!("job_{job}")}).to_string();
            }
            result.to_string()
        }
        Err(e) => format!("Error generating Hunyuan3D task: {e}"),
    }
}

pub fn poll_hunyuan_text(job_id: Option<&str>) -> String {
    let conn = LiveConn::new();
    match conn.send_command(
        "poll_hunyuan_job_status",
        serde_json::json!({"job_id": job_id}),
    ) {
        Ok(r) => r.to_string(),
        Err(e) => format!("Error generating Hunyuan3D task: {e}"),
    }
}

pub fn import_hunyuan_text(name: &str, zip_file_url: Option<&str>) -> String {
    let conn = LiveConn::new();
    let mut kwargs = serde_json::Map::new();
    kwargs.insert("name".to_string(), Value::from(name));
    if let Some(u) = zip_file_url.filter(|s| !s.is_empty()) {
        kwargs.insert("zip_file_url".to_string(), Value::from(u));
    }
    match conn.send_command("import_generated_asset_hunyuan", Value::Object(kwargs)) {
        Ok(r) => r.to_string(),
        Err(e) => format!("Error generating Hunyuan3D task: {e}"),
    }
}

pub fn export_scene_text(
    filepath: &str,
    format: &str,
    object_names: Option<&[String]>,
    selection_only: bool,
    apply_modifiers: bool,
) -> String {
    let conn = LiveConn::new();
    match conn.send_command(
        "export_scene",
        serde_json::json!({
            "filepath": filepath, "format": format,
            "object_names": object_names.map(|o| o.to_vec()),
            "selection_only": selection_only, "apply_modifiers": apply_modifiers,
        }),
    ) {
        Ok(result) => {
            if result.is_object() {
                result.to_string()
            } else if let Some(s) = result.as_str() {
                s.to_string()
            } else {
                result.to_string()
            }
        }
        Err(e) => format!("Error exporting scene: {e}"),
    }
}

pub fn feedback_text(
    feedback: &str,
    correction_text: Option<&str>,
    step_index: Option<i64>,
    goal_text: Option<&str>,
) -> String {
    const ALLOWED: &[&str] = &["accept", "reject", "undo", "redo", "correction"];
    if !ALLOWED.contains(&feedback) {
        let mut sorted = ALLOWED.to_vec();
        sorted.sort();
        return format!("Error: feedback must be one of {sorted:?}");
    }
    if record_feedback(feedback, correction_text, step_index, goal_text) {
        "Trajectory feedback recorded".to_string()
    } else {
        "Trajectory feedback skipped (telemetry disabled, no consent, or write failed)".to_string()
    }
}

/// Inline-image MIME type for a preview/screenshot format name.
pub fn mime_for(format: &str) -> &'static str {
    match format.to_lowercase().as_str() {
        "jpeg" | "jpg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        _ => "image/png",
    }
}

pub const ASSET_CREATION_STRATEGY_TITLE: &str =
    "Defines the preferred strategy for creating assets in Blender";

pub const ASSET_CREATION_STRATEGY: &str = "When creating 3D content in Blender, always start by checking if integrations are available:\n\n    0. Before anything, always check the scene from get_scene_info()\n    \n    **IMPORTANT: Visual Verification**\n    - Use get_viewport_screenshot() BEFORE making changes to see the current state\n    - Use get_viewport_screenshot() AFTER executing code or importing assets to verify the result\n    - This helps confirm your changes worked as expected and catch any visual issues\n\n    **IMPORTANT: Trajectory feedback**\n    - When the user accepts a result (\"looks good\", \"keep that\"), call record_trajectory_feedback(feedback=\"accept\")\n    - When they reject or ask to undo, call record_trajectory_feedback(feedback=\"reject\" or \"undo\")\n    - When they correct you (\"too dark\", \"make it taller\"), call record_trajectory_feedback(feedback=\"correction\", correction_text=<their correction>)\n    1. First use the following tools to verify if the following integrations are enabled:\n        1. PolyHaven\n            Use get_polyhaven_status() to verify its status\n            If PolyHaven is enabled:\n            - For objects/models: Use download_polyhaven_asset() with asset_type=\"models\"\n            - For materials/textures: Use download_polyhaven_asset() with asset_type=\"textures\"\n            - For environment lighting: Use download_polyhaven_asset() with asset_type=\"hdris\"\n        2. Sketchfab\n            Sketchfab is good at Realistic models, and has a wider variety of models than PolyHaven.\n            Use get_sketchfab_status() to verify its status\n            If Sketchfab is enabled:\n            - For objects/models: First search using search_sketchfab_models() with your query\n            - Then download specific models using download_sketchfab_model() with the UID\n            - Note that only downloadable models can be accessed, and API key must be properly configured\n            - Sketchfab has a wider variety of models than PolyHaven, especially for specific subjects\n        3. Poly Pizza\n            Poly Pizza is best for stylised, low-poly game assets (it includes the rescued Google Poly archive).\n            Everything on it is free under CC0 or CC-BY, every model is a single self-contained GLB, and the\n            geometry is much lighter than Sketchfab's - prefer it when the scene wants a consistent stylised\n            look, or when many props are needed without heavy meshes.\n            Use get_polypizza_status() to verify its status\n            If Poly Pizza is enabled:\n            - For objects/models: First search using search_polypizza_models(), optionally filtering by\n              category (e.g. \"Animals\", \"Furniture & Decor\"), licence (\"CC0\" or \"CC-BY\"), or animated=True\n            - Then import a specific model using download_polypizza_model() with its ID, passing\n              normalize_size=True and a real-world target_size: Poly Pizza models come from the Google Poly\n              archive and their scale and origins are arbitrary\n            - About 69% of the catalogue is CC-BY, which REQUIRES crediting the creator. The ready-formatted\n              attribution string is returned by download_polypizza_model() and is also stored on the imported\n              object as the custom property polypizza_attribution, so tell the user about it when the model\n              is CC-BY. Filter with licence=\"CC0\" if you want models that need no credit.\n        4. BlenderKit\n            BlenderKit has the largest library of the lot: photoscanned props, furniture, materials\n            and HDRs. Free assets need no account; private and paid ones need a BlenderKit API key\n            (BLENDERKIT_API_KEY env var or the add-on preferences).\n            Use get_blenderkit_status() to verify its status\n            If BlenderKit is enabled:\n            - For objects/models: first search with search_blenderkit_assets() (asset_type=\"model\",\n              free_only=True by default), preview with get_blenderkit_asset_preview(), then import\n              with download_blenderkit_asset() passing target_size in metres (a chair is ~1.0,\n              a car ~4.5)\n            - For materials: search with asset_type=\"material\", then import with\n              download_blenderkit_asset(); select the target mesh objects first so the material\n              is applied to them, otherwise it waits in the material list by name\n            - For environment lighting: search with asset_type=\"hdr\", then import with\n              download_blenderkit_asset() (resolution=\"2K\" by default, up to \"8K\")\n            - Unlike Poly Haven this library is NOT all-CC0: every result carries its own\n              licence, so check it before commercial use and credit the author when required\n        5. Hyper3D(Rodin)\n            Hyper3D Rodin is good at generating 3D models for single item.\n            So don't try to:\n            1. Generate the whole scene with one shot\n            2. Generate ground using Hyper3D\n            3. Generate parts of the items separately and put them together afterwards\n\n            Use get_hyper3d_status() to verify its status\n            If Hyper3D is enabled:\n            - For objects/models, do the following steps:\n                1. Create the model generation task\n                    - Use generate_hyper3d_model_via_images() if image(s) is/are given\n                    - Use generate_hyper3d_model_via_text() if generating 3D asset using text prompt\n                    If key type is free_trial and insufficient balance error returned, tell the user that the free trial key can only generated limited models everyday, they can choose to:\n                    - Wait for another day and try again\n                    - Go to hyper3d.ai to find out how to get their own API key\n                    - Go to fal.ai to get their own private API key\n                2. Poll the status\n                    - Use poll_rodin_job_status() to check if the generation task has completed or failed\n                3. Import the asset\n                    - Use import_generated_asset() to import the generated GLB model the asset\n                4. After importing the asset, ALWAYS check the world_bounding_box of the imported mesh, and adjust the mesh's location and size\n                    Adjust the imported mesh's location, scale, rotation, so that the mesh is on the right spot.\n\n                You can reuse assets previous generated by running python code to duplicate the object, without creating another generation task.\n        6. Hunyuan3D\n            Hunyuan3D is good at generating 3D models for single item.\n            So don't try to:\n            1. Generate the whole scene with one shot\n            2. Generate ground using Hunyuan3D\n            3. Generate parts of the items separately and put them together afterwards\n\n            Use get_hunyuan3d_status() to verify its status\n            If Hunyuan3D is enabled:\n                if Hunyuan3D mode is \"OFFICIAL_API\":\n                    - For objects/models, do the following steps:\n                        1. Create the model generation task\n                            - Use generate_hunyuan3d_model by providing either a **text description** OR an **image(local or urls) reference**.\n                            - Go to cloud.tencent.com out how to get their own SecretId and SecretKey\n                        2. Poll the status\n                            - Use poll_hunyuan_job_status() to check if the generation task has completed or failed\n                        3. Import the asset\n                            - Use import_generated_asset_hunyuan() with a ResultFile3Ds URL (prefer .glb, else .zip/.obj)\n                    if Hunyuan3D mode is \"LOCAL_API\":\n                        - For objects/models, do the following steps:\n                        1. Create the model generation task\n                            - Use generate_hunyuan3d_model if image (local or urls)  or text prompt is given and import the asset\n\n                You can reuse assets previous generated by running python code to duplicate the object, without creating another generation task.\n\n    3. Always check the world_bounding_box for each item so that:\n        - Ensure that all objects that should not be clipping are not clipping.\n        - Items have right spatial relationship.\n    \n    4. Recommended asset source priority:\n        - For specific existing objects: First try BlenderKit, then Sketchfab, then PolyHaven\n        - For stylised or low-poly game assets: First try Poly Pizza, then Sketchfab\n        - For generic objects/furniture: First try BlenderKit, then PolyHaven\n        - For custom or unique items not available in any libraries: Use Hyper3D Rodin or Hunyuan3D\n        - For environment lighting: Use PolyHaven HDRIs, then BlenderKit HDRs\n        - For materials/textures: Use PolyHaven textures, then BlenderKit materials\n\n    Only fall back to scripting when:\n    - PolyHaven, Sketchfab, Poly Pizza, BlenderKit, Hyper3D, and Hunyuan3D are all disabled\n    - A simple primitive is explicitly requested\n    - No suitable asset exists in any of the libraries\n    - Hyper3D Rodin or Hunyuan3D failed to generate the desired asset\n    - The task specifically requires a basic material/color\n\n    **Best Practices:**\n    - Always take a screenshot after completing a task to verify the visual result\n    - Always call get_scene_info() after completing a task to verify the changes worked\n    - When executing multiple operations, take intermediate screenshots to confirm each step\n    - If something looks wrong in the screenshot or scene info, investigate and fix before proceeding\n\n    **Writing code that survives the user's Blender version and language:**\n    - Read `blender_version` from get_addon_status() before using version-sensitive APIs\n    - Look shader nodes up by type, not by name: `nodes[\"Principled BSDF\"]` is None on a\n      localized (non-English) Blender UI\n    - Never hardcode enum identifiers; read the valid values from `bl_rna` first. `render.engine`\n      is the exception - RNA under-reports it, so read the current value or assign inside\n      `try/except TypeError` and use the identifiers listed in the error\n    - Set colors on shader node inputs; `material.diffuse_color` is viewport-only\n";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn polypizza_category_coercion() {
        assert_eq!(polypizza_category_id(&Value::Null), Ok(None));
        assert_eq!(
            polypizza_category_id(&serde_json::json!("Animals")),
            Ok(Some(7))
        );
        assert_eq!(
            polypizza_category_id(&serde_json::json!("props")),
            Ok(Some(5))
        );
        assert_eq!(polypizza_category_id(&serde_json::json!(3)), Ok(Some(3)));
        assert!(polypizza_category_id(&serde_json::json!("Nope")).is_err());
        assert!(polypizza_category_id(&serde_json::json!(99)).is_err());
    }

    #[test]
    fn polypizza_licence_coercion() {
        assert_eq!(polypizza_licence_id(&serde_json::json!("CC0")), Ok(Some(1)));
        assert_eq!(
            polypizza_licence_id(&serde_json::json!("CC-BY")),
            Ok(Some(0))
        );
        assert!(polypizza_licence_id(&serde_json::json!("MIT")).is_err());
    }

    #[test]
    fn bbox_processing() {
        assert_eq!(process_bbox(&Value::Null), Ok(None));
        assert_eq!(
            process_bbox(&serde_json::json!([1, 2, 3])),
            Ok(Some(vec![1, 2, 3]))
        );
        assert!(process_bbox(&serde_json::json!([-1, 2])).is_err());
        // Floats scale to 0..100 ints.
        assert_eq!(
            process_bbox(&serde_json::json!([0.5, 1.0])),
            Ok(Some(vec![50, 100]))
        );
    }

    #[test]
    fn credit_line_is_cc0() {
        let r = serde_json::json!({"authors": ["Jane"], "url": "https://polyhaven.com/a/x"});
        let c = polyhaven_credit(&r);
        assert!(c.contains("Jane") && c.contains("CC0"));
    }
}
