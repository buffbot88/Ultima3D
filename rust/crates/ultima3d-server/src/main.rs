//! Ultima3D MCP server (Rust host): one server, four backends — procedural
//! worker, live Blender GUI, Godot editor bridge, Photoshop CS3 tools.
//!
//! The Blender geometry worker itself stays Python: it runs inside
//! Blender's embedded interpreter (blender/worker.py), which exposes no
//! Rust API. Everything else is Rust.

mod agentic;
mod godot;
mod live;
mod photoshop;
mod safe_mode;
mod textures;
mod worker;

use std::env;
use std::path::PathBuf;

use rmcp::{
    handler::server::{
        router::{prompt::PromptRouter, tool::ToolRouter},
        wrapper::Parameters,
    },
    model::{        CallToolResult, ContentBlock, PromptMessage, ProtocolVersion, Role, ServerConfig,
    },
    prompt, prompt_handler, prompt_router, tool, tool_handler, tool_router,
    transport::stdio,
    ErrorData as McpError, ServerHandler, ServiceExt,
};
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::{Map, Value};

use worker::Worker;

fn env_path(name: &str, fallback: String) -> PathBuf {
    PathBuf::from(env::var(name).unwrap_or(fallback))
}

fn out_dir(sub: &str) -> PathBuf {
    let base = env_path(
        "ULTIMA3D_OUT",
        std::env::current_dir()
            .unwrap_or(PathBuf::from("."))
            .join("output")
            .to_string_lossy()
            .into_owned(),
    );
    let p = if sub.is_empty() { base } else { base.join(sub) };
    let _ = std::fs::create_dir_all(&p);
    p
}

#[derive(Debug, Deserialize, JsonSchema)]
struct Create3dArgs {
    recipe: Map<String, Value>,
    #[serde(default = "default_name")]
    name: String,
    #[serde(default)]
    blueprint: Option<Map<String, Value>>,
    #[serde(default = "default_true")]
    join: bool,
}

fn default_name() -> String {
    "asset".to_string()
}

fn default_true() -> bool {
    true
}

fn default_resolution() -> u32 {
    512
}

fn default_budget() -> i64 {
    8000
}

fn default_tex_scale() -> f64 {
    3.0
}

fn default_tex_blend() -> f64 {
    0.5
}

fn default_collision() -> String {
    "auto".to_string()
}

fn default_tex_res() -> i64 {
    1024
}

fn ok_text(s: String) -> Result<CallToolResult, McpError> {
    Ok(CallToolResult::success(vec![ContentBlock::text(s)]))
}

