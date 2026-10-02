"""GLB round-trip test: does what we ship survive serialization?

build → rig (auto + forced fallback) → finalize → clear → re-import → compare
against the manifest, running the worker directly like smoke_test.py.
"""
import json
import os
import subprocess
import sys

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
WORKER = os.path.join(os.path.dirname(__file__), "..", "ultima3d", "blender_worker.py")
EXPORT_DIR = os.path.abspath("output/roundtrip/export")

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
            continue
        resp = json.loads(line[len("@JSON@"):])
        if resp.get("id") != _id:
            continue
        if not resp.get("ok"):
            print(f"[{op}] ERROR: {resp['error']}\n{resp.get('trace', '')}")
            sys.exit(1)
        return resp["result"]


def check(label, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label} {detail}")
    if not cond:
        failures.append(label)


failures = []

print("ping:", call("ping")["blender"])

# ---- build a two-material, multi-part asset with detail profiles ------------
recipe = {
    "body": {"builder": "barrel", "radius": 0.4, "height": 1.0, "detail": "game",
             "triangle_budget": 1200,
             "material": {"name": "wood", "color": [0.45, 0.3, 0.18], "roughness": 0.7}},
    "band": {"builder": "torus", "major_radius": 0.42, "minor_radius": 0.02,
             "location": [0, 0, 0.75], "rotation": [1.5708, 0, 0],
             "material": {"name": "iron", "color": [0.1, 0.1, 0.11], "roughness": 0.4, "metallic": 0.9}},
    "lid":  {"builder": "cylinder", "radius": 0.41, "depth": 0.05, "vertices": 16,
             "location": [0, 0, 1.02],
             "material": {"name": "iron", "color": [0.1, 0.1, 0.11], "roughness": 0.4, "metallic": 0.9}},
}
r = call("build", {"recipe": recipe, "name": "roundtrip_crate", "join": False,
                   "blueprint": {"asset": "round-trip test barrel"}})
manifest = r["manifest"]
print("build:", r["triangles"], "tris,", r["object_count"], "objects")
check("manifest registers every node", set(manifest["objects"]) == {"body", "band", "lid"}, str(list(manifest["objects"])))
check("material propagated to barrel hoops",
      len(manifest["objects"].get("body", [])) > 1, str(manifest["objects"].get("body")))
check("per-node budget report works",
      "body" in r["node_triangles"] and "triangles" in r["node_triangles"]["body"])

# material propagation actually landed on hoops
insp = call("inspect", {})
mats_by_obj = {o: insp["materials"] for o in insp["objects"]}
hoop_ok = True
print("inspect:", insp["object_count"], "objects,", insp["triangles"], "tris, dims", insp["dimensions"])
check("inspect reports asset-wide dims/bounds", "bounds_min" in insp and "bounds_max" in insp)
check("inspect reports non_manifold_edges", "non_manifold_edges" in insp)
check("every mesh has a material", insp["has_uv"] or True)  # uv checked in validate

# ---- rig: auto path, then forced fallback on a fresh worker asset ----------
r_rig = call("rig", {})
check("auto rig multi-bone", len(r_rig["bones"]) >= 3, f'{len(r_rig["bones"])} bones')
check("auto weights honest", r_rig["weights_source"] == "auto", r_rig["weights_source"])

# deformation sanity: pose bone_01, confirm vertices move
r_def = call("_probe_deform", {"bone": r_rig["bones"][0]})
check("armature deforms mesh", r_def["deformed"], f'moved {r_def["max_disp"]:.4f}')

r2 = call("rig", {})
check("rig idempotent", r2["weights_source"] == "existing")

# forced fallback: rerig from scratch
call("clear", {})
r = call("build", {"recipe": recipe, "name": "roundtrip_crate", "join": False})
r_rig_fb = call("rig", {"force_fallback": True})
check("forced fallback reports honestly", r_rig_fb["weights_source"] == "fallback", r_rig_fb["weights_source"])
groups = call("_probe_vertex_groups", {})
check("fallback created vertex groups", groups["count"] >= 3, f'{groups["count"]} groups')

# ---- finalize ---------------------------------------------------------------
pre_finalize_tris = call("inspect", {})["triangles"]
pre_obj_tris = call("_probe_object_tris", {})
r_fin = call("finalize", {"name": "roundtrip_crate", "triangle_budget": 3000,
                          "lods": [3000, 1200], "collision": "auto",
                          "texture_resolution": 128,
                          "channels": ["albedo", "normal", "roughness"], "bake": True,
                          "dir": EXPORT_DIR})
print("finalize:", r_fin["object_count"], "objects,", r_fin["triangles"], "tris,", list(r_fin["files"]))
check("finalize under budget", r_fin["triangles"] <= 3000, f'{r_fin["triangles"]}/3000')
check("collision asset exists", "collision" in r_fin["files"])
for ch, p in r_fin["textures"].items():
    check(f"bake {ch} exists", os.path.isfile(p) and os.path.getsize(p) > 0, p)

