"""Procedural texture presets: flat PBR upgraded to node chains, no downloads.

Each preset builds in the background worker, then is verified here: the
material exists, carries the expected procedural node, and the asset still
inspects, validates and renders. Unknown preset names must fall back to flat
color, never crash the build.
"""
import json
import os
import subprocess
import sys

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
WORKER = os.path.join(os.path.dirname(__file__), "..", "blender", "worker.py")

proc = subprocess.Popen(
    [BLENDER, "--background", "--factory-startup", "--python", WORKER],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, encoding="utf-8", bufsize=1,
)

_id = 0
failures = []


def call(op, params=None):
    global _id
    _id += 1
    proc.stdin.write(json.dumps({"id": _id, "op": op, "params": params or {}}) + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            print("WORKER DIED")
            sys.exit(1)
        line = line.strip()
        if not line.startswith("@JSON@"):
            continue
        resp = json.loads(line[len("@JSON@"):])
        if resp.get("id") != _id:
            continue
        if not resp.get("ok"):
            print(f"[{op}] ERROR: {resp['error']}\n{resp.get('trace', '')}")
            sys.exit(1)
        return resp["result"]


def check(label, passed, detail=""):
    print(f"  [{'ok' if passed else 'FAIL'}] {label}{f' -- {detail}' if detail else ''}")
    if not passed:
        failures.append(label)


print("ping:", call("ping")["blender"])

PRESETS = ["wood_grain", "bark", "stone", "mossy", "grass_blend", "worn_metal",
           "fabric", "plaster", "concrete", "brushed_metal", "leather", "soil"]
BASE = {"wood_grain": [0.5, 0.33, 0.18], "bark": [0.35, 0.24, 0.15],
        "stone": [0.55, 0.55, 0.57], "mossy": [0.3, 0.42, 0.2],
        "grass_blend": [0.32, 0.45, 0.2], "worn_metal": [0.5, 0.5, 0.52],
        "fabric": [0.45, 0.45, 0.48], "plaster": [0.85, 0.84, 0.82],
        "concrete": [0.55, 0.55, 0.56], "brushed_metal": [0.55, 0.56, 0.58],
        "leather": [0.4, 0.26, 0.15], "soil": [0.3, 0.22, 0.13]}

for preset in PRESETS:
    r = call("build", {"recipe": {"node": {
        "builder": "box", "size": [1, 1, 1],
        "material": {"name": f"tex_{preset}", "color": BASE[preset],
                     "roughness": 0.7, "texture": preset, "tex_seed": 4}}},
        "name": preset, "join": True})
    check(f"{preset} builds", r["triangles"] > 0, f"tris={r['triangles']}")
    insp = call("inspect", {})
    check(f"{preset} material recorded", f"tex_{preset}" in insp["materials"],
          f"{insp['materials']}")
    probe = call("probe_material_nodes", {"material": f"tex_{preset}"})
    kinds = probe["node_types"]
    has_tex = any(k.startswith("ShaderNodeTex") for k in kinds)
    check(f"{preset} carries a procedural texture node", has_tex, f"{kinds}")
    check(f"{preset} keeps Principled + Output", "ShaderNodeBsdfPrincipled" in kinds
          and "ShaderNodeOutputMaterial" in kinds, f"{kinds}")
    rv = call("validate", {"triangle_budget": 8000})
    check(f"{preset} validates", rv["pass"], f"tris={rv['triangles']}")

# unknown preset falls back to flat, build survives
r = call("build", {"recipe": {"node": {
    "builder": "box", "size": [1, 1, 1],
    "material": {"name": "tex_bogus", "color": [0.5, 0.5, 0.5],
                 "texture": "dragon_scales"}}}, "name": "bogus", "join": True})
check("unknown preset falls back without crashing", r["triangles"] > 0,
      f"tris={r['triangles']}")

# set_material path upgrades an existing asset in place
call("build", {"recipe": {"node": {"builder": "box", "size": [1, 1, 1],
                                   "material": {"name": "plain", "color": [0.5, 0.5, 0.5]}}},
               "name": "plain", "join": True})
call("set_material", {"name": "plain", "color": [0.5, 0.5, 0.5], "texture": "stone"})
probe = call("probe_material_nodes", {"material": "plain"})
check("set_material applies a procedural preset",
      any(k.startswith("ShaderNodeTex") for k in probe["node_types"]),
      f"{probe['node_types']}")

# determinism: same spec + seed built twice -> same node count
call("build", {"recipe": {"node": {
    "builder": "box", "size": [1, 1, 1],
    "material": {"name": "tex_stone", "color": BASE["stone"],
                 "roughness": 0.7, "texture": "stone", "tex_seed": 4}}},
    "name": "stone", "join": True})
n1 = call("probe_material_nodes", {"material": "tex_stone"})["node_count"]
call("build", {"recipe": {"node": {
    "builder": "box", "size": [1, 1, 1],
    "material": {"name": "tex_stone", "color": BASE["stone"],
                 "roughness": 0.7, "texture": "stone", "tex_seed": 4}}},
    "name": "stone", "join": True})
n2 = call("probe_material_nodes", {"material": "tex_stone"})["node_count"]
check("seeded rebuild is deterministic", n1 == n2 and n1 > 0, f"{n1} -> {n2}")

r = call("render_views", {"views": ["front_34"], "resolution": 256,
                          "dir": "output/texture/renders"})
p = r["renders"]["front_34"]
check("textured asset renders", os.path.isfile(p) and os.path.getsize(p) > 0, p)

print("TEXTURE TEST " + ("OK" if not failures else f"FAILED ({len(failures)}): {failures}"))
proc.terminate()
sys.exit(1 if failures else 0)