#[derive(Debug, Deserialize, JsonSchema)]
struct BlueprintArgs {
    blueprint: Map<String, Value>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct RefineAssetArgs {
    #[serde(default)]
    changes: Option<Map<String, Value>>,
    #[serde(default)]
    recipe: Option<Map<String, Value>>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct RenderAssetArgs {
    #[serde(default)]
    views: Option<Vec<String>>,
    #[serde(default = "default_resolution")]
    resolution: u32,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct SetMaterialArgs {
    name: String,
    #[serde(default)]
    color: Option<Vec<f64>>,
    #[serde(default)]
    roughness: Option<f64>,
    #[serde(default)]
    metallic: Option<f64>,
    #[serde(default)]
    object: Option<String>,
    #[serde(default)]
    texture: Option<String>,
    #[serde(default = "default_tex_scale")]
    tex_scale: f64,
    #[serde(default = "default_tex_blend")]
    tex_blend: f64,
    #[serde(default)]
    tex_seed: i64,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct SetGeometryArgs {
    builder: String,
    node_name: String,
    #[serde(default)]
    params: Option<Map<String, Value>>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct BudgetArgs {
    #[serde(default = "default_budget")]
    triangle_budget: i64,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct AssertionsArgs {
    assertions: Vec<Value>,
    #[serde(default)]
    materials: Option<Map<String, Value>>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct FinalizeArgs {
    #[serde(default)]
    name: Option<String>,
    #[serde(default = "default_budget")]
    triangle_budget: i64,
    #[serde(default)]
    lods: Option<Vec<i64>>,
    #[serde(default = "default_collision")]
    collision: String,
    #[serde(default = "default_tex_res")]
    texture_resolution: i64,
    #[serde(default)]
    channels: Option<Vec<String>>,
    #[serde(default = "default_true")]
    bake: bool,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct RigArgs {
    #[serde(default)]
    bones: Option<i64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct PathArgs {
    path: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct LoadRecipeArgs {
    path: String,
    #[serde(default = "default_true")]
    rebuild: bool,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ExportGodotArgs {
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    dest_dir: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ExecArgs {
    code: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct CreateImageArgs {
    #[serde(default = "default_image_size")]
    width: i64,
    #[serde(default = "default_image_size")]
    height: i64,
    #[serde(default = "default_background")]
    background: String,
    #[serde(default = "default_name")]
    name: String,
}

fn default_image_size() -> i64 {
    1024
}

fn default_background() -> String {
    "white".to_string()
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ScriptArgs {
    script: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct MakeTextureArgs {
    preset: String,
    #[serde(default = "default_texture_size")]
    size: i64,
    #[serde(default)]
    seed: i64,
}

fn default_texture_size() -> i64 {
    256
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ReviewArgs {
    #[serde(default = "default_budget")]
    triangle_budget: i64,
    #[serde(default)]
    assertions: Option<Vec<Value>>,
    #[serde(default)]
    materials: Option<Map<String, Value>>,
}

// -- live-Blender tool args (user_prompt kept for schema parity; the Rust
// host does not ship telemetry credentials, so it is accepted and ignored)
#[derive(Debug, Deserialize, JsonSchema)]
struct UserPromptArgs {
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ObjectInfoArgs {
    object_name: String,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ScreenshotArgs {
    #[serde(default = "default_screenshot_size")]
    max_size: i64,
    #[serde(default)]
    user_prompt: Option<String>,
}

fn default_screenshot_size() -> i64 {
    1000
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ExecCodeArgs {
    code: String,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct DescribeNodeArgs {
    bl_idname: String,
    #[serde(default)]
    property_overrides: Option<Map<String, Value>>,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct LookupArgs {
    query: String,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct PolyhavenCatsArgs {
    #[serde(default = "default_hdris")]
    asset_type: String,
    #[serde(default)]
    user_prompt: Option<String>,
}

fn default_hdris() -> String {
    "hdris".to_string()
}

fn default_asset_type_all() -> String {
    "all".to_string()
}

fn default_search_limit() -> i64 {
    20
}

fn default_resolution_1k() -> String {
    "1k".to_string()
}

fn default_glb() -> String {
    "glb".to_string()
}

#[derive(Debug, Deserialize, JsonSchema)]
struct SearchPolyhavenArgs {
    #[serde(default)]
    query: Option<String>,
    #[serde(default = "default_asset_type_all")]
    asset_type: String,
    #[serde(default)]
    category: Option<String>,
    #[serde(default)]
    attributes: Option<Value>,
    #[serde(default)]
    min_size_m: Option<f64>,
    #[serde(default = "default_search_limit")]
    limit: i64,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct AssetPreviewArgs {
    asset_id: String,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct DownloadPolyhavenArgs {
    asset_id: String,
    asset_type: String,
    #[serde(default = "default_resolution_1k")]
    resolution: String,
    #[serde(default)]
    file_format: Option<String>,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct SetTextureArgs {
    object_name: String,
    texture_id: String,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct SearchSketchfabArgs {
    query: String,
    #[serde(default)]
    categories: Option<String>,
    #[serde(default = "default_search_limit")]
    count: i64,
    #[serde(default = "default_true")]
    downloadable: bool,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct SketchfabPreviewArgs {
    uid: String,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct DownloadSketchfabArgs {
    uid: String,
    target_size: f64,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct SearchPolypizzaArgs {
    #[serde(default)]
    query: String,
    #[serde(default)]
    category: Option<Value>,
    #[serde(default)]
    licence: Option<Value>,
    #[serde(default)]
    animated: bool,
    #[serde(default = "default_search_limit")]
    limit: i64,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct DownloadPolypizzaArgs {
    model_id: String,
    #[serde(default)]
    normalize_size: bool,
    #[serde(default = "default_target_size")]
    target_size: f64,
    #[serde(default)]
    user_prompt: Option<String>,
}

fn default_target_size() -> f64 {
    1.0
}

#[derive(Debug, Deserialize, JsonSchema)]
struct RodinTextArgs {
    text_prompt: String,
    #[serde(default)]
    bbox_condition: Option<Value>,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct RodinImagesArgs {
    #[serde(default)]
    input_image_paths: Option<Vec<String>>,
    #[serde(default)]
    input_image_urls: Option<Vec<String>>,
    #[serde(default)]
    bbox_condition: Option<Value>,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct PollRodinArgs {
    #[serde(default)]
    subscription_key: Option<String>,
    #[serde(default)]
    request_id: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ImportGeneratedArgs {
    name: String,
    #[serde(default)]
    task_uuid: Option<String>,
    #[serde(default)]
    request_id: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct HunyuanGenArgs {
    #[serde(default)]
    text_prompt: Option<String>,
    #[serde(default)]
    input_image_url: Option<String>,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct PollHunyuanArgs {
    #[serde(default)]
    job_id: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ImportHunyuanArgs {
    name: String,
    #[serde(default)]
    zip_file_url: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ExportSceneArgs {
    filepath: String,
    #[serde(default = "default_glb")]
    format: String,
    #[serde(default)]
    object_names: Option<Vec<String>>,
    #[serde(default)]
    selection_only: bool,
    #[serde(default = "default_true")]
    apply_modifiers: bool,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct FeedbackArgs {
    feedback: String,
    #[serde(default)]
    correction_text: Option<String>,
    #[serde(default)]
    step_index: Option<i64>,
    #[serde(default)]
    user_prompt: Option<String>,
}

#[derive(Clone)]
struct Ultima3d {
    tool_router: ToolRouter<Self>,
    prompt_router: PromptRouter<Self>,
    worker: Worker,
    live: live::LiveConn,
}

#[tool_router]
impl Ultima3d {
    fn new() -> Self {
        let cwd = std::env::current_dir().unwrap_or(PathBuf::from("."));
        let blender = env_path(
            "ULTIMA3D_BLENDER",
            r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe".to_string(),
        );
        // Cwd is whatever directory the MCP client happened to be in, so the
        // worker script is resolved from the executable first (see
        // worker::resolve_worker_py) and only then from the cwd.
        let worker_py = env::var("ULTIMA3D_WORKER")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let exe_dir = std::env::current_exe()
                    .ok()
                    .and_then(|exe| exe.parent().map(PathBuf::from));
                worker::resolve_worker_py(exe_dir.as_deref(), &cwd)
            });
        let timeout: u64 = env::var("ULTIMA3D_CALL_TIMEOUT")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(600);
        let worker = Worker::new(blender, worker_py, timeout);
        Self {
            tool_router: Self::tool_router(),
            prompt_router: Self::prompt_router(),
            worker,
            live: live::LiveConn::new(),
        }
    }

    fn prompt_router() -> PromptRouter<Self> {
        Self::prompt_router_gen()
    }

    fn err_text(e: anyhow::Error) -> Result<CallToolResult, McpError> {
        // Tool ran and failed: tool-level error content, not a protocol error,
        // so the model receives the message (mirrors ToolError in server.py).
        Ok(CallToolResult::error(vec![
            rmcp::model::ContentBlock::text(e.to_string()),
        ]))
    }

    #[tool(
        description = "Build a 3D asset in Blender from a parametric recipe and (optionally) a design blueprint."
    )]
    async fn create_3d(
        &self,
        Parameters(args): Parameters<Create3dArgs>,
    ) -> Result<CallToolResult, McpError> {
        let params = serde_json::json!({
            "recipe": args.recipe,
            "blueprint": args.blueprint,
            "name": args.name,
            "join": args.join,
        });
        match self.worker.call("build", params).await {
            Ok(r) => Ok(CallToolResult::success(vec![
                rmcp::model::ContentBlock::text(r.to_string()),
            ])),
            Err(e) => Self::err_text(e),
        }
    }

    #[tool(
        description = "Return machine-readable facts about the current asset: triangles, dimensions, materials, UVs, recipe."
    )]
    async fn inspect_asset(&self) -> Result<CallToolResult, McpError> {
        match self
            .worker
            .call("inspect", Value::Object(Default::default()))
            .await
        {
            Ok(r) => Ok(CallToolResult::success(vec![
                rmcp::model::ContentBlock::text(r.to_string()),
            ])),
            Err(e) => Self::err_text(e),
        }
    }

    #[tool(description = "Check the Blender worker: version, liveness, current scene state.")]
    async fn ultima3d_status(&self) -> Result<CallToolResult, McpError> {
        match self
            .worker
            .call("ping", Value::Object(Default::default()))
            .await
        {
            Ok(ping) => {
                let body = serde_json::json!({"ok": true, "blender": ping.get("blender"), "worker": "running"});
                Ok(CallToolResult::success(vec![
                    rmcp::model::ContentBlock::text(body.to_string()),
                ]))
            }
            Err(e) => Ok(CallToolResult::success(vec![
                rmcp::model::ContentBlock::text(
                    serde_json::json!({"ok": false, "error": e.to_string()}).to_string(),
                ),
            ])),
        }
    }

    #[tool(description = "Clear the Blender scene and forget the current recipe/blueprint.")]
    async fn reset_scene(&self) -> Result<CallToolResult, McpError> {
        self.worker_text("clear", Value::Object(Default::default()))
            .await
    }

    async fn worker_text(&self, op: &str, params: Value) -> Result<CallToolResult, McpError> {
        match self.worker.call(op, params).await {
            Ok(r) => ok_text(r.to_string()),
            Err(e) => Self::err_text(e),
        }
    }

    async fn worker_inspect(&self) -> Value {
        self.worker
            .call("inspect", Value::Object(Default::default()))
            .await
            .unwrap_or(Value::Null)
    }

    #[tool(
        description = "Validate a v1 asset blueprint and compile it to recipe nodes plus warnings. Feed the recipe to create_3d."
    )]
    async fn compile_blueprint(
        &self,
        Parameters(args): Parameters<BlueprintArgs>,
    ) -> Result<CallToolResult, McpError> {
        self.worker_text(
            "compile_blueprint",
            serde_json::json!({"blueprint": args.blueprint}),
        )
        .await
    }

    #[tool(
        description = "Parametric refinement: pass {changes: {node.param: value}} using dotted node names, or {recipe: {...}} to replace the whole recipe."
    )]
    async fn refine_asset(
        &self,
        Parameters(args): Parameters<RefineAssetArgs>,
    ) -> Result<CallToolResult, McpError> {
        let insp = self.worker_inspect().await;
        let mut last_recipe: Map<String, Value> = insp
            .get("recipe")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();
        let name = insp
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("asset")
            .to_string();
        let blueprint = insp.get("blueprint").cloned().unwrap_or(Value::Null);
        if let Some(recipe) = args.recipe {
            return self
                .worker_text(
                    "build",
                    serde_json::json!({"recipe": recipe, "name": name,
                                       "blueprint": blueprint, "join": true}),
                )
                .await;
        }
        let mut applied: Vec<String> = vec![];
        let mut ignored: Vec<String> = vec![];
        if let Some(changes) = args.changes {
            for (dotted, value) in changes {
                match dotted.split_once('.') {
                    Some((node, key)) if last_recipe.contains_key(node) && !key.is_empty() => {
                        match last_recipe.get_mut(node).and_then(|v| v.as_object_mut()) {
                            Some(obj) => {
                                obj.insert(key.to_string(), value);
                                applied.push(dotted);
                            }
                            None => ignored.push(dotted),
                        }
                    }
                    _ => ignored.push(dotted),
                }
            }
        }
        let mut result = match self
            .worker
            .call(
                "build",
                serde_json::json!({"recipe": last_recipe, "name": name,
                                   "blueprint": blueprint, "join": true}),
            )
            .await
        {
            Ok(r) => r,
            Err(e) => return Self::err_text(e),
        };
        let mut dropped: Vec<String> = vec![];
        if let Some(obj) = result.get("ignored_params").and_then(|v| v.as_object()) {
            for (node, info) in obj {
                if let Some(list) = info.get("ignored").and_then(|v| v.as_array()) {
                    for key in list.iter().filter_map(Value::as_str) {
                        dropped.push(format!("{node}.{key}"));
                    }
                }
            }
        }
        dropped.sort();
        if let Some(obj) = result.as_object_mut() {
            let kept: Vec<&String> = applied.iter().filter(|d| !dropped.contains(d)).collect();
            obj.insert("applied".to_string(), serde_json::to_value(&kept).unwrap());
            ignored.extend(dropped);
            obj.insert(
                "ignored".to_string(),
                serde_json::to_value(&ignored).unwrap(),
            );
        }
        ok_text(result.to_string())
    }

    #[tool(
        description = "Render the asset from standard angles for visual evaluation. Returns the images inline."
    )]
    async fn render_asset(
        &self,
        Parameters(args): Parameters<RenderAssetArgs>,
    ) -> Result<CallToolResult, McpError> {
        let fallback = [
            "front", "back", "left", "right", "front_34", "back_34", "top", "wire",
        ];
        let views: Vec<String> = args
            .views
            .clone()
            .unwrap_or_else(|| fallback.iter().map(|s| s.to_string()).collect());
        let dir = out_dir("renders");
        let r = match self
            .worker
            .call(
                "render_views",
                serde_json::json!({"views": args.views, "resolution": args.resolution,
                                   "dir": dir.to_string_lossy()}),
            )
            .await
        {
            Ok(r) => r,
            Err(e) => return Self::err_text(e),
        };
        use base64::Engine as _;
        let mut blocks = vec![];
        for v in &views {
            let p = r
                .get("renders")
                .and_then(|m| m.get(v))
                .and_then(Value::as_str)
                .unwrap_or("");
            if p.is_empty() {
                continue;
            }
            if let Ok(bytes) = tokio::fs::read(p).await {
                blocks.push(ContentBlock::image(
                    base64::engine::general_purpose::STANDARD.encode(&bytes),
                    "image/png",
                ));
            }
        }
        Ok(CallToolResult::success(blocks))
    }

    #[tool(
        description = "Apply a PBR material to the asset (or a named object). color = [r,g,b] 0-1. texture names a procedural preset."
    )]
    async fn set_material(
        &self,
        Parameters(args): Parameters<SetMaterialArgs>,
    ) -> Result<CallToolResult, McpError> {
        self.worker_text(
            "set_material",
            serde_json::json!({
                "name": args.name, "color": args.color, "roughness": args.roughness,
                "metallic": args.metallic, "object": args.object, "texture": args.texture,
                "tex_scale": args.tex_scale, "tex_blend": args.tex_blend,
                "tex_seed": args.tex_seed,
            }),
        )
        .await
    }

    #[tool(
        description = "Add one node with a given builder to the current recipe and rebuild (additive edit)."
    )]
    async fn set_geometry(
        &self,
        Parameters(args): Parameters<SetGeometryArgs>,
    ) -> Result<CallToolResult, McpError> {
        let insp = self.worker_inspect().await;
        let mut recipe: Map<String, Value> = insp
            .get("recipe")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();
        let mut node = Map::new();
        node.insert("builder".to_string(), Value::String(args.builder));
        if let Some(params) = args.params {
            for (k, v) in params {
                node.insert(k, v);
            }
        }
        recipe.insert(args.node_name, Value::Object(node));
        self.worker_text(
            "build",
            serde_json::json!({
                "recipe": recipe,
                "name": insp.get("name").and_then(Value::as_str).unwrap_or("asset"),
                "blueprint": insp.get("blueprint").cloned().unwrap_or(Value::Null),
                "join": true,
            }),
        )
        .await
    }

    #[tool(
        description = "Machine-checked validation: duplicates, zero-area faces, budget, UVs, materials, scale, manifoldness."
    )]
    async fn validate_asset(
        &self,
        Parameters(args): Parameters<BudgetArgs>,
    ) -> Result<CallToolResult, McpError> {
        self.worker_text(
            "validate",
            serde_json::json!({"triangle_budget": args.triangle_budget}),
        )
        .await
    }

    #[tool(
        description = "Verify blueprint assertions against the live asset: part count, ratios, symmetry, color coverage. Unmeasurable checks return manual."
    )]
    async fn check_assertions(
        &self,
        Parameters(args): Parameters<AssertionsArgs>,
    ) -> Result<CallToolResult, McpError> {
        self.worker_text(
            "check_assertions",
            serde_json::json!({"assertions": args.assertions, "materials": args.materials}),
        )
        .await
    }

    #[tool(
        description = "Game-ready export: clean, UV, decimate to budget, bake textures, LODs, collision hull, GLB + asset.json."
    )]
    async fn finalize_asset(
        &self,
        Parameters(args): Parameters<FinalizeArgs>,
    ) -> Result<CallToolResult, McpError> {
        let channels = args.channels.unwrap_or_else(|| {
            ["albedo", "normal", "roughness"]
                .iter()
                .map(|s| s.to_string())
                .collect()
        });
        self.worker_text(
            "finalize",
            serde_json::json!({
                "name": args.name, "triangle_budget": args.triangle_budget,
                "lods": args.lods, "collision": args.collision,
                "texture_resolution": args.texture_resolution,
                "channels": channels, "bake": args.bake,
                "dir": out_dir("export").to_string_lossy(),
            }),
        )
        .await
    }

    #[tool(
        description = "Multi-bone auto-rig: mass-adaptive vertical bone chain with automatic vertex weights."
    )]
    async fn rig_asset(
        &self,
        Parameters(args): Parameters<RigArgs>,
    ) -> Result<CallToolResult, McpError> {
        self.worker_text("rig", serde_json::json!({"bones": args.bones}))
            .await
    }

    #[tool(
        description = "Save the current parametric recipe + blueprint as JSON for later editing/rebuilding."
    )]
    async fn save_recipe(
        &self,
        Parameters(args): Parameters<PathArgs>,
    ) -> Result<CallToolResult, McpError> {
        self.worker_text("save_recipe", serde_json::json!({"path": args.path}))
            .await
    }

    #[tool(description = "Load a saved recipe; optionally rebuild the asset from it immediately.")]
    async fn load_recipe(
        &self,
        Parameters(args): Parameters<LoadRecipeArgs>,
    ) -> Result<CallToolResult, McpError> {
        let mut r = match self
            .worker
            .call("load_recipe", serde_json::json!({"path": args.path}))
            .await
        {
            Ok(r) => r,
            Err(e) => return Self::err_text(e),
        };
        if args.rebuild {
            let text = match tokio::fs::read_to_string(&args.path).await {
                Ok(t) => t,
                Err(e) => return Self::err_text(anyhow::anyhow!(e)),
            };
            let data: Value = match serde_json::from_str(&text) {
                Ok(d) => d,
                Err(e) => return Self::err_text(anyhow::anyhow!(e)),
            };
            let build = self
                .worker
                .call(
                    "build",
                    serde_json::json!({
                        "recipe": data.get("recipe").cloned().unwrap_or(Value::Null),
                        "name": data.get("asset").and_then(Value::as_str).unwrap_or("asset"),
                        "blueprint": data.get("blueprint").cloned().unwrap_or(Value::Null),
                        "join": true,
                    }),
                )
                .await;
            match build {
                Ok(b) => {
                    if let Some(obj) = r.as_object_mut() {
                        obj.insert("build".to_string(), b);
                    }
                }
                Err(e) => return Self::err_text(e),
            }
        }
        ok_text(r.to_string())
    }

    #[tool(description = "Copy the finalized GLB into a Godot project folder for import.")]
    async fn export_asset_to_godot(
        &self,
        Parameters(args): Parameters<ExportGodotArgs>,
    ) -> Result<CallToolResult, McpError> {
        let dest = args
            .dest_dir
            .map(PathBuf::from)
            .unwrap_or_else(|| out_dir("godot_import"));
        if let Err(e) = std::fs::create_dir_all(&dest) {
            return Self::err_text(anyhow::anyhow!(e));
        }
        let mut src_name = args.name;
        if src_name.is_none() {
            let insp = self.worker_inspect().await;
            src_name = insp
                .get("name")
                .and_then(Value::as_str)
                .map(|s| s.to_string());
        }
        let src_name = src_name.unwrap_or_else(|| "asset".to_string());
        let src = out_dir("export").join(format!("{src_name}.glb"));
        if !src.is_file() {
            return Self::err_text(anyhow::anyhow!(
                "no finalized GLB at {}; run finalize_asset(name={src_name:?}) first",
                src.display()
            ));
        }
        let out = dest.join(format!("{src_name}.glb"));
        if let Err(e) = std::fs::copy(&src, &out) {
            return Self::err_text(anyhow::anyhow!(e));
        }
        ok_text(
            serde_json::json!({"ok": true, "src": src.to_string_lossy(),
                               "dest": out.to_string_lossy(),
                               "note": "import it in Godot (auto .godot import on scan); no .tscn is created"})
            .to_string(),
        )
    }

    #[tool(description = "Run validate (+ optional critic) and report done vs next fixes.")]
    async fn review_asset_loop(
        &self,
        Parameters(args): Parameters<ReviewArgs>,
    ) -> Result<CallToolResult, McpError> {
        let validation = match self
            .worker
            .call(
                "validate",
                serde_json::json!({"triangle_budget": args.triangle_budget}),
            )
            .await
        {
            Ok(v) => v,
            Err(e) => return Self::err_text(e),
        };
        let assertions_json = match args.assertions {
            Some(a) => match self
                .worker
                .call(
                    "check_assertions",
                    serde_json::json!({"assertions": a, "materials": args.materials}),
                )
                .await
            {
                Ok(r) => Some(r.to_string()),
                Err(e) => return Self::err_text(e),
            },
            None => None,
        };
        ok_text(
            agentic::review_gate(&validation.to_string(), assertions_json.as_deref()).to_string(),
        )
    }

    // -- combined status (worker + godot + live) --------------------------
    #[tool(description = "One snapshot: worker, Godot bridge, live-Blender GUI.")]
    async fn ultima3d_status_all(&self) -> Result<CallToolResult, McpError> {
        let ultima = match self
            .worker
            .call("ping", Value::Object(Default::default()))
            .await
        {
            Ok(ping) => {
                serde_json::json!({"ok": true, "blender": ping.get("blender"), "worker": "running"})
            }
            Err(e) => serde_json::json!({"ok": false, "error": e.to_string()}),
        };
        let godot = match godot::call("status").await {
            Ok(v) => v,
            Err(e) => serde_json::json!({"ok": false, "error": e.to_string()}),
        };
        let live = live::connection_info();
        ok_text(agentic::combine_status(ultima, godot, live).to_string())
    }

    // -- live Blender GUI (user's open Blender over :9876) -----------------
    fn live_text(s: String) -> Result<CallToolResult, McpError> {
        ok_text(s)
    }

    fn live_image_or_note(
        bytes: Vec<u8>,
        format: String,
        note: String,
    ) -> Result<CallToolResult, McpError> {
        use base64::Engine as _;
        if live::image_output_mode() == "inline" {
            Ok(CallToolResult::success(vec![ContentBlock::image(
                base64::engine::general_purpose::STANDARD.encode(&bytes),
                live::mime_for(&format),
            )]))
        } else {
            ok_text(note)
        }
    }

    #[tool(
        description = "Check whether the connected Blender addon matches this MCP server version."
    )]
    async fn get_addon_status(
        &self,
        Parameters(args): Parameters<UserPromptArgs>,
    ) -> Result<CallToolResult, McpError> {
        let _ = args;
        Self::live_text(live::addon_status_text())
    }

    #[tool(
        description = "Turn OFF collection of prompts, code, screenshots and scene data. Takes effect immediately."
    )]
    async fn disable_telemetry(
        &self,
        Parameters(args): Parameters<UserPromptArgs>,
    ) -> Result<CallToolResult, McpError> {
        let _ = args;
        Self::live_text(live::disable_telemetry_text())
    }

    #[tool(description = "Get detailed information about the current Blender scene.")]
    async fn get_scene_info(
        &self,
        Parameters(args): Parameters<UserPromptArgs>,
    ) -> Result<CallToolResult, McpError> {
        let _ = args;
        Self::live_text(live::scene_info_text())
    }

    #[tool(description = "Get detailed information about a specific object in the Blender scene.")]
    async fn get_object_info(
        &self,
        Parameters(args): Parameters<ObjectInfoArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::object_info_text(&args.object_name))
    }

    #[tool(
        description = "Capture a screenshot of the current Blender 3D viewport. Returns a file path (or the image with BLENDER_MCP_IMAGE_OUTPUT=inline)."
    )]
    async fn get_viewport_screenshot(
        &self,
        Parameters(args): Parameters<ScreenshotArgs>,
    ) -> Result<CallToolResult, McpError> {
        match live::screenshot_bytes(args.max_size) {
            Ok((bytes, format)) => {
                if live::image_output_mode() == "inline" {
                    Self::live_image_or_note(bytes, format, String::new())
                } else {
                    match live::screenshot_note(args.max_size) {
                        Ok(note) => Self::live_text(note),
                        Err(e) => Self::live_text(e),
                    }
                }
            }
            Err(e) => Self::live_text(e),
        }
    }

    #[tool(description = "Execute arbitrary Python code in Blender. Break it into smaller chunks.")]
    async fn execute_blender_code(
        &self,
        Parameters(args): Parameters<ExecCodeArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::execute_code_text(&args.code))
    }

    #[tool(
        description = "Look up the property and socket schema of a Blender node type, without touching the scene."
    )]
    async fn describe_node_type(
        &self,
        Parameters(args): Parameters<DescribeNodeArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::describe_node_text(
            &args.bl_idname,
            args.property_overrides.as_ref(),
        ))
    }

    #[tool(
        description = "Structured Blender RNA/API reference lookup: types, properties, functions, operators."
    )]
    async fn bpy_api_lookup(
        &self,
        Parameters(args): Parameters<LookupArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::bpy_lookup_text(&args.query))
    }

    #[tool(description = "Get the categories and attributes you can filter Poly Haven assets by.")]
    async fn get_polyhaven_categories(
        &self,
        Parameters(args): Parameters<PolyhavenCatsArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::polyhaven_categories_text(&args.asset_type))
    }

    #[tool(description = "Search Poly Haven's library of free CC0 HDRIs, textures and models.")]
    async fn search_polyhaven_assets(
        &self,
        Parameters(args): Parameters<SearchPolyhavenArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::search_polyhaven_text(
            args.query.as_deref(),
            &args.asset_type,
            args.category.as_deref(),
            args.attributes.as_ref(),
            args.min_size_m,
            args.limit,
        ))
    }

    #[tool(description = "Get a preview thumbnail of a Poly Haven asset by its ID.")]
    async fn get_polyhaven_asset_preview(
        &self,
        Parameters(args): Parameters<AssetPreviewArgs>,
    ) -> Result<CallToolResult, McpError> {
        match live::polyhaven_preview(&args.asset_id) {
            Ok((bytes, format, _name, _authors, _url)) => {
                if live::image_output_mode() == "inline" {
                    Self::live_image_or_note(bytes, format, String::new())
                } else {
                    match live::polyhaven_preview_note(&args.asset_id) {
                        Ok(note) => Self::live_text(note),
                        Err(e) => Self::live_text(e),
                    }
                }
            }
            Err(e) => Self::live_text(e),
        }
    }

    #[tool(
        description = "Download and import a Polyhaven asset (hdris, textures, models) into Blender."
    )]
    async fn download_polyhaven_asset(
        &self,
        Parameters(args): Parameters<DownloadPolyhavenArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::download_polyhaven_text(
            &args.asset_id,
            &args.asset_type,
            &args.resolution,
            args.file_format.as_deref(),
        ))
    }

    #[tool(description = "Apply a previously downloaded Polyhaven texture to an object.")]
    async fn set_texture(
        &self,
        Parameters(args): Parameters<SetTextureArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::set_texture_text(&args.object_name, &args.texture_id))
    }

    #[tool(description = "Check if PolyHaven integration is enabled in Blender.")]
    async fn get_polyhaven_status(
        &self,
        Parameters(args): Parameters<UserPromptArgs>,
    ) -> Result<CallToolResult, McpError> {
        let _ = args;
        Self::live_text(live::addon_probe_text(
            "get_polyhaven_status",
            "PolyHaven is good at Textures, and has a wider variety of textures than Sketchfab.",
            "PolyHaven",
        ))
    }

    #[tool(description = "Check if Hyper3D Rodin integration is enabled in Blender.")]
    async fn get_hyper3d_status(
        &self,
        Parameters(args): Parameters<UserPromptArgs>,
    ) -> Result<CallToolResult, McpError> {
        let _ = args;
        Self::live_text(live::addon_probe_text("get_hyper3d_status", "", "Hyper3D"))
    }

    #[tool(description = "Check if Sketchfab integration is enabled in Blender.")]
    async fn get_sketchfab_status(
        &self,
        Parameters(args): Parameters<UserPromptArgs>,
    ) -> Result<CallToolResult, McpError> {
        let _ = args;
        Self::live_text(live::addon_probe_text(
            "get_sketchfab_status",
            "Sketchfab is good at Realistic models, and has a wider variety of models than PolyHaven.",
            "Sketchfab",
        ))
    }

    #[tool(description = "Search for models on Sketchfab with optional filtering.")]
    async fn search_sketchfab_models(
        &self,
        Parameters(args): Parameters<SearchSketchfabArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::search_sketchfab_text(
            &args.query,
            args.categories.as_deref(),
            args.count,
            args.downloadable,
        ))
    }

    #[tool(description = "Get a preview thumbnail of a Sketchfab model by its UID.")]
    async fn get_sketchfab_model_preview(
        &self,
        Parameters(args): Parameters<SketchfabPreviewArgs>,
    ) -> Result<CallToolResult, McpError> {
        match live::sketchfab_preview(&args.uid) {
            Ok((bytes, format, _name, _author)) => {
                if live::image_output_mode() == "inline" {
                    Self::live_image_or_note(bytes, format, String::new())
                } else {
                    match live::sketchfab_preview_note(&args.uid) {
                        Ok(note) => Self::live_text(note),
                        Err(e) => Self::live_text(e),
                    }
                }
            }
            Err(e) => Self::live_text(e),
        }
    }

    #[tool(
        description = "Download and import a Sketchfab model by its UID (scaled to target_size metres)."
    )]
    async fn download_sketchfab_model(
        &self,
        Parameters(args): Parameters<DownloadSketchfabArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::download_sketchfab_text(&args.uid, args.target_size))
    }

    #[tool(description = "Check if Poly Pizza integration is enabled in Blender.")]
    async fn get_polypizza_status(
        &self,
        Parameters(args): Parameters<UserPromptArgs>,
    ) -> Result<CallToolResult, McpError> {
        let _ = args;
        Self::live_text(live::addon_probe_text(
            "get_polypizza_status",
            " Poly Pizza is good at stylised, low-poly game assets. Everything is free under CC0 or CC-BY, and models are far lighter geometry than Sketchfab's.",
            "Poly Pizza",
        ))
    }

    #[tool(description = "Search for models on Poly Pizza with optional filtering.")]
    async fn search_polypizza_models(
        &self,
        Parameters(args): Parameters<SearchPolypizzaArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::search_polypizza_text(
            &args.query,
            args.category.as_ref().unwrap_or(&Value::Null),
            args.licence.as_ref().unwrap_or(&Value::Null),
            args.animated,
            args.limit,
        ))
    }

    #[tool(description = "Download and import a Poly Pizza model by its ID.")]
    async fn download_polypizza_model(
        &self,
        Parameters(args): Parameters<DownloadPolypizzaArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::download_polypizza_text(
            &args.model_id,
            args.normalize_size,
            args.target_size,
        ))
    }

    #[tool(
        description = "Generate 3D asset using Hyper3D from a text description, and import it into Blender."
    )]
    async fn generate_hyper3d_model_via_text(
        &self,
        Parameters(args): Parameters<RodinTextArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::rodin_text_job(
            &args.text_prompt,
            args.bbox_condition.as_ref().unwrap_or(&Value::Null),
        ))
    }

    #[tool(
        description = "Generate 3D asset using Hyper3D from images, and import it into Blender."
    )]
    async fn generate_hyper3d_model_via_images(
        &self,
        Parameters(args): Parameters<RodinImagesArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::rodin_images_job(
            args.input_image_paths.as_deref(),
            args.input_image_urls.as_deref(),
            args.bbox_condition.as_ref().unwrap_or(&Value::Null),
        ))
    }

    #[tool(description = "Check if a Hyper3D Rodin generation task is completed.")]
    async fn poll_rodin_job_status(
        &self,
        Parameters(args): Parameters<PollRodinArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::poll_rodin_text(
            args.subscription_key.as_deref(),
            args.request_id.as_deref(),
        ))
    }

    #[tool(description = "Import the asset generated by Hyper3D Rodin after the task completes.")]
    async fn import_generated_asset(
        &self,
        Parameters(args): Parameters<ImportGeneratedArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::import_generated_text(
            &args.name,
            args.task_uuid.as_deref(),
            args.request_id.as_deref(),
        ))
    }

    #[tool(description = "Check if Hunyuan3D integration is enabled in Blender.")]
    async fn get_hunyuan3d_status(
        &self,
        Parameters(args): Parameters<UserPromptArgs>,
    ) -> Result<CallToolResult, McpError> {
        let _ = args;
        Self::live_text(live::addon_probe_text(
            "get_hunyuan3d_status",
            "",
            "Hunyuan3D",
        ))
    }

    #[tool(
        description = "Generate 3D asset using Hunyuan3D from text and/or an image, and import it into Blender."
    )]
    async fn generate_hunyuan3d_model(
        &self,
        Parameters(args): Parameters<HunyuanGenArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::generate_hunyuan_text(
            args.text_prompt.as_deref(),
            args.input_image_url.as_deref(),
        ))
    }

    #[tool(description = "Check if a Hunyuan3D generation task is completed.")]
    async fn poll_hunyuan_job_status(
        &self,
        Parameters(args): Parameters<PollHunyuanArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::poll_hunyuan_text(args.job_id.as_deref()))
    }

    #[tool(description = "Import the asset generated by Hunyuan3D after the task completes.")]
    async fn import_generated_asset_hunyuan(
        &self,
        Parameters(args): Parameters<ImportHunyuanArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::import_hunyuan_text(
            &args.name,
            args.zip_file_url.as_deref(),
        ))
    }

    #[tool(
        description = "Export the whole scene, selection, or named objects to GLB/FBX for another application."
    )]
    async fn export_scene(
        &self,
        Parameters(args): Parameters<ExportSceneArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::export_scene_text(
            &args.filepath,
            &args.format,
            args.object_names.as_deref(),
            args.selection_only,
            args.apply_modifiers,
        ))
    }

    #[tool(
        description = "Record evaluation feedback (accept | reject | undo | correction) for a captured trajectory step."
    )]
    async fn record_trajectory_feedback(
        &self,
        Parameters(args): Parameters<FeedbackArgs>,
    ) -> Result<CallToolResult, McpError> {
        Self::live_text(live::feedback_text(
            &args.feedback,
            args.correction_text.as_deref(),
            args.step_index,
            args.user_prompt.as_deref(),
        ))
    }

    // -- Godot editor bridge --------------------------------------------
    async fn godot_text(&self, tool: &str) -> Result<CallToolResult, McpError> {
        match godot::call(tool).await {
            Ok(r) => ok_text(r.to_string()),
            Err(e) => ok_text(serde_json::json!({"ok": false, "error": e.to_string()}).to_string()),
        }
    }

    #[tool(description = "Get the running Godot editor/project status (offline-tolerant).")]
    async fn godot_status(&self) -> Result<CallToolResult, McpError> {
        self.godot_text("status").await
    }

    #[tool(description = "Inspect the currently edited Godot scene tree (offline-tolerant).")]
    async fn godot_scene_tree(&self) -> Result<CallToolResult, McpError> {
        self.godot_text("scene_tree").await
    }

    #[tool(description = "Run the current Godot project in the editor.")]
    async fn godot_run_project(&self) -> Result<CallToolResult, McpError> {
        self.godot_text("run_project").await
    }

    #[tool(description = "Stop the currently running Godot game.")]
    async fn godot_stop_project(&self) -> Result<CallToolResult, McpError> {
        self.godot_text("stop_project").await
    }

    #[tool(description = "Read buffered Godot editor/game output since the last read.")]
    async fn godot_debug_output(&self) -> Result<CallToolResult, McpError> {
        self.godot_text("debug_output").await
    }

    #[tool(description = "Capture the Godot editor/game viewport as PNG.")]
    async fn godot_screenshot(&self) -> Result<CallToolResult, McpError> {
        use base64::Engine as _;
        match godot::call("screenshot").await {
            Ok(data) => {
                if let Some(err) = data.get("error").and_then(Value::as_str) {
                    return Self::err_text(anyhow::anyhow!(err.to_string()));
                }
                let b64 = data.get("_image_b64").and_then(Value::as_str).unwrap_or("");
                match base64::engine::general_purpose::STANDARD.decode(b64) {
                    Ok(bytes) => Ok(CallToolResult::success(vec![ContentBlock::image(
                        base64::engine::general_purpose::STANDARD.encode(&bytes),
                        "image/png",
                    )])),
                    Err(e) => Self::err_text(anyhow::anyhow!(e)),
                }
            }
            Err(e) => Self::err_text(e),
        }
    }

    // -- Photoshop CS3 ----------------------------------------------------
    #[tool(
        description = "Check the Photoshop CS3 backend: exe presence, COM reachability, JSX presets."
    )]
    async fn photoshop_status(&self) -> Result<CallToolResult, McpError> {
        ok_text(photoshop::status().to_string())
    }

    #[tool(
        description = "Run ExtendScript in the OPEN Photoshop and return its result (live control). Send __quit__ to stop the bridge."
    )]
    async fn photoshop_exec(
        &self,
        Parameters(args): Parameters<ExecArgs>,
    ) -> Result<CallToolResult, McpError> {
        let code = args.code.trim().to_string();
        let r = if code == "__quit__" {
            photoshop::call(None, "quit", std::time::Duration::from_secs(60))
        } else {
            photoshop::call(Some(&code), "exec", std::time::Duration::from_secs(60))
        };
        match r {
            Ok(v) => ok_text(v.to_string()),
            Err(e) => ok_text(serde_json::json!({"ok": false, "error": e.to_string()}).to_string()),
        }
    }

    #[tool(
        description = "Create a new RGB image in the open Photoshop (white/transparent/background)."
    )]
    async fn photoshop_create_image(
        &self,
        Parameters(args): Parameters<CreateImageArgs>,
    ) -> Result<CallToolResult, McpError> {
        let code =
            match photoshop::build_create(args.width, args.height, &args.background, &args.name) {
                Ok(c) => c,
                Err(e) => {
                    return ok_text(
                        serde_json::json!({"ok": false, "error": e.to_string()}).to_string(),
                    )
                }
            };
        match photoshop::call(Some(&code), "exec", std::time::Duration::from_secs(60)) {
            Ok(v) => ok_text(v.to_string()),
            Err(e) => ok_text(serde_json::json!({"ok": false, "error": e.to_string()}).to_string()),
        }
    }

    #[tool(
        description = "Run a JSX preset (prep_texture, export_png) or inline ExtendScript in CS3. Fire-and-forget."
    )]
    async fn photoshop_run_script(
        &self,
        Parameters(args): Parameters<ScriptArgs>,
    ) -> Result<CallToolResult, McpError> {
        match photoshop::run_script(&args.script) {
            Ok(v) => ok_text(v.to_string()),
            Err(e) => ok_text(serde_json::json!({"ok": false, "error": e.to_string()}).to_string()),
        }
    }

    #[tool(
        description = "Synthesize a tileable texture trio (albedo/rough/normal PNGs) headlessly. Presets: stone, wood_grain, noise."
    )]
    async fn photoshop_make_texture(
        &self,
        Parameters(args): Parameters<MakeTextureArgs>,
    ) -> Result<CallToolResult, McpError> {
        let dir = out_dir("textures");
        match textures::make_texture(
            &args.preset,
            args.size.max(0) as usize,
            args.seed as u64,
            &dir,
            None,
        ) {
            Ok(v) => ok_text(v.to_string()),
            Err(e) => ok_text(
                serde_json::json!({"ok": false, "error": e.to_string(),
                                   "presets": textures::PRESETS})
                .to_string(),
            ),
        }
    }
}

