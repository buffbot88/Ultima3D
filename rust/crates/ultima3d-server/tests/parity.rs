//! Tool-surface parity: the Rust host must expose all 65 tools.
//! Needs no Blender/Godot/Photoshop — only initialize + tools/list.

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use std::time::Duration;

fn rpc(lines: &[&str]) -> Vec<serde_json::Value> {
    let exe = env!("CARGO_BIN_EXE_ultima3d-server");
    let mut child = Command::new(exe)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn ultima3d-server");
    {
        let stdin = child.stdin.as_mut().expect("stdin");
        for line in lines {
            writeln!(stdin, "{line}").expect("write");
        }
    }
    let stdout = child.stdout.take().expect("stdout");
    let reader = BufReader::new(stdout);
    let mut out = vec![];
    // Notifications get no response: expect one reply per request id.
    let want: usize = lines.iter().filter(|l| l.contains("\"id\"")).count();
    for line in reader.lines().take(want) {
        let line = line.expect("read");
        out.push(serde_json::from_str(&line).expect("json"));
    }
    let _ = child.kill();
    let _ = child.wait_timeout(Duration::from_secs(5));
    out
}

// Small wait_timeout helper so a hung child cannot stall CI.
trait WaitTimeout {
    fn wait_timeout(&mut self, d: Duration) -> std::io::Result<()>;
}

impl WaitTimeout for std::process::Child {
    fn wait_timeout(&mut self, d: Duration) -> std::io::Result<()> {
        let start = std::time::Instant::now();
        loop {
            match self.try_wait()? {
                Some(_) => return Ok(()),
                None if start.elapsed() >= d => return Ok(()),
                None => std::thread::sleep(Duration::from_millis(50)),
            }
        }
    }
}

const EXPECTED_TOOLS: &[&str] = &[
    // Procedural worker + pipeline (18).
    "create_3d",
    "compile_blueprint",
    "refine_asset",
    "inspect_asset",
    "render_asset",
    "set_material",
    "set_geometry",
    "validate_asset",
    "check_assertions",
    "finalize_asset",
    "rig_asset",
    "save_recipe",
    "load_recipe",
    "ultima3d_status",
    "reset_scene",
    "ultima3d_status_all",
    "export_asset_to_godot",
    "review_asset_loop",
    // Godot bridge (6).
    "godot_status",
    "godot_scene_tree",
    "godot_run_project",
    "godot_stop_project",
    "godot_debug_output",
    "godot_screenshot",
    // Photoshop CS3 (5).
    "photoshop_status",
    "photoshop_exec",
    "photoshop_create_image",
    "photoshop_run_script",
    "photoshop_make_texture",
    // Live Blender GUI (32).
    "get_addon_status",
    "disable_telemetry",
    "get_scene_info",
    "get_object_info",
    "get_viewport_screenshot",
    "execute_blender_code",
    "describe_node_type",
    "bpy_api_lookup",
    "get_polyhaven_categories",
    "search_polyhaven_assets",
    "get_polyhaven_asset_preview",
    "download_polyhaven_asset",
    "set_texture",
    "get_polyhaven_status",
    "get_hyper3d_status",
    "get_sketchfab_status",
    "search_sketchfab_models",
    "get_sketchfab_model_preview",
    "download_sketchfab_model",
    "get_polypizza_status",
    "search_polypizza_models",
    "download_polypizza_model",
    "generate_hyper3d_model_via_text",
    "generate_hyper3d_model_via_images",
    "poll_rodin_job_status",
    "import_generated_asset",
    "get_blenderkit_status",
    "search_blenderkit_assets",
    "get_blenderkit_asset_preview",
    "download_blenderkit_asset",
    "get_hunyuan3d_status",
    "generate_hunyuan3d_model",
    "poll_hunyuan_job_status",
    "import_generated_asset_hunyuan",
    "export_scene",
    "record_trajectory_feedback",
];

#[test]
fn all_sixty_five_tools_listed() {
    assert_eq!(EXPECTED_TOOLS.len(), 65, "test itself must expect 65 tools");
    let res = rpc(&[
        r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"parity","version":"0"}}}"#,
        r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#,
        r#"{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}"#,
        r#"{"jsonrpc":"2.0","id":3,"method":"prompts/list","params":{}}"#,
    ]);
    assert_eq!(
        res.len(),
        3,
        "one response per request (none for the notification)"
    );
    let by_id = |id: i64| res.iter().find(|r| r["id"] == id).expect("response id");
    assert!(by_id(1)["result"]["serverInfo"]["name"] == "ultima3d");

    let tools = by_id(2)["result"]["tools"].as_array().expect("tools array");
    let mut names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
    names.sort_unstable();
    let mut expected: Vec<&str> = EXPECTED_TOOLS.to_vec();
    expected.sort_unstable();
    assert_eq!(names, expected, "tool surface drift");

    let prompts = by_id(3)["result"]["prompts"]
        .as_array()
        .expect("prompts array");
    assert!(
        prompts
            .iter()
            .any(|p| p["name"] == "asset_creation_strategy"),
        "asset_creation_strategy prompt present"
    );
}
