---
name: ultima3d
description: Builds, renders, inspects, refines, validates and exports game-ready 3D assets in Blender from parametric recipes (GLB, LODs, collision hull, baked PBR), and can drive live Blender, Godot 4 and GIMP. Use when the user wants a 3D model, prop, weapon, building, vista or environment, a GLB for a game, triangle/dimension measurements, a render to look at, or a Godot/GIMP handoff.
license: MIT
compatibility: Requires this repo on the user's own machine, Blender 5.2 installed, and Python 3.10+ with mcp and httpx. Serves 127.0.0.1 only; cannot run in a cloud workspace.
metadata:
  version: "9"
---

# Ultima3D — agentic 3D asset work on this machine

Ultima3D builds 3D assets from parametric recipes in a headless Blender, measures them in `bpy`,
renders them for you to look at, and exports game-ready GLB (LODs, collision hull, baked PBR).
The loop is **build → look → judge → refine → validate → ship**. You own intent and visual
judgement; the tools own mechanics and numbers.

## 0. Where this runs — read first

Every command below must run on the **user's own computer**, in the repo root (the folder holding
`ultima3d/`, `blender/worker.py` and `skills/`). The server launches a local Blender and talks to
`127.0.0.1`; a cloud workspace has neither the Blender install nor the user's editor, so tool calls
there will fail. If you are not on the user's machine, say so instead of improvising.

Check before promising anything:

```bash
python -m ultima3d.skill status
```

## 1. One command is the whole surface

```bash
python -m ultima3d.skill call <tool> '<json-args>'
```

- The first call starts a loopback daemon (`127.0.0.1:8791`) that owns the Blender worker, so the
  scene, recipe and blueprint **persist between calls**. Do not spawn Blender yourself.
- `python -m ultima3d.skill tools` lists all 61 tools with one-line descriptions.
- `python -m ultima3d.skill status` shows the daemon, the tool count and the session budget.
- `python -m ultima3d.skill stop` stops the daemon and the Blender worker.
- Long JSON also works from a file: `--args-file args.json`, or piped on stdin.

Responses are JSON. `{"ok": false, "error": "..."}` means the call failed — read the message; it
carries the real Blender error, not a generic wrapper. Image-returning tools (`render_asset`,
`get_viewport_screenshot`, `godot_screenshot`) come back as **file paths**; open the PNG and look at
it before judging anything.

## 2. The 40-minute session budget

Hard cap, enforced in the server: 40 minutes per server process (`ULTIMA3D_SESSION_MINUTES`).
`status` reports `session.remaining_seconds`. Past the deadline every call is refused and the
server kills the Blender worker and exits.

Plan the work to fit: one asset end to end, not an open-ended grind. When the budget runs out,
report what exists and what remains — do not restart the server to keep going unless the user
explicitly asks for another session.

## 3. The workflow

1. **Build** — `create_3d` with a recipe (and optionally a blueprint). It returns node names and a
   triangle count.
2. **Look** — `render_asset` (front/back/left/right/front_34/back_34/top/wire). Open the images.
3. **Judge** — proportions, silhouette, read-at-a-glance, obvious gaps. Say what is wrong.
4. **Refine** — `refine_asset({"changes": {"roof.height": 0.55}})` patches the recipe and rebuilds
   deterministically; `set_material` and `set_geometry` are the other parametric edits.
5. **Validate** — `validate_asset` (7 machine checks) plus `check_assertions` against the
   blueprint. Fix failures before shipping.
6. **Finalize** — `finalize_asset` (clean, UV, decimate, bake, LODs, collision, GLB + `asset.json`).
7. **Hand off** — `export_asset_to_godot` copies the GLB into a Godot project.

```bash
python -m ultima3d.skill call create_3d '{
  "name": "medieval_mailbox",
  "recipe": {
    "post": {"builder": "box", "size": [0.12, 0.12, 1.1], "location": [0, 0, 0.55],
             "material": {"name": "oak", "color": [0.42, 0.28, 0.16], "roughness": 0.75,
                          "texture": "wood_grain"}},
    "box":  {"builder": "rounded_box", "size": [0.45, 0.3, 0.4], "bevel": 0.03,
             "location": [0, 0, 1.2],
             "material": {"name": "painted_metal", "color": [0.15, 0.28, 0.22],
                          "roughness": 0.45, "metallic": 0.7}},
    "roof": {"builder": "roof", "width": 0.55, "depth": 0.38, "height": 0.16,
             "location": [0, 0, 1.42]}
  }}'
python -m ultima3d.skill call render_asset '{"resolution": 640}'
```

