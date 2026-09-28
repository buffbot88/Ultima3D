# Our Build Plan — ultima3d

## Context

This project was built in a prior session **without plan approval**, violating the vows now
in effect. This document records what exists, the plan that would have governed it, and the
known deviations requiring either user acceptance or remediation.

## Goal

An agentic 3D asset generator MCP: the LLM owns intent and visual judgement; a headless
Blender 5.2 worker owns mechanics and verification. ~13 high-level tools, parametric
recipes, game-ready GLB output (LODs, collision, validation) for Godot.

## Files (as built)

| File | Why |
|---|---|
| `pyproject.toml` | Package metadata; `mcp` dependency; `ultima3d` entry point |
| `ultima3d/blender_worker.py` | JSON-lines RPC worker run by `blender --background`; geometry compiler (17 constructors, 12 `build_*` composites), 26 registered builders, 11 RPC ops (build/render/validate/finalize/rig/recipe) |
| `ultima3d/server.py` | MCP server (`mcp` 2.x `MCPServer`), 13 tools, persistent worker management |
| `tests/smoke_test.py` | End-to-end pipeline check without the MCP layer |
| `README.md` | Architecture, tools, run/wiring instructions |
| `C:/Users/buffb/.agents/mcp.json` | Added `ultima3d` server entry (existing entries untouched) |

## Change list (historical)

1. Worker protocol: request/response JSON lines, `@JSON@` prefix to skip Blender stdout noise.
2. Geometry compiler + builder registry; recipe compiler maps `{node: {builder, params, material}}`.
3. Pipeline: join + normalize, 8-view EEVEE/Workbench renders with auto-fit camera, `bpy`-measured validation, finalize (clean → smart UV → decimate → LODs → convex-hull collision → GLB + `asset.json`).
4. MCP surface: `create_3d`, `refine_asset`, `inspect_asset`, `render_asset`, `set_material`, `set_geometry`, `validate_asset`, `finalize_asset`, `rig_asset`, `save_recipe`, `load_recipe`, `ultima3d_status`, `reset_scene`.
5. Registration in Freebuff config.

## Risks / known deviations (as originally recorded)

- **Built without plan approval (vow 7 violated).** User may reject any part; nothing here is irreversible.
- `rig_asset` is a placeholder (single root bone) — conflicts with the no-underbuild vow.
- Texture baking is incomplete: albedo/normal only, no bake-node materials, metallic unhandled; smoke test runs with `bake: False`.
- `create_lathe` leaves open ring ends; `create_arch`/`create_extrusion` rely on solidify for caps.
- Validation counts only the joined main mesh for mesh-level checks.
- No `git` repo initialized; no commits.

## Status of those deviations (updated 2026-09-28)

| Deviation | Status |
|---|---|
| Built without plan approval | **Closed.** Remediation since carried out under separately approved plans. |
| `rig_asset` placeholder (single root bone) | **Resolved.** Multi-bone density-adaptive auto-rig with automatic weights, idempotent, rig metadata in `asset.json`. |
| Baking incomplete (albedo/normal only, metallic unhandled, smoke test with `bake: False`) | **Resolved.** albedo, normal, roughness and metallic all bake; the smoke test now bakes for real and asserts all four PNGs exist and are non-empty. |
| `create_lathe` open ring ends | **Resolved (v0.2).** Off-axis end rings capped with n-gons; near-zero-radius rings fan to a pole vertex. |
| `create_arch`/`create_extrusion` rely on solidify for caps | **Resolved in effect.** Solidify is now baked before joining, so caps survive multi-node recipes. |
| Validation counts only the joined main mesh | **Resolved (v0.2).** Validate, inspect, finalize, materials and collision are asset-wide via the in-memory manifest. |
| No `git` repo initialized | **Resolved.** Repository initialized; v0.1 committed. |

## Validation (already run)

- Worker smoke test: build (4,021 tris as recorded then; the current test file reports 3,884) → 8 renders → validate PASS → finalize with LOD1 + collision → parametric rebuild. All pass on Blender 5.2.2 LTS.
- MCP stdio handshake: initialize, tools/list (13 tools), live `ultima3d_status` → `{"ok": true, "blender": "5.2.2 LTS"}`.

## Remediation pass — Blender and the agentic loop (2026-09-28)

User directive: concentrate on Blender and the agentic 3D agent; Godot deprioritised.
Probing all 26 builders on Blender 5.2.2 and driving the live connector surfaced five
defects. Each was presented as a plan and approved before any edit:

| # | Defect | Root cause | Fix |
|---|---|---|---|
| 1 | Multi-node recipes silently lost geometry | `bpy.ops.object.join()` discards modifiers on every object but the active one, flattening extrusions, arches, roofs and boolean cuts into sheets | Bake `BEVEL/SOLIDIFY/ARRAY/BOOLEAN/SUBSURF/DISPLACE` into each mesh before joining; shading `NODES` left intact |
| 2 | `build_window` crashed outright | `Boolean.solver = "FAST"`, removed in Blender 5.2 (valid: `FLOAT`, `EXACT`, `MANIFOLD`) | `solver="EXACT"` validated against the enum; cutter parked in a hidden `ASSET_CUTTERS` collection so it can never be joined in as a stray solid |
| 3 | `build_rock` crashed outright | `DisplaceModifier.seed` does not exist | Removed; `seed` now drives deterministic vertex jitter that survives a join |
| 4 | The model could not read its own failures | Tools raised `RuntimeError`; mcp 2.x reports non-`ToolError` exceptions as `UnexpectedToolError` and withholds the message, leaving only `Error executing tool <name>` | Worker failures raise `ToolError`, so the message and traceback reach the model |
| 5 | `build_roof` sat one full extruded depth off in −Y | Centring offset sign inverted (Solidify extrudes along +Z, which the 90° X rotation maps to −Y) | Positive offset of half the extruded depth |

Evidence recorded at the time:

- Builder sweep **26/26** (was 24/26).
- Join parity: `box+extrusion` 20 = 20 and `box+arch` 144 = 144 with `join` on and off (was 13 and 44).
- Marker-box control case measured exactly its predicted 1.55 before the roof result was trusted.
- Roof position `y ∈ [−2.79, −0.93]` → `[−0.93, 0.93]`; `body + roof` 189 tris / Y 1.58 → **196 tris / `[1.86, 1.86, 2.35]`**.
- `tests/smoke_test.py` green and byte-identical before and after (3884 tris, 6-bone auto-rig, four bake PNGs).
- A sentry post built, validated and rendered end to end through the MCP tool surface.
- Visual check: the browser preview produced no frames and Pillow was unavailable, so render pixels were read through `bpy` and printed as an ASCII luminance map.

**Process failure to record:** defect 5 was initially reported as "the roof builder is
innocent" because only its bounding-box *span* had been measured, which cannot reveal an
offset. The sign error was found afterwards, reported to the user, and fixed only after
separate approval. Measurement that cannot distinguish the hypotheses is not evidence.

## Decision

**Approved as v1 baseline (user, 2026-09-28).** Bake and rig remediation deferred, each
requiring its own approved plan; both were subsequently completed under separately approved
plans, as recorded above.

Outstanding known deviations: per-builder triangle density unbudgeted (now addressed by
detail profiles + advisory per-node budgets, v0.2) and textures not embedded in the GLB.
`tests/smoke_test.py` remains the only automated check and does not cover
any of the five defects above, so none of this pass is regression-protected.

## v0.2 pass — trustworthy game-asset compiler (2026-09-28)

User directive: lock down correctness before adding builders or AI behavior; rename the
project to **Ultima3D** (full rename: package, server name, entry point, `ULTIMA3D_*` env
vars, mcp.json wiring). Plan approved via structured questions (full pass + `force_fallback`
param). Version 0.1.0 labels the v0.1 baseline commit; this pass is the v0.2 milestone.

| Change | Detail |
|---|---|
| Rename | `asset3d/` → `ultima3d/`, server name, entry point, env vars, docs, `~/.agents/mcp.json` |
| Asset manifest | Every build records `{objects per node, materials, root_collection}`; recorded in build result and `asset.json` |
| Asset-wide ops | `inspect` (union bounds/dims/tris/verts/non-manifold), `validate`, `finalize`, `set_material` (no target = all asset meshes) |
| Material propagation | A node's `material` applies to **all** objects the node created (barrel hoops, crate slats, mullions, posts, foliage) |
| Detail profiles | `detail: draft\|game\|hero` resolves density knobs against each builder's actual signature; explicit params win; per-node `triangle_budget` reported (`over_budget` flag) |
| Lathe caps | End rings closed: n-gon caps off-axis rings, pole fans near-zero rings |
| Rig fallback | `rig(force_fallback=true)` exercises the fallback; fallback weights are rigid per-height assignments (empty groups are dropped by glTF export) |
| Round-trip test | New `tests/roundtrip_test.py`: build → rig auto+forced → pose-probe deformation → finalize → clear → re-import GLB → compare mesh/material/triangle/dimension/rig vs manifest |

### Defects found by the round-trip test (and fixed)

1. `asset.json` recorded triangle count **after** LOD decimation had mutated the scene (the
   main GLB was correct; the manifest lied). Fixed: measure before LODs.
2. The rig fallback created **empty vertex groups**, which glTF export drops — the armature
   vanished from the shipped GLB. Fixed: rigid per-bone weight-1.0 assignment by world Z.
3. Pose-bone deformation probe set `rotation_euler` on a quaternion bone (silently
   ignored). Fixed: set `rotation_mode` first.
4. Blender's glTF importer (`bone_heuristic="BLENDER"`) leaves a stray `Icosphere` bind-pose
   helper object in the scene. Fixed: import with `TEMPERANCE` and sweep any remnants.

### Validation

- `tests/smoke_test.py`: green, byte-comparable behavior (3,884 tris, 6-bone auto-rig,
  four bake PNGs) plus new manifest in build output.
- `tests/roundtrip_test.py`: 24/24 checks green — mesh/material/triangle/dimension exact
  match on re-import, rig survives with 3 bones, collision GLB reimports, deformation
  verified (max displacement 0.1379).
- The `_probe_*` ops are test instrumentation (scene/import introspection), not part of
  the MCP surface.
