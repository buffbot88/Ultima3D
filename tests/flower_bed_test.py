"""Focused checks for the `flower_bed` builder.

The bed is the planted interior of the Clover Village garden: it has to fill the
kerb, stay inside the authored radius (or foliage pokes through the stone) and
keep the soil dome under the cap. These build it the way a recipe does and assert
on the numbers the game will render and collide against.

    python tests/flower_bed_test.py
"""
import json
import os
import subprocess
import sys

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
WORKER = os.path.join(os.path.dirname(__file__), "..", "blender", "worker.py")

# Clover Village: the kerb's inner face is r 2.71, so the soil edge sits under it.
BASE = {
    "builder": "flower_bed", "radius": 2.66, "soil_height": 0.245, "seed": 7,
    "groundcover": 80, "clovers": 26, "flower_masses": 9, "flowers_per_mass": 26,
    "flower_size": 0.115, "accents": 6,
}

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
            return {"error": resp["error"]}
        return resp["result"]


def build(spec):
    return call("build", {"recipe": {"bed": spec}, "name": "bed", "join": True})


failures = []


def expect(label, got, want, tol=0.0):
    ok = abs(got - want) <= tol if tol else got == want
    if not ok:
        failures.append(f"{label}: got {got!r}, want {want!r} (±{tol})")
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: {got} (want {want} ±{tol})")


print("base bed")
built = build(BASE)
if "error" in built:
    print("build failed:", built["error"])
    sys.exit(1)
info = call("inspect", {})
expect("triangles in range", 3000 <= info["triangles"] <= 14000, True)
expect("diameter", info["dimensions"][0], 2 * BASE["radius"], 0.05)
expect("height above soil", info["dimensions"][2] > BASE["soil_height"], True)
expect("material slots", len(info["materials"]), 11)
expect("one object", info.get("object_count", 1), 1)
print(f"  triangles: {info['triangles']}, bounds: {[round(v, 3) for v in info['dimensions']]}")

validated = call("validate", {"triangle_budget": 14000})
failed = [c["check"] for c in validated["checks"] if not c["pass"]]
if failed:
    failures.append(f"base bed validate failed: {failed}")
print(f"  [{'ok' if not failed else 'FAIL'}] validate checks: {[c['check'] for c in validated['checks']]}")
inside = next((c for c in validated["checks"] if c["check"].startswith("flower_bed_inside")), None)
if inside is None:
    failures.append("no flower_bed_inside check was reported")
elif not inside["pass"]:
    failures.append(f"planting grows past the bed radius: {inside['detail']}")
print(f"  reach detail: {inside['detail'] if inside else 'missing'}")

print("determinism")
again = build(BASE)
again_info = call("inspect", {})
expect("same triangles", again_info["triangles"], info["triangles"])
expect("same bounds", again_info["bounds_max"], info["bounds_max"])

print("density")
dense = build({**BASE, "groundcover": 20, "flower_masses": 2, "flowers_per_mass": 4,
               "clovers": 2, "accents": 0})
if "error" in dense:
    failures.append(f"sparse bed failed to build: {dense['error']}")
else:
    sparse_info = call("inspect", {})
    expect("sparser bed has fewer tris", sparse_info["triangles"] < info["triangles"], True)

print("short palette")
short = build({**BASE, "palette": [
    {"name": "s", "color_srgb": [90, 70, 50]},
    {"name": "l1", "color_srgb": [50, 80, 45]},
    {"name": "l2", "color_srgb": [70, 110, 60]},
    {"name": "l3", "color_srgb": [100, 150, 80]},
    {"name": "b1", "color_srgb": [240, 200, 90]},
]})
if "error" in short:
    failures.append(f"5-slot palette rejected: {short['error']}")
else:
    expect("5-slot palette materials", len(call("inspect", {})["materials"]), 5)

print("bad palette")
bad = build({**BASE, "palette": [{"name": "s", "color_srgb": [90, 70, 50]}]})
if "error" not in bad:
    failures.append("a single-slot palette was accepted")
print(f"  [{'ok' if 'error' in bad else 'FAIL'}] one-slot palette rejected: "
      f"{bad.get('error', 'accepted')!r}")

proc.terminate()

if failures:
    print(f"\nFLOWER BED TESTS FAILED ({len(failures)}):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("\nFLOWER BED TESTS OK")
