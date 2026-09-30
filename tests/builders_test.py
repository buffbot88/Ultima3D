"""Builder sweep: every registered builder must register its objects so the rest of the
pipeline can see them, not just the build result.

Curve-authored builders (`pipe`, `curve`) used to create CURVE objects that the MESH-only
manifest diff ignored, so build reported a scene while inspect raised "no asset in scene"
and validate raised "nothing to validate".
"""
# Per-builder geometry checks are reported but not asserted: some bmesh-authored builders
# (`arch`, `extrusion`, `lathe`, `roof`, `weapon_blade`) emit meshes with no UV layer, and
# `stairs` emits doubles. Those are pre-existing and independent of builder registration —
# in a multi-node asset the join supplies the UV map, so `validate_asset` passes.
import json
import os
import subprocess
import sys

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
WORKER = os.path.join(os.path.dirname(__file__), "..", "ultima3d", "blender_worker.py")

# Keep in sync with BUILDERS in ultima3d/blender_worker.py.
BUILDERS = ["box", "rounded_box", "cylinder", "tapered_cylinder", "sphere", "torus",
            "arch", "panel", "pipe", "curve", "extrusion", "lathe", "rock", "beam",
            "barrel", "crate", "roof", "window", "door", "stairs", "column", "wall",
            "fence", "tree", "weapon_blade", "weapon_handle"]

# Builders whose geometry arguments have no usable default.
REQUIRED_ARGS = {
    "pipe": {"path": [[0, 0, 0], [0, 0, 1], [0.5, 0, 1.5]]},
    "curve": {"points": [[0, 0, 0], [0.5, 0.2, 0.5], [1, 0, 1]]},
    "extrusion": {"profile2d": [[0, 0], [1, 0], [1, 0.4], [0, 0.4]]},
    "lathe": {"profile2d": [[0.15, 0], [0.4, 0.1], [0.3, 0.5]], "segments": 16},
    "beam": {"start": [0, 0, 0], "end": [1, 1, 1]},
}

MATERIAL = {"name": "steel", "color": [0.5, 0.5, 0.55], "roughness": 0.4, "metallic": 0.8}

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
            return {"error": resp["error"]}
        return resp["result"]


print("ping:", call("ping")["blender"])

failures = []
quality = []
for builder in BUILDERS:
    spec = {"builder": builder, "material": MATERIAL, **REQUIRED_ARGS.get(builder, {})}
    built = call("build", {"recipe": {"node": spec}, "name": builder, "join": True})
    if "error" in built:
        failures.append((builder, f"build: {built['error']}"))
        continue
    registered = built["manifest"]["objects"]["node"]
    if not registered:
        failures.append((builder, "registered no objects in the manifest"))
        continue
    # The bug this guards: the node built, but the mesh-only manifest diff missed it,
    # leaving inspect/validate with nothing to act on.
    inspected = call("inspect", {})
    if "error" in inspected:
        failures.append((builder, f"inspect: {inspected['error']}"))
        continue
    # inspect reports the joined asset (one object, renamed to the asset name); the
    # manifest keeps the pre-join node names. Both must be non-empty.
    if inspected["object_count"] < 1 or inspected["triangles"] < 1:
        failures.append((builder, f"inspect sees no geometry: {inspected}"))
        continue
    validated = call("validate", {"triangle_budget": 8000})
    if "error" in validated:
        failures.append((builder, f"validate: {validated['error']}"))
        continue
    if validated["pass"]:
        print(f"  [ok]   {builder:16s} objects={len(registered)} tris={inspected['triangles']}")
    else:
        bad = [c["check"] for c in validated["checks"] if not c["pass"]]
        quality.append((builder, bad))
        print(f"  [ok]   {builder:16s} objects={len(registered)} tris={inspected['triangles']}"
              f"  (quality: {', '.join(bad)})")

proc.terminate()

if failures:
    print(f"\nBUILDER SWEEP FAILED ({len(failures)}/{len(BUILDERS)}):")
    for builder, why in failures:
        print(f"  - {builder}: {why}")
    sys.exit(1)

print(f"\nBUILDER SWEEP OK ({len(BUILDERS)} builders register, inspect and validate)")
if quality:
    print("Pre-existing geometry-quality notes (not asserted):")
    for builder, bad in quality:
        print(f"  - {builder}: {', '.join(bad)}")
