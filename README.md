# ultima3d (Project Ultima3D) — Agentic 3D Asset Generator MCP

AI-directed procedural 3D synthesis: the LLM owns **intent and visual judgement**, the
One `ultima3d` server — stdio MCP, or the loopback daemon a Freebuff skill drives — exposes
61 tools: 15 procedural asset tools, 32 live-Blender GUI tools, 6 Godot editor tools,
5 GIMP tools, and 3 pipeline tools — plus the `asset_creation_strategy` prompt.

**Milestones.** v0.1 — deterministic procedural asset compiler proven.
v0.2 — trustworthy game-asset compiler: measurements are asset-wide, budgets
are enforceable, materials are consistent, rigs are tested, and exported artifacts are
round-trip verified.
v0.3 — export-only finalize (decimation never touches the scene, reruns are
idempotent) and bounded worker calls (a hung Blender is killed past a deadline).
v0.4 — blueprint compiler: v1 contract, `compile_blueprint` +
`check_assertions` critic (counts, ratios, symmetry, color coverage, occlusion,
mirrored depth).
v0.5 — Rust MCP host: the 61-tool surface (worker-protocol, live-Blender,
Godot, Photoshop) runs as a single `ultima3d-server` binary; only the code
that executes *inside* Blender/Godot/Photoshop stays in its native language.
v0.6 — full Rust switch: host-side Python deleted, system tray
ported to Rust (`ultima3d-tray`, whose icon mark is rendered by Ultima3D
itself), GitHub CI (build + 61-tool parity test) and release workflows.
v0.7 (this tree) — Python host restored beside Rust (no build step needed), a loopback
daemon plus a Freebuff Desktop skill (the desktop no longer loads MCP servers), and a
40-minute session budget so no agent can run the tool continuously.
v8 — Photoshop CS3 removed outright (its ExtendScript bridge kept
wedging the app); GIMP backend in its place, with batch Script-Fu proven live
on GIMP 3.2.8, and the first end-to-end asset shipped: supply_crate built,
rendered, validated and exported to GLB.
v9 (this tree) — worker lifecycle diagnostics plus robust worker-script
resolution; finalize exports baked PBR textures instead of white
procedurals (verified by bake_export_test) with an opt-out material merge;
new text signage builder; new foliage_cluster and stone_kerb builders with a
foliage spec and colour diagnostic under docs/.
v10 — five new builders for the Clover Village set: flower_bed
(domed soil, clover, rosettes, colour-grouped blooms), courier_statue (seated
rabbit-courier focal piece), bench, lantern_post, and the stone_kerb ring
builder; SKILL.md documents all 36 builders.
v11 (this tree) — Ultima3D is now a skill-hosting platform: skills/registry.json
manages ultima3d, ponytail, pullstack (both vendored as sparse git checkouts)
and a brain slot awaiting its starter kit, with a Python updater
(`python -m ultima3d.skill skill-update [--all]`); state/config.json toggles
the procedural/live/gimp/godot modules via the daemon or CLI; the Electron
dashboard/ app (npm start) shows daemon status, module toggles, skill
enable/update, and a tray icon — green idle, orange busy.

```text
LLM (MCP client — or a Freebuff skill driving the loopback daemon)
 │  create_3d(recipe, blueprint)
 ▼
ultima3d host (Python `ultima3d.server`, or the Rust `ultima3d-server`)
 │  JSON-lines RPC ("@JSON@"-prefixed responses)
 ▼
blender --background --factory-startup
 │  geometry compiler → Blender scene
 │  8-view render → LLM visual eval → refine → validate → finalize
 ▼
game-ready GLB (+ LODs, collision hull, baked PBR, asset.json)
```

The loop is: **build → look → judge → refine → validate → ship**. Refinement is parametric,
so revisions rebuild deterministically from the saved recipe and stay reproducible.

## Tray controller

