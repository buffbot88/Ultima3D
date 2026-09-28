# asset3d — Agentic 3D Asset Generator MCP

AI-directed procedural 3D synthesis: the LLM owns **intent and visual judgement**, the
Blender worker owns **mechanics and verification**. Exposes 13 high-level MCP tools, not
hundreds of Blender buttons.

```text
LLM (any MCP client)
 │  create_3d(recipe, blueprint)
 ▼
asset3d MCP server (Python, stdio, mcp 2.x)
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

All 13 are registered on the `asset3d` MCP server.

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
| `asset3d_status()` | Worker health: Blender version, liveness |
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

## Rigging

`rig_asset()` builds a vertical bone chain adapted to where the geometry actually is:

- A 16-bin histogram of world-space Z, weighted by vertex count.
- Bone count `min(8, max(3, non-empty bins // 2))`, or an explicit `bones` value.
- Joints placed at cumulative-density fractions, so they cluster where mass concentrates
  rather than at even intervals.
- Connected chain with tapering bone radii.
- Weights via `parent_set(type="ARMATURE_AUTO")`, with an explicit fallback (parent plus
  empty vertex groups) if Blender's automatic weighting refuses. The result reports
  `weights_source` as `auto`, `fallback`, or `existing`.
- Idempotent: rigging twice returns the existing armature instead of stacking a second one.

`finalize_asset` then exports the rigged GLB and records an `armature`/`bones`/`joints_z`
block in `asset.json`.

## Validation

`validate_asset` returns seven machine checks, all measured in `bpy`:

`no_duplicate_vertices`, `no_zero_area_faces`, `triangle_budget`, `has_uv`,
`has_material`, `scale_applied`, `non_manifold_edges` (advisory by default, since stylized
hard-surface meshes are rarely watertight).

## Output contract

`finalize_asset` writes to `$ASSET3D_OUT/export`:

- `<name>.glb` — main mesh, rigged when a rig exists
- `<name>_LOD1.glb` — decimated LOD per budget entry
- `<name>_collision.glb` — convex hull for collision
- `<name>_<channel>.png` — baked PBR channels
- `asset.json` — triangles, dimensions, materials, texture paths, rig block

## Example

```python
create_3d(recipe={
  "body":  {"builder": "rounded_box", "size": [1, .45, .5], "radius": .06,
            "material": {"name": "wood", "color": [.45, .3, .18], "roughness": .7}},
  "roof":  {"builder": "roof", "width": 1.15, "depth": .55, "height": .35, "location": [0, 0, .5]},
  "latch": {"builder": "rounded_box", "size": [.12, .06, .18], "location": [0, -.25, .15],
            "material": {"name": "brass", "color": [.75, .6, .2], "metallic": 1.0}},
}, name="medieval_mailbox")

render_asset()                                   # look at it
refine_asset({"changes": {"roof.height": .55}})  # fix what you saw
validate_asset()
finalize_asset(name="medieval_mailbox")
```

## Run

```bash
pip install -e .
python tests/smoke_test.py   # end-to-end: build → render → validate → finalize → rig → re-finalize
```

Config (env): `ASSET3D_BLENDER` (Blender executable), `ASSET3D_OUT` (output dir).

Requires **Blender 5.2 LTS** and **mcp >= 2.0**. Note that `mcp` 2.x renamed `FastMCP` to
`MCPServer`; `mcp.server.fastmcp` deliberately does not resolve.

## Freebuff / MCP client wiring

`~/.agents/mcp.json`:

```json
{
  "mcpServers": {
    "asset3d": {
      "command": "python",
      "args": ["C:/Users/buffb/Desktop/GitHub/AssetGeneration_MCP/asset3d/server.py"]
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

- A builder's `material` applies only to the object it returns, so sub-parts (barrel hoops,
  crate slats, window mullions, fence posts, tree foliage) have no material.
- `create_lathe` leaves open ring ends.
- `validate_asset`'s mesh-level checks cover only the joined main mesh.
- `inspect_asset` reports dimensions for a single mesh, not the whole asset.
- Triangle density is unbudgeted per builder: a `torus` is 1152 tris and a default `barrel`
  3600, which can dominate a game-ready budget.
- Baked textures ship beside the GLB rather than embedded in it.
- `tests/smoke_test.py` is the only automated check; the geometry and error-surfacing fixes
  recorded in `Our Build Plan.md` are not yet regression-protected.