## 4. Tools

**Procedural / pipeline (18)** — `create_3d`, `compile_blueprint`, `refine_asset`, `inspect_asset`,
`render_asset`, `set_material`, `set_geometry`, `validate_asset`, `check_assertions`,
`finalize_asset`, `rig_asset`, `save_recipe`, `load_recipe`, `reset_scene`, `ultima3d_status`,
`ultima3d_status_all`, `export_asset_to_godot`, `review_asset_loop`.

**Live Blender GUI (32)** — `get_scene_info`, `get_object_info`, `execute_blender_code`,
`get_viewport_screenshot`, `get_addon_status`, asset-library tools (Poly Haven, Sketchfab, Poly
Pizza, Hyper3D Rodin, Hunyuan3D) and the rest, all driving the user's **open** Blender over
`localhost:9876`. Run `tools` for the exact names.

**Godot 4 (6)** — `godot_status`, `godot_scene_tree`, `godot_run_project`, `godot_stop_project`,
`godot_debug_output`, `godot_screenshot` (bridge at `GODOT_MCP_URL`, default
`http://127.0.0.1:8765/mcp`).

**GIMP (5)** — `gimp_status`, `gimp_exec`, `gimp_create_image`,
`gimp_run_script`, `gimp_make_texture` (headless texture synth, no GIMP needed).
Batch Script-Fu always completes or fails loudly; nothing wedges the editor.

Documented defaults: `finalize_asset` uses `triangle_budget=8000`, `collision="auto"`,
`texture_resolution=1024`, `channels=[albedo, normal, roughness]`, `bake=True`.

## 5. Recipes

29 builders: `box, rounded_box, cylinder, tapered_cylinder, sphere, torus, arch, panel, pipe,
curve, extrusion, lathe, rock, beam, barrel, crate, roof, window, door, stairs, column, wall,
fence, tree, weapon_blade, weapon_handle, terrain, scatter, skydome`.

- A node is `{"builder": "...", plus that builder's parameters, optional "material": {...}}`;
  a node's material applies to every object it creates.
- `detail: draft|game|hero` picks density; an explicit density parameter always wins.
- Material spec: `color` [r,g,b], `roughness`, `metallic`, optional `texture` preset
  (`wood_grain, bark, stone, mossy, grass_blend, worn_metal`) with `tex_scale`, `tex_blend`,
  `tex_seed`. Unknown preset names warn and fall back to flat color.
- Vistas compose environment scenes: `terrain` (seeded value noise), `scatter` (stamps an earlier
  node across a terrain node by name), `skydome` (render-only backdrop, excluded from
  validate/finalize/export). Budget tens of thousands of triangles for a vista, not 8k.
- A misspelled builder parameter is not always an error: the result reports `ignored_params`.
  Check that list — "built" is not the same as "accepted what you asked for".

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `blender not found at ...` | Set `ULTIMA3D_BLENDER` to `blender.exe`, or install Blender 5.2 |
| `daemon unreachable` | `python -m ultima3d.skill status`; the daemon log is `output/daemon.log` |
| Live tools time out | Open Blender, enable the `blender/addon.py` addon, Start MCP Server (port 9876) |
| `session budget exhausted` | The 40-minute cap is spent: report and stop; a restart needs the user's go-ahead |
| Render paths but no judgement | Actually open the PNGs; never claim a look you did not see |
| Godot/GIMP tools `ok: false` | Those editors are offline — that is state, not a crash |

## 7. Rules

- Measure with tools; never estimate triangles, dimensions or material values.
- Change geometry through the recipe (`refine_asset`), not by rewriting a scene.
- Do not `finalize_asset` before `validate_asset` passes (or explain the exception).
- Report the real result, including failures and the budget you have left.