`ultima3d-tray` (Windows, `cargo run -p ultima3d-tray`) puts a tray icon in
charge of the MCP server: Start / Stop / Restart / Open output folder / Quit.
The icon shows state at a glance — gray stopped, green online, amber busy (the
Blender worker is burning CPU on a job), blue idle — and a two-tone chime
sounds once when a busy server goes quiet for 2 minutes, i.e. your job just
finished. Single-instance guarded; exiting the tray stops the server.
Left-click toggles start/stop. The icon mark itself is rendered by Ultima3D:
`output/icon_work/render_icon.py` builds a beveled block in Blender and
renders `rust/crates/ultima3d-tray/assets/mark.png`, which the tray
composites over a per-status backdrop at runtime. Status logic, icons and the
process manager are covered by the tray crate's unit tests.

A no-Rust twin ships with the Python host: `python -m ultima3d.tray` owns
`python -m ultima3d.server` with the same menu, status model and idle chime, so the
tool is controllable without a cargo build.

## Tools

All 61 are registered on the one server, reachable as stdio MCP or over the daemon: the 15
procedural tools below, plus `ultima3d_status_all`, `export_asset_to_godot` and
`review_asset_loop` (pipeline), 6 `godot_*` tools, 5 `gimp_*` tools, and 32 live-Blender
GUI tools.

| Tool | Purpose |
|---|---|
| `create_3d(recipe, name, blueprint)` | Build from a parametric recipe (29 builders) + optional design blueprint |
| `compile_blueprint(blueprint)` | Validate a v1 blueprint and compile it to recipe nodes + warnings |
| `refine_asset(params)` | `{"changes": {"roof.height": 0.55}}` to patch params, or `{"recipe": {...}}` to replace wholesale |
| `set_geometry(builder, node_name, params)` | Add one node to the current recipe and rebuild |
| `inspect_asset()` | Machine facts: triangles, dimensions, materials, UVs, modifiers, recipe, blueprint |
| `render_asset(views, resolution)` | `front, back, left, right, front_34, back_34, top, wire`, returned inline for vision evaluation |
| `set_material(name, color, roughness, metallic, object, texture, ...)` | Apply a PBR material, optionally a procedural texture preset (wood_grain, bark, stone, mossy, grass_blend, worn_metal) |
| `validate_asset(triangle_budget)` | 7 machine checks (see below) |
| `check_assertions(assertions, materials)` | Measure part count, ratios, symmetry, and color coverage over renders in bpy; unmeasurable checks return manual |
| `finalize_asset(...)` | Clean → UV → decimate → bake → LODs → collision → GLB + `asset.json` |
| `rig_asset(bones)` | Multi-bone auto-rig with automatic weights |
| `save_recipe(path)` / `load_recipe(path, rebuild)` | Editable, replayable generation history (JSON) |
| `ultima3d_status()` | Worker health: Blender version, liveness |
| `reset_scene()` | Clear the scene and forget the current recipe/blueprint |

`finalize_asset` defaults: `triangle_budget=8000`, `collision="auto"`,
`texture_resolution=1024`, `channels=["albedo", "normal", "roughness"]`, `bake=True`.
`metallic` is available as an opt-in bake channel.

## Builders

29 builders form the recipe vocabulary:

`box, rounded_box, cylinder, tapered_cylinder, sphere, torus, arch, panel, pipe, curve,
extrusion, lathe, rock, beam, barrel, crate, roof, window, door, stairs, column, wall,
fence, tree, weapon_blade, weapon_handle, terrain, scatter, skydome`

Under them sit 17 deterministic low-level constructors, including `boolean_cut`, `array` and
`radial_array` (the latter three take object references, so they are not recipe-addressable).
Above them sit 15 `build_*` composites, such as `build_roof` (a solidified extrusion) and
`build_barrel` (a lathe plus hoops). All 29 registered builders are verified to build on
Blender 5.2.2.

## Vistas