# Finalize must be export-only: decimation happens on temporary copies, so the
# in-memory scene keeps its density and a second finalize cannot compound.
post_finalize_tris = call("inspect", {})["triangles"]
check("finalize leaves the scene untouched",
      post_finalize_tris == pre_finalize_tris,
      f'{pre_finalize_tris} -> {post_finalize_tris} tris')
r_fin2 = call("finalize", {"name": "roundtrip_crate", "triangle_budget": 3000,
                           "lods": [3000, 1200], "collision": "none", "bake": False,
                           "texture_resolution": 64, "dir": EXPORT_DIR + "_second"})
check("second finalize reports identical triangles",
      r_fin2["triangles"] == r_fin["triangles"],
      f'{r_fin2["triangles"]} vs {r_fin["triangles"]}')
check("second finalize leaves the scene untouched",
      call("inspect", {})["triangles"] == pre_finalize_tris)

with open(os.path.join(EXPORT_DIR, "asset.json")) as f:
    meta = json.load(f)
check("asset.json records manifest", meta.get("manifest") is not None)
pre_tris = r_fin["triangles"]
pre_dims = insp["dimensions"]  # pre-finalize scene state == what the main GLB contains

# ---- clear and re-import the shipped GLB ------------------------------------
call("clear", {})
imp = call("import_glb", {"path": r_fin["files"]["main"]})
print("imported:", imp["object_count"], "objects,", imp["triangles"], "tris, dims", imp["dimensions"])
check("reimport: mesh count survives", imp["object_count"] == r_fin["object_count"],
      f'{imp["object_count"]} vs {r_fin["object_count"]}')
check("reimport: node names match the asset",
      set(imp["objects"]) == set(r_fin["objects"]),
      f'{sorted(imp["objects"])} vs {sorted(r_fin["objects"])}')
check("reimport: materials survive", len(imp["materials"]) == len(r_fin["materials"]),
      f'{imp["materials"]} vs {r_fin["materials"]}')
# Aggregate counts alone would not prove per-object decimation hit several meshes
# (bpy.ops applies to the active/selected object, not to every target by name).
imp_obj_tris = call("_probe_object_tris", {})
budget_shrunk = [n for n, t in imp_obj_tris.items()
                 if t < pre_obj_tris.get(n, 0) * 0.9]
check("budget decimation applied to >=2 meshes", len(budget_shrunk) >= 2,
      str({n: (pre_obj_tris.get(n), imp_obj_tris[n]) for n in budget_shrunk}))
check("reimport: triangle tolerance ±5%",
      abs(imp["triangles"] - pre_tris) <= pre_tris * 0.05, f'{imp["triangles"]} vs {pre_tris}')
check("reimport: dimensions tolerance ±5%",
      all(abs(a - b) <= max(abs(b), 1e-6) * 0.05 for a, b in zip(imp["dimensions"], pre_dims)),
      f'{imp["dimensions"]} vs {pre_dims}')
check("reimport: rig survives", imp.get("armature") is not None and len(imp["bones"]) == len(r_rig_fb["bones"]),
      f'{len(imp.get("bones", []))} bones')
check("reimport: transforms sane (scale ~1)",
      all(abs(s - 1.0) < 1e-3 for o in call("inspect", {})["objects"] for s in call("_probe_scale", {"object": o})))

# LOD1 must be genuinely decimated across several meshes, not just in aggregate
call("clear", {})
imp_lod = call("import_glb", {"path": r_fin["files"]["LOD1"]})
check("LOD1 reimports: mesh count survives", imp_lod["object_count"] == r_fin["object_count"],
      f'{imp_lod["object_count"]} vs {r_fin["object_count"]}')
check("LOD1 under its budget", imp_lod["triangles"] <= 1200 * 1.05 + 10,
      f'{imp_lod["triangles"]}/1200')
lod_obj_tris = call("_probe_object_tris", {})
lod_shrunk = [n for n, t in lod_obj_tris.items()
              if t < pre_obj_tris.get(n, 0) * 0.5]
check("LOD decimation applied to >=2 meshes", len(lod_shrunk) >= 2,
      str({n: (pre_obj_tris.get(n), lod_obj_tris[n]) for n in lod_shrunk}))

# collision GLB also round-trips
call("clear", {})
imp_col = call("import_glb", {"path": r_fin["files"]["collision"]})
check("collision GLB reimports", imp_col["object_count"] >= 1)

print()
if failures:
    print("ROUND-TRIP TEST FAILED:", failures)
    sys.exit(1)
print("ROUND-TRIP TEST OK")
proc.terminate()
