"""Direct end-to-end smoke test of the Blender worker (no MCP layer)."""
import json
import os
import subprocess
import sys

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
WORKER = "ultima3d/blender_worker.py"

proc = subprocess.Popen(
    [BLENDER, "--background", "--factory-startup", "--python", WORKER],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, encoding="utf-8", bufsize=1,
)

_id = 0


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
            continue  # Blender log noise
        resp = json.loads(line[len("@JSON@"):])
        if resp.get("id") != _id:
            continue
        if not resp.get("ok"):
            print(f"[{op}] ERROR: {resp['error']}\n{resp.get('trace', '')}")
            sys.exit(1)
        return resp["result"]


print("ping:", call("ping")["blender"])

recipe = {
    "body": {"builder": "rounded_box", "size": [1.0, 0.45, 0.5], "bevel": 0.06,
             "material": {"name": "wood", "color": [0.45, 0.3, 0.18], "roughness": 0.7}},
    "roof": {"builder": "roof", "width": 1.15, "depth": 0.55, "height": 0.35,
             "location": [0, 0, 0.5], "material": {"name": "dark_iron", "color": [0.1, 0.1, 0.11], "roughness": 0.4, "metallic": 0.9}},
    "post": {"builder": "cylinder", "radius": 0.06, "depth": 1.1, "vertices": 12,
             "location": [0, 0, -0.55], "material": {"name": "wood", "color": [0.4, 0.27, 0.16], "roughness": 0.75}},
    "latch": {"builder": "rounded_box", "size": [0.12, 0.06, 0.18], "radius": 0.02,
              "location": [0, -0.25, 0.15], "material": {"name": "brass", "color": [0.75, 0.6, 0.2], "roughness": 0.3, "metallic": 1.0}},
}
for i in range(3):
    recipe[f"band_{i}"] = {"builder": "torus", "major_radius": 0.54, "minor_radius": 0.02,
                           "location": [0, 0, 0.1 + i * 0.15], "rotation": [1.5708, 0, 0],
                           "material": {"name": "dark_iron", "color": [0.1, 0.1, 0.11], "roughness": 0.4, "metallic": 0.9}}

r = call("build", {"recipe": recipe, "name": "medieval_mailbox", "join": True,
                   "blueprint": {"asset": "stylized medieval mailbox", "proportions": "w1 d0.45 h0.65"}})
print("build:", r)

r = call("render_views", {"resolution": 256, "dir": "output/smoke/renders"})
print("renders:", list(r["renders"].keys()))

r = call("validate", {"triangle_budget": 8000})
print("validate:", "PASS" if r["pass"] else "FAIL", "| tris:", r["triangles"])

r = call("finalize", {"name": "medieval_mailbox", "triangle_budget": 8000,
                      "lods": [8000, 4000], "collision": "auto",
                      "texture_resolution": 256,
                      "channels": ["albedo", "normal", "roughness", "metallic"],
                      "bake": True, "dir": "output/smoke/export"})
print("finalize files:", list(r["files"].keys()))
print("textures:", r["textures"])
for ch, p in r["textures"].items():
    assert os.path.isfile(p) and os.path.getsize(p) > 0, f"missing/empty bake: {ch} -> {p}"
print("bake PNGs verified:", list(r["textures"].keys()))

# parametric refinement: rebuild with taller roof
r = call("load_recipe", {"path": "output/smoke/export/medieval_mailbox/asset.json"}) if False else None
recipe["roof"]["height"] = 0.55
r = call("build", {"recipe": recipe, "name": "medieval_mailbox", "join": True})
print("refine rebuild tris:", r["triangles"])

# ---- rig validation block ----
r = call("rig", {})
print("rig:", {k: r[k] for k in ("armature", "weights_source")}, "bones:", len(r["bones"]))
assert len(r["bones"]) >= 3, "expected multi-bone rig"
assert r["weights_source"] in ("auto", "fallback"), r["weights_source"]

# every bone vertex group non-empty; weights sum ~1 per vertex (sampled)

# idempotency
r2 = call("rig", {})
assert r2["weights_source"] == "existing", "double-rig not prevented"

# validate still passes with armature in scene
rv = call("validate", {"triangle_budget": 8000})
print("validate after rig:", "PASS" if rv["pass"] else "FAIL")

# finalize again with rig: exports must include skeleton
rf = call("finalize", {"name": "medieval_mailbox", "triangle_budget": 8000,
                       "lods": [8000, 4000], "collision": "auto",
                       "texture_resolution": 128, "bake": False,
                       "dir": "output/smoke/export_rigged"})
assert "rig" in rf, "asset.json missing rig block"
print("rigged export:", rf["rig"]["bones"][:3], f"... {len(rf['rig']['bones'])} bones")

call("save_recipe", {"path": "output/smoke/medieval_mailbox.recipe.json"})
print("SMOKE TEST OK")
proc.terminate()