A vista is an ordinary recipe that composes an environment scene instead of a
single asset: `terrain` meshes seeded value-noise ground (`size`, `segments`,
`height`, `seed`), `scatter` stamps an earlier recipe node across it (`source`
+ `terrain` node names, `count`, `area`, `seed`, `scale_min/max`, `sink`), and
`skydome` adds a gradient sky. Scatter reads the terrain node's spec, so
placement and the mesh share one height function — instances sit on the
ground by construction — and a seed makes the layout reproducible (rebuilding
the same recipe yields the same triangles; verified by `tests/vista_test.py`).
The dome is scene backdrop, not asset geometry: it renders but is excluded
from inspect, validate, finalize and export. Vistas are big — budget tens of
thousands of triangles, not the 8k single-asset default.

```python
create_3d(recipe={
  "ground": {"builder": "terrain", "size": [24, 24], "segments": 32, "height": 1.6, "seed": 7,
              "material": {"name": "grass", "color": [0.3, 0.45, 0.2], "roughness": 0.9}},
  "oak":    {"builder": "tree", "height": 3.0},
  "grove":  {"builder": "scatter", "source": "oak", "terrain": "ground", "count": 6, "seed": 3},
  "sky":    {"builder": "skydome", "radius": 120},
}, name="meadow_vista")
```

## Procedural textures

No downloads needed: a recipe material accepts an optional `texture` preset
that builds a small deterministic EEVEE-safe shader-node chain (TexCoord →
Mapping → Noise/Voronoi/Wave → ColorRamp → Principled + Bump) instead of a
flat color. This is the realism path while the live addon's Poly Haven
commands are unavailable — the overcast vista look needs no photo textures.

| Preset | Built from | Good for |
|---|---|---|
| `wood_grain` | stretched Wave bands + Bump | beams, crates, handles, doors |
| `bark` | Voronoi cells + strong Bump | tree trunks |
| `stone` | Voronoi F1 + Bump | rocks, walls, arches, columns |
| `mossy` | large-scale Noise blend + Bump | vista rocks, damp stone |
| `grass_blend` | Noise blend + faint Bump | terrain |
| `worn_metal` | Noise color/roughness + faint Bump | blades, latches, hoops |

```python
{"builder": "rock", "size": [0.5, 0.5, 0.4],
 "material": {"name": "granite", "color": [0.5, 0.5, 0.52],
               "roughness": 0.85, "texture": "stone",
               "tex_scale": 3.0, "tex_blend": 0.5, "tex_seed": 4}}
```

`tex_scale` tiles the pattern, `tex_blend` shifts the color-ramp midpoint,
`tex_seed` offsets the mapping for variation. Unknown names warn and fall
back to flat color, never crashing the build. `set_material` accepts the same
keys. Chains survive baking to the existing PNG channels. Verified by
`tests/texture_test.py` (each preset builds, validates, renders; unknown
name falls back; rebuild is deterministic).

## GIMP

The `ultima3d.gimp` backend pairs your GIMP install (2.10 or 3.x) with the same
headless texture synthesizer, so texture work never waits on the GUI app — and
unlike the old Photoshop CS3 bridge, GIMP ships a real CLI, so every call
completes with an exit code or fails loudly instead of wedging the editor:

- `gimp_make_texture(preset, size, seed)` — stdlib-only seamless
  albedo/rough/normal PNG trios under `output/textures/` (GIMP never
  opens). Presets: `stone`, `wood_grain`, `noise`. Same seed rebuilds
  identical bytes; verified by `tests/gimp_test.py`.
- `gimp_status()` — reports the exe found (GIMP 3 preferred, then 2.10
  console), its `--version`, Script-Fu server reachability, and available
  presets. Never hangs: every probe is bounded.
- `gimp_exec(script)` — runs Script-Fu in a throwaway batch GIMP
  (`--new-instance -i --batch-interpreter=plug-in-script-fu-eval -b ... [--quit]`)
  and returns its console output. Create,
  modify, query — e.g. `(gimp-message "hi")` comes back as text.
- `gimp_create_image(width, height, background, name)` — new RGB image with a
  filled background layer (`white`/`transparent`/`background`), saved as PNG
  under `output/gimp/`; returns the file path.