#[prompt_router(router = "prompt_router_gen")]
impl Ultima3d {
    #[prompt(
        name = "asset_creation_strategy",
        description = "Defines the preferred strategy for creating assets in Blender"
    )]
    async fn asset_creation_strategy(&self) -> Vec<PromptMessage> {
        vec![PromptMessage::new_text(
            Role::User,
            live::ASSET_CREATION_STRATEGY,
        )]
    }
}

#[tool_handler]
#[prompt_handler]
impl ServerHandler for Ultima3d {
    fn get_info(&self) -> ServerConfig {
        // ServerConfig is #[non_exhaustive]: start from Default and mutate.
        let mut info = ServerConfig::default();
        info.protocol_version = ProtocolVersion::LATEST;
        info.capabilities.tools = Some(Default::default());
        info.capabilities.prompts = Some(Default::default());
        info.server_info.name = "ultima3d".to_string();
        info.server_info.title = Some("Ultima3D".to_string());
        info.server_info.version = env!("CARGO_PKG_VERSION").to_string();
        info.server_info.description = Some(
            "Agentic 3D asset generator: procedural Blender worker, live Blender GUI, Godot editor and Photoshop CS3 backends."
                .to_string(),
        );
        info.instructions = Some(live::server_instructions());
        info
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let service = Ultima3d::new().serve(stdio()).await?;
    service.waiting().await?;
    Ok(())
}
