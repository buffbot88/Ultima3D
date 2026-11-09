# 🧊 Ultima3D — Agentic 3D Asset Generator

> **The LLM owns intent and visual judgement. Ultima3D owns mechanics and numbers.**
> Describe a game-ready prop, building, vista or environment → get a measured,
> validated, textured **GLB** with LODs, collision hull and baked PBR — built in
> headless Blender, rendered for your eyes, refined parametrically until it looks right.

[![version](https://img.shields.io/badge/version-v11.0.0-blue)](pyproject.toml)
[![blender](https://img.shields.io/badge/blender-5.2_LTS-orange)](https://www.blender.org)
[![python](https://img.shields.io/badge/python-3.10%2B-green)](https://www.python.org)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)
[![platform](https://img.shields.io/badge/platform-Windows_10%2F11-blue)](https://github.com/buffbot88/Ultima3D)

**v11 is the skill-hosting platform release:** a skill registry + updater
(`ultima3d`, `ponytail`, `pullstack`, `brain`), enable/disable toggles for every
backend module, and an **Electron dashboard** that pops up when Ultima3D boots,
lives in your system tray (🟢 idle · 🟠 busy · ⚪ down), and drives the whole
thing — daemon lifecycle, modules, skills — from three tabs.

```text
You / LLM director (Freebuff skill → loopback daemon on 127.0.0.1:8791)
 │  create_3d(recipe, blueprint)
 ▼
Ultima3D host — Python `ultima3d.server` (or the optional Rust `ultima3d-server`)
 │  JSON-lines RPC to a persistent blender --background worker
 ▼
geometry compiler → Blender scene → renders → YOU LOOK → refine → validate → finalize
 ▼
game-ready GLB (+ LODs, collision hull, baked PBR, asset.json)
```

The loop is always: **build → look → judge → refine → validate → ship**.
Refinement is parametric, so every revision rebuilds deterministically from the
saved recipe. Nothing ships that wasn't measured in `bpy`.

## Contents

- [5-minute quickstart](#5-minute-quickstart)
- [Dashboard](#-dashboard-electron-tray-app) · [Skills](#-skill-platform) ·
  [Modules](#-modules) · [Tray](#-tray)
- [Run & CLI](#%EF%B8%8F-run--cli) · [Session budget](#session-budget) ·
  [Where outputs go](#-where-outputs-go)
- [Tools](#-tools) · [Builders](#-builders-36) · [Vistas](#-vistas) ·
  [Textures](#-procedural-textures) · [GIMP](#-gimp)
- [Blueprints](#-blueprint-contract-v1) · [Rigging](#-rigging) ·
  [Validation](#-validation) · [Output contract](#-output-contract)
- [Backends](#-backends) · [Freebuff wiring](#-freebuff-wiring) ·
  [Layout](#-layout) · [Milestones](#-milestones) · [Limits](#%EF%B8%8F-known-limitations)

## ⚡ 5-minute quickstart

```bash
pip install -e .                        # once (needs mcp>=2.0 + httpx, Python 3.10+)
python -m ultima3d.skill start          # boots the daemon AND pops up the dashboard
```

Then build something (from your **project** directory — outputs land there, never
in this checkout):

```bash
cd C:\path\to\your-game
python -m ultima3d.skill call create_3d '{"name":"supply_crate","recipe":{
  "body": {"builder": "crate", "size": [1,1,1],
           "material": {"name": "wood", "color": [0.45,0.3,0.18], "roughness": 0.7}}}}'
python -m ultima3d.skill call render_asset '{"views":["front_34"],"resolution":640}'
# open output/renders/*.png, look at it, then:
python -m ultima3d.skill call refine_asset '{"changes": {}}'   # tweak what you saw
python -m ultima3d.skill call validate_asset '{}'
python -m ultima3d.skill call finalize_asset '{"name":"supply_crate"}'  # GLB + LODs + asset.json
```

Headless rigs / CI: add `--no-dashboard` (or `ULTIMA3D_NO_DASHBOARD=1`) and no
window opens. Everything else is identical.

## 🖥️ Dashboard (Electron tray app)

`dashboard/` is plain HTML/JS — no framework, no build step. `npm start` runs it
from source; starting Ultima3D any other way opens it automatically.

| Tab | Does |
|---|---|
| **Daemon** | Start / Stop / **Restart** the daemon, session-budget readout, open-output-folder button |
| **Modules** | Toggle cards for `procedural`, `live`, `gimp`, `godot` — one-click daemon restart applies them |
| **Skills** | Enable/disable each skill, version pins, **Update / Update-all** with log output |

Window behavior: opens on boot, **minimizes and closes to the tray** instead of
quitting. Right-click the tray icon for Show / Start / Restart / Stop / Quit;
double-click restores. Single instance — a second launch just focuses the window.

The tray dot is the server state at a glance:

| Icon | Meaning |
|---|---|
| 🟢 green | daemon idle — ready for work |
| 🟠 orange | worker busy — a build/render is running (your cue, not your job, to wait) |
| ⚪ gray | daemon down |

Polls `/status` every 5s using the exact same busy semantics as the headless
fallback `python -m ultima3d.tray` (which stays available when you'd rather skip
Electron entirely). No Rust build needed for any of this.

```bash
cd dashboard && npm install   # once; pulls Electron
npm start                     # dashboard + its own daemon child
```

## 🧩 Skill platform

`skills/registry.json` is the source of truth — one entry per skill
`{id, kind, source, version/pin, enabled}`:

| Skill | Kind | Source | State |
|---|---|---|---|
| `ultima3d` | local | this repo (v11) | always on |
| `ponytail` | git | `DietrichGebert/ponytail` (MIT) | vendored sparse checkout, pinned |
| `pullstack` | git | `VenTheZone/pullstack` (MIT) | vendored sparse checkout, pinned |
| `brain` | manual-node | joshtified starter kit | ships as a slot — drop the starter files into `skills/brain/` to light it up (stays disabled until then) |

```bash
python -m ultima3d.skill skills                  # list skills + pins + flags
python -m ultima3d.skill skill-update ponytail   # dry run (default: changes nothing)
python -m ultima3d.skill skill-update --all --live   # actually fast-forward git skills
```

Git skills are vendored as **plain files** (no nested `.git`, so the push stays a
clean single repo); the updater re-clones and re-pins on demand and records the
new SHA in the registry. `brain` is verified with its own CLI, never fetched.
Note: `pullstack`'s artifact targets want Docker+gVisor on Linux — on this
Windows machine the repo targets work and artifact work stops with a message.

## 🧷 Modules

Every backend is a toggle in `state/config.json`
(`procedural`, `live`, `gimp`, `godot` — plus per-skill flags). The server only
imports enabled modules, so a disabled backend's tools vanish from `/tools`
entirely rather than erroring at call time:

```bash
python -m ultima3d.skill modules                            # show flags
python -m ultima3d.skill modules --disable procedural      # take the worker offline
python -m ultima3d.skill start                             # restart applies it
python -m ultima3d.skill modules --enable procedural
```

Toggles apply on **daemon restart** — the dashboard's Modules tab has the restart
button right next to the cards. (While `procedural` is off, pipeline calls are
refused with a clear error instead of hanging a missing worker.)

## 🔔 Tray

Two flavors, same status model:

- **Dashboard tray** (default) — owned by the Electron app, menus for Show /
  Start / Restart / Stop / Quit, idle chime when a long job finishes.
- **Headless fallback** — `python -m ultima3d.tray` owns the server with the
  same menu, model and chime, no Electron involved. The Rust
  `ultima3d-tray` binary remains the optional fast path (`cargo run -p ultima3d-tray`).

The tray mark itself is rendered by Ultima3D: `output/icon_work/render_icon.py`
builds a beveled block in headless Blender
(`rust/crates/ultima3d-tray/assets/mark.png`), composited over a per-status
backdrop at runtime.

## ▶️ Run & CLI

```bash
python -m ultima3d.skill call <tool> '<json>'   # one tool call (boots daemon+dashboard first time)
python -m ultima3d.skill tools                  # all 65 tools
python -m ultima3d.skill status                 # daemon + session budget
python -m ultima3d.skill stop                  # stop daemon + Blender worker
python -m ultima3d.server                      # stdio MCP (what an MCP client launches)
python -m ultima3d.server --http 127.0.0.1:8791   # loopback daemon in the foreground (+ dashboard)
python -m ultima3d.skill dashboard             # dashboard window alone (attaches itself)
```

`--http` binds loopback only and refuses any other host; the daemon dispatches
through the same tool manager as stdio, so validation and error text are
identical. `ULTIMA3D_DAEMON_TOKEN` optionally requires an `X-Ultima3D-Token`
header. `ULTIMA3D_DAEMON` overrides the address (default `127.0.0.1:8791`).

### Session budget

One server process is capped at **40 minutes** (`ULTIMA3D_SESSION_MINUTES`). The
clock starts with the process, `ultima3d_status` reports
`session.remaining_seconds`, every tool call is refused past the deadline, and a
watchdog kills the Blender worker and exits. Nothing runs continuously: a new
session means an explicit restart — and a restart begins a new budget (the cap
bounds one run, not a rolling window). The pre-existing Rust binary has no cap
of its own.

### 📁 Where outputs go

Ultima3D is a **shared module** — its outputs never mix into its own code.
Everything (renders, exports, `daemon.log`, `worker.log`) lands in
**`<your-project>/output`**, resolved from the directory you invoke from;
`ULTIMA3D_OUT` overrides it explicitly and always wins (the daemon, dashboard
and tray all forward it, so a daemon booted from anywhere still writes to your
project). Run from your game/project folder, not from this checkout.

## 🛠️ Tools

All **65 tools + 1 prompt** (`asset_creation_strategy`) on the one server,
over stdio MCP or the daemon — 18 procedural/pipeline, 36 live-Blender
(incl. BlenderKit: search + preview + import free models/materials/HDRs),
6 Godot, 5 GIMP:

| Tool | Purpose |
|---|---|
| `create_3d(recipe, name, blueprint)` | Build from a parametric recipe (36 builders) + optional design blueprint |
| `compile_blueprint(blueprint)` | Validate a v1 blueprint and compile it to recipe nodes + warnings |
| `refine_asset(params)` | `{"changes": {"roof.height": 0.55}}` patches params, or `{"recipe": {...}}` replaces wholesale |
| `set_geometry(builder, node_name, params)` | Add one node to the current recipe and rebuild |
| `inspect_asset()` | Machine facts: triangles, dimensions, materials, UVs, modifiers, recipe, blueprint |
| `render_asset(views, resolution)` | `front, back, left, right, front_34, back_34, top, wire` — returned for vision evaluation |
| `set_material(...)` | PBR material + optional procedural texture preset (12 presets: wood, bark, stone, mossy, grass, worn/brushed metal, fabric, plaster, concrete, leather, soil) |
| `validate_asset(triangle_budget)` | 7 machine checks (see below) |
| `check_assertions(assertions, materials)` | Part count, ratios, symmetry, color coverage over renders; unmeasurable → `manual` |
| `finalize_asset(...)` | Clean → UV → decimate → bake → LODs → collision → GLB + `asset.json` |
| `rig_asset(bones)` | Multi-bone auto-rig with automatic weights |
| `save_recipe(path)` / `load_recipe(path, rebuild)` | Editable, replayable generation history (JSON) |
| `ultima3d_status()` | Worker health: Blender version, liveness |
| `reset_scene()` | Clear the scene and forget the current recipe/blueprint |
| `ultima3d_status_all`, `export_asset_to_godot`, `review_asset_loop` | Pipeline: status fan-out, GLB handoff, validate+critic review gate |

`finalize_asset` defaults: `triangle_budget=8000`, `collision="auto"`,
`texture_resolution=1024`, `channels=["albedo","normal","roughness"]`, `bake=True`
(`metallic` opt-in). Every build keeps an in-memory **asset manifest** — each
recipe node registers the objects it created, so downstream ops act on the whole
asset; there is no "primary object".

## 🧱 Builders (36)

The recipe vocabulary — verified to build on Blender 5.2:

`box, rounded_box, cylinder, tapered_cylinder, sphere, torus, arch, panel, pipe,
curve, extrusion, lathe, rock, beam, barrel, crate, roof, window, door, stairs,
column, wall, fence, tree, foliage_cluster, weapon_blade, weapon_handle, text,
terrain, scatter, skydome, stone_kerb, flower_bed, courier_statue, bench,
lantern_post`

Under them sit 17 deterministic low-level constructors, including `boolean_cut`,
`array` and `radial_array` (object references, so not recipe-addressable). Above
them sit `build_*` composites like `build_roof` (solidified extrusion) and
`build_barrel` (lathe plus hoops). Misspelled params don't silently vanish — the
build reports them as `ignored_params`; check that list ("built" ≠ "accepted
what you asked for"). Clover Village set: `flower_bed` (domed soil, clover,
rosettes, colour-grouped blooms), `courier_statue` (seated rabbit-courier focal
piece), `bench`, `lantern_post`, `stone_kerb` (raised bed boundary with
per-course continuity checks).

## 🏞️ Vistas

A vista is an ordinary recipe that composes an environment scene: `terrain`
meshes seeded value-noise ground, `scatter` stamps an earlier recipe node across
it (placement and mesh share one height function — instances sit on the ground
by construction), `skydome` adds a gradient sky. The dome renders but is
excluded from inspect/validate/finalize/export. Budget tens of thousands of
triangles, not the 8k single-asset default. Same seed → same triangles
(`tests/vista_test.py`).

```python
create_3d(recipe={
  "ground": {"builder": "terrain", "size": [24, 24], "segments": 32, "height": 1.6, "seed": 7,
              "material": {"name": "grass", "color": [0.3, 0.45, 0.2], "roughness": 0.9}},
  "oak":    {"builder": "tree", "height": 3.0},
  "grove":  {"builder": "scatter", "source": "oak", "terrain": "ground", "count": 6, "seed": 3},
  "sky":    {"builder": "skydome", "radius": 120},
}, name="meadow_vista")
```

## 🎨 Procedural textures

No downloads: a material's optional `texture` preset builds a deterministic
EEVEE-safe shader chain (TexCoord → Mapping → Noise/Voronoi/Wave → ColorRamp →
Principled + Bump) instead of a flat color. Each preset layers fine grain over
a large base pattern and drives color, roughness, and bump from the mix.

| Preset | Built from | Good for |
|---|---|---|
| `wood_grain` | Wave bands + fine grain, varied roughness | beams, crates, handles, doors |
| `bark` | Voronoi cells + grain, rough everywhere | tree trunks |
| `stone` | Voronoi F1 + grain, mottled roughness | rocks, walls, arches |
| `mossy` | two-scale Noise blend | vista rocks, damp stone |
| `grass_blend` | two-scale Noise, subtle relief | terrain |
| `worn_metal` | two-layer Noise color/roughness | blades, latches, hoops |
| `fabric` | crossed Wave weave + Bump | cushions, curtains, clothing |
| `plaster` | soft low-contrast Noise, matte | interior walls, ceilings |
| `concrete` | two-scale Noise + Bump | floors, pillars, exteriors |
| `brushed_metal` | stretched Noise streaks, metallic | appliances, rails, trim |
| `leather` | Voronoi cells broken by Noise | chairs, straps, bags |
| `soil` | two-tone Noise + strong Bump | garden beds, terrain patches |

Every preset now layers fine grain over its base pattern and drives both
roughness and bump from the mix, so light breaks up across the surface
instead of sitting flat like a prototype paint job.

`tex_scale` tiles, `tex_blend` shifts the ramp midpoint, `tex_seed` varies the
mapping. Unknown names warn and fall back to flat color. Chains survive baking
(`tests/texture_test.py`).

## 🖨️ GIMP

Pairs your GIMP install (2.10 or 3.x) with the same headless synthesizer, so
texture work never waits on the GUI — and unlike the old Photoshop CS3 bridge,
GIMP ships a real CLI, so calls complete or fail loudly instead of wedging:

- `gimp_make_texture(preset, size, seed)` — stdlib-only seamless
  albedo/rough/normal trios under `output/textures/`; same seed → identical bytes.
- `gimp_status()` — exe found (GIMP 3 preferred), `--version`, Script-Fu
  reachability, presets. Every probe bounded, never hangs.
- `gimp_exec(script)` / `gimp_create_image(...)` / `gimp_run_script(...)` —
  batch Script-Fu in a throwaway instance plus bundled `.scm` presets
  (`prep_image`, `export_png`).

`ULTIMA3D_GIMP` overrides the exe. Live control of the open app needs a
user-started Script-Fu server (`127.0.0.1:10008`); everything degrades
gracefully when GIMP is absent.

## 📐 Blueprint contract (v1)

The `blueprint` argument to `create_3d` is the semantic design doc: perception
writes it, the LLM compiles it to recipe nodes, the critic checks it. Persisted
in `asset.json` and by save/load_recipe. Each section names producer and
consumer — a section no vision pass can fill or no stage can read doesn't belong.

| Section | Producer | Consumer |
|---|---|---|
| `meta` | blueprint authoring | traceability |
| `asset` | passes 1–2 | director decisions |
| `scale` | pass 2 (basis object of known size) | unit conversion |
| `parts[]` | pass 3 (forms + evidence) | one recipe node each |
| `relations[]` / `ratios` | pass 4 | placement; critic checks |
| `materials{}` | pass 4 | inlined into nodes |
| `detail` | pass 5 | profile + budgets |
| `depth` | pass 3 (occlusion pairs + view) | critic raycasts |
| `assertions[]` | pass 5 (what "done" looks like) | `check_assertions` |

Predicates: `sits_on, centered_on, attached_to, inset_in, beside` + `offset`.
`parts[].confidence` below ~0.7 means verify-or-ask, not guess.

<details>
<summary>Full v1 example (medieval house) — click to expand</summary>

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

</details>

### Blueprint protocol

Five passes turn one reference image into a v1 blueprint (the server is blind —
passes run in the vision-capable director; `compile_blueprint` and
`check_assertions` are the mechanical backstops):

1. **Identify** — type, style, view → `meta`, `asset.name/type/style_tags`.
2. **Scale + symmetry** — basis object, axis, hidden-side guesses → `scale`, `symmetry`.
3. **Decompose** — forms with `builder/size/center/evidence`, front-to-back depth first (occlusion determines extrusion depths) → `parts[]`, `depth`.
4. **Relate** — predicates, ratios, palette with coverage → `relations[]`, `ratios`, `materials{}`.
5. **Specify done** — profile, budget, one assertion per checkable fact → `detail`, `assertions[]`.

Critic order: `render_asset` → `check_assertions` (occlusion raycasts the depth
pairs; `mirror_depth` re-renders hidden-side depth — lighting can't fake
either). Every `fail` becomes a `refine_asset` change; nothing is done while any
result is `fail` or any `manual` is unverified; re-run after each refine.
Node-level ratios need an unjoined scene (`join: false`). Unknown assertion
types fail loudly.

## 🦴 Rigging

`rig_asset()` builds a vertical bone chain where the geometry actually is: a
16-bin world-Z histogram weighted by vertex count, `min(8, max(3, non-empty
bins // 2))` bones (or explicit `bones`), joints at cumulative-density
fractions, tapering radii, `ARMATURE_AUTO` weights with a rigid fallback that
keeps every group exportable. Idempotent, pose-probe verified, survives GLB
export/re-import (`tests/roundtrip_test.py`); `finalize_asset` records the
`armature/bones/joints_z` block.

## ✅ Validation

`validate_asset` — seven machine checks, all measured in `bpy`, aggregated over
**every** mesh: `no_duplicate_vertices`, `no_zero_area_faces`,
`triangle_budget`, `has_uv`, `has_material`, `scale_applied`,
`non_manifold_edges` (advisory — stylized hard-surface is rarely watertight).
`inspect_asset` reports union bounds, dimensions, counts, triangles, materials,
non-manifold edges. Detail profiles (`draft` 8 / `game` 16 / `hero` 32) fill
whichever density knobs a builder accepts; explicit params always win; per-node
`triangle_budget` is advisory, the asset-wide budget is enforced.

## 📦 Output contract

`finalize_asset` writes to `<your-project>/output/export` (i.e. `$ULTIMA3D_OUT/export`):

- `<name>.glb` — main asset (rigged when a rig exists)
- `<name>_LOD1.glb` — decimated LOD per budget entry
- `<name>_collision.glb` — convex hull of the union
- `<name>_<channel>.png` — baked PBR channels (per mesh when multi-mesh)
- `asset.json` — triangles, dimensions, materials, objects, manifest, texture paths, rig block

## 🗂️ Layout

```text
dashboard/               Electron app: main.js (daemon child, tray, IPC), daemon-client.js
                         (loopback HTTP), preload.js, renderer/ (Daemon/Modules/Skills),
                         icons/ (tray PNGs, generatable via icons/generate_icons.py)
ultima3d/                Python host: server.py (18 tools), skills.py (registry+updater),
                         config.py (module/skill flags), daemon.py (loopback JSON),
                         skill.py (shell bridge), live/ (32), godot/ (6), gimp/ (5),
                         session.py (40-min budget), tray.py (headless tray fallback)
skills/registry.json     skill source of truth (ultima3d/ponytail/pullstack/brain)
skills/ponytail/         vendored: DietrichGebert/ponytail (MIT, sparse, pinned)
skills/pullstack/        vendored: VenTheZone/pullstack (MIT, sparse, pinned)
skills/brain/            joshtified starter kit (manual drop-in; disabled until present)
skills/ultima3d/SKILL.md the Freebuff Desktop skill (installs to ~/.agents/skills)
state/config.json        your local module/skill flags (git-ignored, never committed)
blender/worker.py        geometry compiler (runs inside Blender's interpreter — stays Python)
blender/addon.py         live-Blender GUI addon (install into your Blender, port 9876)
gimp/scripts/            bundled Script-Fu presets (prep_image, export_png)
godot/addon/             Godot editor plugin (into your project's addons/)
rust/                    optional fast host: ultima3d-server + ultima3d-tray binaries
tests/                   pytest-style suites, incl. skills_manager + output_routing
output/                  YOUR renders/exports when invoking from this checkout
                         (from your project dir, that's <project>/output instead)
```

A plain `git clone` is all you need — no submodules (vendored skills are plain
files; the updater re-clones on demand). GitHub CI builds the Rust workspace
and runs `cargo test`, plus a Python-host job (65-tool parity, session budget,
skill package, daemon round-trip; no Blender/Godot required). Releases are cut
from `v*` tags with both Windows binaries attached. The live tools were ported
from [mcp-for-blender](https://github.com/ahujasid/mcp-for-blender); upstream
merges are manual from here.

## 🔌 Backends

One server, four backends — all degrading gracefully when their editor is offline:

- **Procedural** — background Blender worker, deterministic + measurable.
- **Live Blender** — drives your open Blender over `BLENDER_HOST:BLENDER_PORT`
  (default `localhost:9876`). Install `blender/addon.py`, enable it in preferences.
- **Godot** — drives the running editor over `GODOT_MCP_URL` (default
  `http://127.0.0.1:8765/mcp`). Copy `godot/addon/` into your project's
  `addons/` and enable **Ultima3D Godot Bridge** under Plugins.
- **GIMP** — batch Script-Fu that completes or fails loudly, plus headless
  texture synth (see above).
- **Pipeline** — status fan-out, GLB handoff, validate+critic review gate.

Config (env): `ULTIMA3D_BLENDER`, `ULTIMA3D_OUT` (your project output — never
the checkout), `ULTIMA3D_WORKER` (default `blender/worker.py`),
`ULTIMA3D_SERVER` (tray binary), `ULTIMA3D_CALL_TIMEOUT` (default 600s),
`ULTIMA3D_SESSION_MINUTES` (default 40), `ULTIMA3D_DAEMON` (default
`127.0.0.1:8791`), `ULTIMA3D_DAEMON_TOKEN`, `ULTIMA3D_NO_DASHBOARD`
(headless opt-out). Requires **Blender 5.2 LTS**; Rust stable only for the
optional host.

## 🤝 Freebuff wiring

**Desktop (skill, no MCP servers):**

```bash
python -m ultima3d.skill install     # skills/ultima3d -> ~/.agents/skills/ultima3d
```

Reload the desktop; the skill tells the agent to run
`python -m ultima3d.skill call <tool> '<json>'` from the **project** directory,
on this machine only, respecting the session budget. Each call is one JSON
result; renders come back as PNG paths to actually open and look at.

**MCP clients (CLI, other agents)** — `~/.agents/mcp.json`:

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

One long-lived Blender worker per server process: scene, recipe and blueprint
persist across calls under the 40-minute budget. Restart the server after
changing code.

## 🧭 Architecture boundary

The LLM decides *what should exist*; the worker measures *what actually
happened*. Validation numbers come from `bpy`, never from the model.
Refinement is parametric — a revision is a diff against a recipe, not a rewrite
of geometry.

## 🏁 Milestones

- **v0.1** — deterministic procedural asset compiler proven.
- **v0.2** — trustworthy compiler: asset-wide measurements, enforceable
  budgets, consistent materials, tested rigs, round-trip-verified exports.
- **v0.3** — export-only finalize (decimation never touches the scene, idempotent
  reruns) + bounded worker calls.
- **v0.4** — blueprint compiler: v1 contract, `compile_blueprint` + `check_assertions`.
- **v0.5** — Rust MCP host: 65-tool surface as one binary; only in-editor code
  stays native.
- **v0.6** — full Rust switch + tray port + CI/release workflows.
- **v0.7** — Python host restored (no build step), loopback daemon + Freebuff
  skill, 40-minute session budget.
- **v8** — Photoshop CS3 removed (ExtendScript wedged); GIMP backend in its
  place (batch Script-Fu proven live on GIMP 3.2.8); first end-to-end GLB shipped.
- **v9** — worker lifecycle diagnostics + robust worker-script resolution; baked
  (not white) PBR exports with opt-out material merge; text signage builder;
  foliage_cluster + stone_kerb with spec and colour diagnostic.
- **v10** — Clover Village set: flower_bed, courier_statue, bench,
  lantern_post, stone_kerb ring; SKILL.md documents all builders.
- **v11 (this tree)** — skill-hosting platform: registry + updater for
  ultima3d/ponytail/pullstack/brain, module/skill toggles via daemon or CLI,
  Electron dashboard (npm start, tray 🟢/🟠/⚪, Modules/Skills/Daemon tabs),
  boot-to-dashboard with headless opt-out, invoker-directory output isolation.

## ⚠️ Known limitations

- A builder's `material` applies to every object its node created — split the
  node for per-sub-part materials.
- Baked textures ship beside the GLB rather than embedded in it.
- Per-node `triangle_budget` is advisory; only the asset-wide budget is enforced.
- `finalize_asset` embeds textures only when Blender's exporter does so via
  material node images; external bake PNGs are canonical (in `asset.json`).
- The 40-minute budget belongs to the Python host + daemon; the Rust binary has
  no cap of its own.
- The daemon has no auth unless `ULTIMA3D_DAEMON_TOKEN` is set — loopback-only
  by design, refuses any other bind address.
- Restarting the server begins a new 40-minute budget (one run, not a window).
- GIMP batch runs headless; live control needs a user-started Script-Fu server
  on `127.0.0.1:10008`.
- Packaging (electron-builder installer) is deferred — run the dashboard from
  source with `npm start`.