- `gimp_run_script(script, image, output)` — runs a bundled `.scm` preset
  against an image file (`prep_image`: flatten + RGB 8-bit copy; `export_png`:
  PNG copy beside the source, which is never modified) or inline Script-Fu,
  and returns the console output. Refuses code that never touches GIMP.

Set `ULTIMA3D_GIMP` to override the exe path. Batch covers create, convert
and export runs; if you also run a Script-Fu server (default port 10008,
`ULTIMA3D_GIMP_PORT`), `live:true` in `gimp_status` confirms it for live
control of the open app. All calls degrade gracefully when GIMP is absent.

Every build maintains an in-memory **asset manifest**: each recipe node registers the
objects it created, so all downstream operations (inspect, materials, validate, finalize,
collision, export) act on the whole asset — there is no "primary object" concept.
Recorded in the build result and in `asset.json`.

## Detail profiles

Recipe nodes may specify semantic quality instead of Blender density knobs:

```json
{"builder": "barrel", "radius": 0.45, "height": 0.9, "detail": "game", "triangle_budget": 1200}
```

`detail` is `draft` (8 segments) / `game` (16) / `hero` (32); the profile fills whichever
density parameters the chosen builder actually accepts (`vertices`, `segments`,
`ring_count`, `bevel_segments`), and an explicit parameter always wins over the profile.
The LLM specifies intent; Blender determines geometry — extended to polygon density.
A per-node `triangle_budget` is measured after the build and reported as
`node_triangles[node].over_budget` in the build result (advisory; the global budget is
enforced by `validate_asset`).

## Blueprint contract (v1)

The `blueprint` argument to `create_3d` is the semantic design doc a recipe compiles
from: perception writes it, the LLM compiles it to recipe nodes, and the critic checks
it. It is persisted in `asset.json` and by `save_recipe`/`load_recipe`. Pre-v1 free-form
blueprints remain accepted; v1 below is the recommended contract.

Each section names its producer and consumer — a section that can't be filled by a
vision pass or read by a pipeline stage doesn't belong:

| Section | Producer | Consumer |
|---|---|---|
| `meta` | blueprint authoring | traceability (source image, confidence) |
| `asset` | passes 1–2 (what is it, style, symmetry, hidden sides) | director decisions |
| `scale` | pass 2 (basis object of known size) | unit conversion to recipe floats |
| `parts[]` | pass 3 (forms + evidence) | one recipe node each (`builder` verbatim, optional builder-native `params` which is authoritative, `location` defaulted from `center`, material inlined) |
| `relations[]` / `ratios` | pass 4 (layout + materials) | resolving ambiguous placement; critic checks |
| `materials{}` | pass 4 | inlined into recipe nodes |
| `detail` | pass 5 | `detail` profile + budgets |
| `depth` | pass 3 (front-to-back order, occlusion pairs + view) | `compile_blueprint` validates node refs; critic verifies via `occlusion` raycasts |
| `assertions[]` | pass 5 (what "done" looks like) | critic: `check_assertions` measures part count, ratios (`of: ["node.dim", ...]` plus `expected`), symmetry, and color coverage over renders (inline `color`, or `material` resolved against a `materials` map) in bpy; unmeasurable checks return `manual`; misses become `refine_asset` changes |

`relations` predicates are `sits_on`, `centered_on`, `attached_to`, `inset_in`, `beside`,
plus an `offset`. `parts[].confidence` below ~0.7 means verify-or-ask, not guess.
Revision attempts and critic history live in session state, not in the persisted spec.

