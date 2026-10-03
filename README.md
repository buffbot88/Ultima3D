# ultima3d (Project Ultima3D) — Agentic 3D Asset Generator MCP

AI-directed procedural 3D synthesis: the LLM owns **intent and visual judgement**, the
Blender worker owns **mechanics and verification**. Exposes 15 high-level MCP tools, not
hundreds of Blender buttons.

**Milestones.** v0.1 — deterministic procedural asset compiler proven.
v0.2 — trustworthy game-asset compiler: measurements are asset-wide, budgets
are enforceable, materials are consistent, rigs are tested, and exported artifacts are
round-trip verified.
v0.3 — export-only finalize (decimation never touches the scene, reruns are
idempotent) and bounded worker calls (a hung Blender is killed past a deadline).
v0.4 (this tree) — blueprint compiler: v1 contract, `compile_blueprint` +
`check_assertions` critic (counts, ratios, symmetry, color coverage, occlusion,
mirrored depth).

```text
LLM (any MCP client)
 │  create_3d(recipe, blueprint)
 ▼
ultima3d MCP server (Python, stdio, mcp 2.x)
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

## Tools

All 15 are registered on the `ultima3d` MCP server.

| Tool | Purpose |
|---|---|
| `create_3d(recipe, name, blueprint)` | Build from a parametric recipe (26 builders) + optional design blueprint |
| `compile_blueprint(blueprint)` | Validate a v1 blueprint and compile it to recipe nodes + warnings |
| `refine_asset(params)` | `{"changes": {"roof.height": 0.55}}` to patch params, or `{"recipe": {...}}` to replace wholesale |
| `set_geometry(builder, node_name, params)` | Add one node to the current recipe and rebuild |
| `inspect_asset()` | Machine facts: triangles, dimensions, materials, UVs, modifiers, recipe, blueprint |
| `render_asset(views, resolution)` | `front, back, left, right, front_34, back_34, top, wire`, returned inline for vision evaluation |
| `set_material(name, color, roughness, metallic, object)` | Apply a PBR material to the asset or a named object |
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

26 builders form the recipe vocabulary:

`box, rounded_box, cylinder, tapered_cylinder, sphere, torus, arch, panel, pipe, curve,
extrusion, lathe, rock, beam, barrel, crate, roof, window, door, stairs, column, wall,
fence, tree, weapon_blade, weapon_handle`

Under them sit 17 deterministic low-level constructors, including `boolean_cut`, `array` and
`radial_array` (the latter three take object references, so they are not recipe-addressable).
Above them sit 12 `build_*` composites, such as `build_roof` (a solidified extrusion) and
`build_barrel` (a lathe plus hoops). All 26 registered builders are verified to build on
Blender 5.2.2.

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

## Cloning

`blender_mcp` is a git submodule pointing at our fork of
[mcp-for-blender](https://github.com/ahujasid/mcp-for-blender) — so a plain
`git clone` gives you an empty folder:

```bash
git clone --recurse-submodules https://github.com/buffbot88/Ultima3D.git
cd Ultima3D
```

If you already cloned without the flag:

```bash
git submodule update --init --recursive
```

### Following upstream blender_mcp

The submodule's `local-changes` branch sits on top of upstream history with our local
adaptations (stubbed telemetry, `image_files` for hosts that can't render image blocks).
To pull in upstream changes:

```bash
cd blender_mcp
git remote add upstream https://github.com/ahujasid/mcp-for-blender.git  # once
git fetch upstream
git checkout local-changes
git merge upstream/main          # resolve conflicts, then run blender_mcp's tests
cd ..
git add blender_mcp              # record the new submodule commit
```

`godot_mcp` is not a submodule — it is regular tracked code and needs no special handling.

## Run

```bash
pip install -e .
python tests/smoke_test.py      # end-to-end: build → render → validate → finalize → rig → re-finalize
python tests/roundtrip_test.py  # build → rig (auto+fallback) → finalize → clear → re-import GLB → compare vs manifest
python tests/builders_test.py   # sweep all 26 builders: each registers, inspects and validates
python tests/params_test.py     # recipe/refine parameter reporting, over the MCP server itself
python tests/blueprint_test.py  # blueprint compile + critic: counts, ratios, symmetry, color, occlusion, mirror depth
python tests/timeout_test.py    # bounded worker calls: hung Blender is killed past the deadline
```

Config (env): `ULTIMA3D_BLENDER` (Blender executable), `ULTIMA3D_OUT` (output dir),
`ULTIMA3D_CALL_TIMEOUT` (per-call worker deadline in seconds, default 600 — a hung
worker is killed past it, losing its scene state).

Requires **Blender 5.2 LTS** and **mcp >= 2.0**. Note that `mcp` 2.x renamed `FastMCP` to
`MCPServer`; `mcp.server.fastmcp` deliberately does not resolve.

## Freebuff / MCP client wiring

`~/.agents/mcp.json`:

```json
{
  "mcpServers": {
    "ultima3d": {
      "command": "python",
      "args": ["C:/Users/buffb/Desktop/GitHub/Ultima3D/ultima3d/server.py"]
    }
  }
}
```

The client launches one long-lived Blender worker per server process, so scene, recipe and
blueprint state persist across tool calls. Edits to the Python files are not picked up by an
already-running server — restart the MCP server after changing code.

## Architecture boundary

The LLM decides *what should exist*; Python measures *what actually happened*. Validation
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
