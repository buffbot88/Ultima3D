# ultima3d (Project Ultima3D) — Agentic 3D Asset Generator MCP

AI-directed procedural 3D synthesis: the LLM owns **intent and visual judgement**, the
Blender worker owns **mechanics and verification**. Exposes 13 high-level MCP tools, not
hundreds of Blender buttons.

**Milestones.** v0.1 — deterministic procedural asset compiler proven.
v0.2 (this tree) — trustworthy game-asset compiler: measurements are asset-wide, budgets
are enforceable, materials are consistent, rigs are tested, and exported artifacts are
round-trip verified.

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

All 13 are registered on the `ultima3d` MCP server.

| Tool | Purpose |
|---|---|
| `create_3d(recipe, name, blueprint)` | Build from a parametric recipe (26 builders) + optional design blueprint |
| `refine_asset(params)` | `{"changes": {"roof.height": 0.55}}` to patch params, or `{"recipe": {...}}` to replace wholesale |
| `set_geometry(builder, node_name, params)` | Add one node to the current recipe and rebuild |
| `inspect_asset()` | Machine facts: triangles, dimensions, materials, UVs, modifiers, recipe, blueprint |
| `render_asset(views, resolution)` | `front, back, left, right, front_34, back_34, top, wire`, returned inline for vision evaluation |
| `set_material(name, color, roughness, metallic, object)` | Apply a PBR material to the asset or a named object |
| `validate_asset(triangle_budget)` | 7 machine checks (see below) |
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
```

Config (env): `ULTIMA3D_BLENDER` (Blender executable), `ULTIMA3D_OUT` (output dir).

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