```json
{
  "meta": {"version": 1, "sources": ["ref_front_34.png"],
           "reference_view": "front-three-quarter",
           "produced_by": "generate_asset_blueprint", "confidence": 0.82},
  "asset": {"name": "medieval_house", "type": "house", "style_tags": ["medieval", "stylized"],
            "symmetry": {"axis": "x", "confidence": 0.9},
            "hidden_geometry": {"assumption": "rear mirrors front", "confidence": 0.8}},
  "scale": {"basis": "door", "height_m": 2.1},
  "parts": [
    {"node": "body", "label": "main timber body", "builder": "rounded_box",
     "size": [4.0, 3.2, 2.6], "center": [0, 0, 1.3],
     "material": "timber_dark", "confidence": 0.9,
     "evidence": "front-left view: timber walls"},
    {"node": "roof", "label": "steep gabled thatch roof", "builder": "roof",
     "size": [4.6, 3.8, 1.6], "center": [0, 0, 3.4],
     "material": "thatch", "confidence": 0.85,
     "evidence": "pitch ~47deg estimated against wall height"},
    {"node": "door", "label": "front plank door", "builder": "door",
     "size": [0.9, 0.08, 2.1], "center": [0.4, -1.62, 1.05],
     "material": "wood_mid", "confidence": 0.75,
     "evidence": "off-center in reference; rear assumed mirrored"},
    {"node": "chimney", "label": "stone chimney, right slope", "builder": "box",
     "size": [0.5, 0.5, 1.4], "center": [1.2, 0.3, 4.2],
     "material": "stone", "confidence": 0.6,
     "evidence": "partially occluded; height estimated"}
  ],
  "relations": [
    {"subject": "roof", "predicate": "sits_on", "object": "body", "offset": [0, 0, 0.05]},
    {"subject": "door", "predicate": "inset_in", "object": "body", "face": "front"},
    {"subject": "chimney", "predicate": "attached_to", "object": "roof", "face": "right-slope"}
  ],
  "ratios": {"roof_height_to_wall_height": 0.62, "door_width_to_wall_width": 0.22},
  "depth": {"view": "front", "occludes": [{"front": "roof", "behind": "chimney"}]},
  "materials": {
    "timber_dark": {"color": [0.32, 0.22, 0.14], "roughness": 0.75, "metallic": 0.0, "coverage": 0.55},
    "thatch":      {"color": [0.55, 0.45, 0.26], "roughness": 0.9,  "metallic": 0.0, "coverage": 0.3},
    "wood_mid":    {"color": [0.45, 0.3, 0.18],  "roughness": 0.7,  "metallic": 0.0, "coverage": 0.08},
    "stone":       {"color": [0.5, 0.5, 0.52],   "roughness": 0.85, "metallic": 0.0, "coverage": 0.07}
  },
  "detail": {"profile": "game", "triangle_budget": 8000,
             "surface_notes": ["rough-hewn timber", "uneven thatch edge"]},
  "assertions": [
    {"check": "part_count", "expected": 4},
    {"check": "symmetry_x", "min": 0.8},
    {"check": "ratio", "name": "roof_height_to_wall_height",
     "of": ["body.z", "roof.z"], "expected": 0.62, "tolerance": 0.12},
    {"check": "color_present", "material": "thatch", "min_coverage": 0.2}
  ]
}
```

## Blueprint protocol

The five passes turn one reference image into a v1 blueprint; each pass answers
only its own questions, then the critic verifies before anything is called done.
The server is blind — passes run in the vision-capable director; `compile_blueprint`
and `check_assertions` are the mechanical backstops.

- **Pass 1 — identify:** asset type, overall style, reference view. Fills `meta`, `asset.name/type/style_tags`.
- **Pass 2 — scale and symmetry:** basis object of known size, symmetry axis, what the hidden sides most likely look like. Fills `scale`, `asset.symmetry`, `asset.hidden_geometry`.
- **Pass 3 — decompose:** major forms as parts with `builder`, `size`, `center`, builder-native `params` where the shape needs them (beams, extrusions, lathes), plus `evidence` per part. Estimate front-to-back depth order before finalizing `size`/`center` — what occludes what determines extrusion depths. Fills `parts[]`, `depth`.
- **Pass 4 — relate:** spatial predicates, dimension ratios that must hold, material palette with coverage. Fills `relations[]`, `ratios`, `materials{}`.
- **Pass 5 — specify done:** detail profile, triangle budget, surface notes, and one assertion per checkable fact. Fills `detail`, `assertions[]`.

Compile with `compile_blueprint` (unknown builders, dangling material refs, bad relation
targets, and missing required params error here — not at build time), fix warnings,
then `create_3d` with the returned recipe. Critic procedure, in order:

1. `render_asset`, then `check_assertions` — part count, ratios, symmetry, color
   coverage, occlusion, and mirrored depths are measured (color needs renders;
   without them it stays `manual`). `occlusion` raycasts the depth pairs from the
   reference view; `mirror_depth` re-renders hidden-side depth from geometry as a
   second opinion on the symmetry assumption — lighting can't fake either.
   Every `fail` becomes a `refine_asset` change; remaining `manual` results are
   verified by the director against renders.
2. Nothing is called done while any result is `fail` or any `manual` is unverified.
3. After each refine, re-run `check_assertions` — ratios and symmetry are re-measured,
   not assumed fixed.

Node-level ratios need an unjoined scene (`join: false`): joining merges geometry, so
merged-away nodes honestly report `manual`. Unknown assertion types fail loudly.

## Rigging

`rig_asset()` builds a vertical bone chain adapted to where the geometry actually is:

- A 16-bin histogram of world-space Z, weighted by vertex count.
- Bone count `min(8, max(3, non-empty bins // 2))`, or an explicit `bones` value.
- Joints placed at cumulative-density fractions, so they cluster where mass concentrates
  rather than at even intervals.
- Connected chain with tapering bone radii.
- Weights via `parent_set(type="ARMATURE_AUTO")`. The fallback path (`force_fallback=true`
  forces it) assigns every vertex rigidly, weight 1.0, to the bone spanning its world Z —
  empty groups would be dropped by glTF export. `weights_source` reports `auto`,
  `fallback`, or `existing`.
- Idempotent: rigging twice returns the existing armature instead of stacking a second one.
- Verified to deform (pose probe) and to survive GLB export/re-import, by
  `tests/roundtrip_test.py`.

`finalize_asset` then exports the rigged GLB and records an `armature`/`bones`/`joints_z`
block in `asset.json`.

## Validation

`validate_asset` returns seven machine checks, all measured in `bpy` and **aggregated over
every mesh in the asset**:

`no_duplicate_vertices`, `no_zero_area_faces`, `triangle_budget`, `has_uv`,
`has_material`, `scale_applied`, `non_manifold_edges` (advisory by default, since stylized
hard-surface meshes are rarely watertight).

`inspect_asset` reports the union bounds, dimensions, object count, total triangles and
vertices, materials, and non-manifold edge count of the whole asset.

## Output contract

`finalize_asset` writes to `$ULTIMA3D_OUT/export`:

- `<name>.glb` — main asset (all meshes; rigged when a rig exists)
- `<name>_LOD1.glb` — decimated LOD per budget entry
- `<name>_collision.glb` — convex hull of the union of all meshes
- `<name>_<channel>.png` — baked PBR channels (per mesh when the asset is multi-mesh)
- `asset.json` — triangles, dimensions, materials, objects, manifest, texture paths, rig block

## Example

```python
create_3d(recipe={
  "body":  {"builder": "rounded_box", "size": [1, .45, .5], "radius": .06,
            "material": {"name": "wood", "color": [.45, .3, .18], "roughness": .7}},
  "roof":  {"builder": "roof", "width": 1.15, "depth": .55, "height": .35, "location": [0, 0, .5],
            "material": {"name": "dark_iron", "color": [.1, .1, .11], "roughness": .4, "metallic": .9}},
  "latch": {"builder": "rounded_box", "size": [.12, .06, .18], "location": [0, -.25, .15],
            "material": {"name": "brass", "color": [.75, .6, .2], "metallic": 1.0}},
}, name="medieval_mailbox")

render_asset()                                   # look at it
refine_asset({"changes": {"roof.height": .55}})  # fix what you saw
validate_asset()
finalize_asset(name="medieval_mailbox")
```

## Layout

```text
ultima3d/                Python host: server.py (18 tools), live/ (32), godot/ (6),
                         gimp/ (5), session.py (40-min budget), daemon.py (loopback
                         JSON), skill.py (shell bridge + skill installer)
skills/ultima3d/SKILL.md the Freebuff Desktop skill (installed into ~/.agents/skills)
rust/                    Rust workspace (the optional fast host: server + tray)
  crates/ultima3d-server/  the `ultima3d` MCP server (61 tools + 1 prompt)
  crates/ultima3d-tray/    the Windows system-tray controller
  crates/ultima3d-tray/assets/mark.png  tray mark, rendered by Ultima3D itself
blender/
  worker.py      geometry compiler run under blender --background (stays
                 Python: it executes inside Blender's embedded interpreter,
                 which exposes no Rust API)
  addon.py       live-Blender GUI addon (install into the user's Blender)
gimp/
  scripts/       bundled Script-Fu presets (prep_image, export_png)
godot/addon/     Godot editor plugin (install into your project)
output/icon_work/render_icon.py  renders the tray mark (Blender, headless)
```

A plain `git clone` is all you need — no submodules. GitHub CI builds the Rust
workspace and runs `cargo test`, and a second job exercises the Python host (61-tool
parity, session budget, skill package, daemon round-trip; no Blender/Godot required);
releases are cut from `v*` tags with both Windows binaries attached.

The live tools were ported from
[mcp-for-blender](https://github.com/ahujasid/mcp-for-blender); upstream
merges are manual from here.

## Run

The Python host needs no build step — `pip install -e .` once (needs `mcp>=2.0` + `httpx`):

```bash
python -m ultima3d.skill call ultima3d_status     # starts the loopback daemon, then calls a tool
python -m ultima3d.skill tools                    # all 61 tools
python -m ultima3d.skill status                   # daemon + session budget
python -m ultima3d.skill stop                     # stop the daemon and its Blender worker
python -m ultima3d.server                         # stdio MCP (what an MCP client launches)
python -m ultima3d.server --http 127.0.0.1:8791   # loopback JSON daemon in the foreground
python -m ultima3d.tray                           # optional tray controller, owns the stdio server
```

`--http` binds loopback only and refuses any other host. The daemon dispatches through the same
tool manager as stdio, so validation and error text are identical, and it writes
`output/daemon.log`. `ULTIMA3D_DAEMON_TOKEN` optionally requires an `X-Ultima3D-Token` header.

### Session budget

One server process is capped at **40 minutes** (`ULTIMA3D_SESSION_MINUTES`). The clock starts with
the process, `ultima3d_status` reports `session.remaining_seconds`, every tool call is refused past
the deadline, and a watchdog kills the Blender worker and exits the server when it lands. Nothing
runs continuously: a new session means an explicit restart.

The Rust host remains the optional fast path:

```bash
cd rust && cargo build --workspace     # ultima3d-server + ultima3d-tray
cargo test --workspace                 # unit tests + 61-tool parity test
cargo run -p ultima3d-tray             # tray icon owning the server
```

See `rust/BUILD.md` for the Windows linker setup (llvm-mingw, no VS needed).

Config (env): `ULTIMA3D_BLENDER` (Blender executable), `ULTIMA3D_OUT` (output dir —
defaults to `./output` under your invoking project directory, never the checkout),
`ULTIMA3D_WORKER` (worker script, default `blender/worker.py`),
`ULTIMA3D_SERVER` (server binary for the tray to own),
`ULTIMA3D_CALL_TIMEOUT` (per-call worker deadline in seconds, default 600 — a hung
worker is killed past it, losing its scene state),
`ULTIMA3D_SESSION_MINUTES` (session cap, default 40),
`ULTIMA3D_DAEMON` (daemon address, default `127.0.0.1:8791`),
`ULTIMA3D_DAEMON_TOKEN` (optional shared token for the daemon).

Requires **Blender 5.2 LTS**; a Rust stable toolchain only for the Rust host.

## Backends

One MCP server, four backends — all degrading gracefully when their editor is offline:

- **Procedural** (`create_3d`, `refine_asset`, `render_asset`, `validate_asset`,
  `finalize_asset`, …) — background Blender worker, deterministic + measurable.
- **Live Blender** (`get_scene_info`, `execute_blender_code`,
  `get_viewport_screenshot`, asset libraries, …) — drives the user's open Blender
  GUI over `BLENDER_HOST:BLENDER_PORT` (default `localhost:9876`). Install the addon
  from `blender/addon.py`, then enable it in Blender preferences.
- **Godot** (`godot_status`, `godot_scene_tree`, `godot_run_project`,
  `godot_stop_project`, `godot_debug_output`, `godot_screenshot`) — drives the
  running Godot editor over `GODOT_MCP_URL` (default `http://127.0.0.1:8765/mcp`).
  Copy `godot/addon/` into your Godot project's `addons/` folder and enable
  **Ultima3D Godot Bridge** under Project Settings → Plugins.
- **GIMP** (`gimp_status`, `gimp_exec`, `gimp_make_texture`, …) — batch
  Script-Fu that always completes or fails loudly, plus a headless texture
  synthesizer (see the GIMP section).
- **Pipeline** (`ultima3d_status_all`, `export_asset_to_godot`, `review_asset_loop`)
  — status fan-out, GLB handoff to a Godot project, validate+critic review gate.

## Freebuff wiring

### Desktop: the skill (no MCP servers)

Freebuff Desktop loads skills, not MCP servers, so the tool surface is reached by shelling out to
the local daemon. Install the skill once:

```bash
python -m ultima3d.skill install     # copies skills/ultima3d -> ~/.agents/skills/ultima3d
```

Restart or reload the desktop; the skill tells the agent to run
`python -m ultima3d.skill call <tool> '<json>'` from the repo root, on this machine only, and to
respect the session budget. Each call is one JSON result; renders come back as PNG paths.

### MCP clients (CLI, other agents)

`~/.agents/mcp.json` points at the Python module (or the built Rust binary):

```json
{
  "mcpServers": {
    "ultima3d": {
      "command": "python",
      "args": ["-m", "ultima3d.server"],
      "cwd": "C:/Users/buffb/Desktop/GitHub/Ultima3D"
    }
  }
}
```

The client launches one long-lived Blender worker per server process, so scene, recipe and
blueprint state persist across tool calls, and the 40-minute budget applies to it too.
Restart the server after changing code.

## Architecture boundary

The LLM decides *what should exist*; the worker measures *what actually happened*. Validation
numbers come from `bpy`, never from the model. Refinement is parametric, so a revision is a
diff against a recipe rather than a rewrite of geometry.

## Known limitations

- A builder's `material` applies to all objects the node created (hoops, slats, mullions,
  posts, foliage) — but per-sub-part overrides inside one node are not possible; split the
  node if two sub-parts need different materials.
- Baked textures ship beside the GLB rather than embedded in it.
- Per-node `triangle_budget` is advisory; only the asset-wide budget is enforced at
  validate/finalize time.
- `finalize_asset` embeds textures in the GLB only when Blender's exporter does so via
  material node images; external bake PNGs are the canonical reference (recorded in asset.json).
- The 40-minute session budget belongs to the Python host and its daemon; the pre-existing
  Rust binary has no cap of its own.
- The daemon has no authentication unless `ULTIMA3D_DAEMON_TOKEN` is set. It is loopback-only
  by design and refuses any other bind address.
- Restarting the server begins a new 40-minute budget: the cap bounds one run, not a rolling
  window across restarts.
-GIMP batch runs headlessly with no GUI; live control of the open app needs a
  user-started Script-Fu server on 127.0.0.1:10008.
