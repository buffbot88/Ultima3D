"""Focused checks for the `stone_kerb` builder.

The ring is a raised bed boundary: if a course does not close, or the footprint
drifts from the authored radius, the bed leaks soil or the plaza path silently
narrows. These build the ring the way a recipe does and assert on the numbers the
game will collide against.

    python tests/kerb_test.py
"""
import json
import os
import subprocess
import sys

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
WORKER = os.path.join(os.path.dirname(__file__), "..", "blender", "worker.py")

# The Clover Village garden bed: outer face lands on the authored r 3.0 edge.
BASE = {
    "builder": "stone_kerb", "radius": 2.855, "thickness": 0.22, "height": 0.3,
    "segments": 34, "seed": 11, "gaps": 0, "cap": True, "cap_height": 0.07,
    "cap_overhang": 0.035, "jitter": 0.05,
}
TRI_PER_BLOCK = 12

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
    return call("build", {"recipe": {"kerb": spec}, "name": "kerb", "join": True})


failures = []


def expect(label, got, want, tol=0.0):
    ok = abs(got - want) <= tol if tol else got == want
    if not ok:
        failures.append(f"{label}: got {got!r}, want {want!r} (±{tol})")
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: {got} (want {want} ±{tol})")


print("base ring")
built = build(BASE)
if "error" in built:
    print("build failed:", built["error"])
    sys.exit(1)
info = call("inspect", {})
# 34 blocks a course, two courses, one course offset half a block.
expect("blocks", info["triangles"] // TRI_PER_BLOCK, 68)
expect("triangles", info["triangles"], 68 * TRI_PER_BLOCK)
expect("outer diameter", info["dimensions"][0], 6.0, 0.12)
expect("height", info["dimensions"][2], 0.3, 0.02)
expect("material slots", len(info["materials"]), 2)
expect("non-manifold edges", info["non_manifold_edges"], 0)

validated = call("validate", {"triangle_budget": 3000})
failed = [c["check"] for c in validated["checks"] if not c["pass"]]
if failed:
    failures.append(f"base ring validate failed: {failed}")
print(f"  [{'ok' if not failed else 'FAIL'}] validate checks: {[c['check'] for c in validated['checks']]}")
closure = next((c for c in validated["checks"] if c["check"].startswith("kerb_continuity")), None)
if closure is None:
    failures.append("no kerb_continuity check was reported")
elif not closure["pass"]:
    failures.append(f"ring does not close: {closure['detail']}")
print(f"  closure detail: {closure['detail'] if closure else 'missing'}")

print("determinism")
again = build(BASE)
again_info = call("inspect", {})
expect("same triangles", again_info["triangles"], info["triangles"])
expect("same bounds", again_info["bounds_max"], info["bounds_max"])

print("planting gaps")
gapped = build({**BASE, "gaps": 4, "gap_deg": 20})
# A gap swallows whole blocks, so it is never narrower than asked for: 20 deg
# needs two 10.59 deg blocks a course, four gaps on two courses.
per_gap = 2
removed = 4 * per_gap * 2
expect("gapped blocks", call("inspect", {})["triangles"] // TRI_PER_BLOCK, 68 - removed)
gapped_validate = call("validate", {"triangle_budget": 3000})
gapped_closure = next((c for c in gapped_validate["checks"]
                       if c["check"].startswith("kerb_continuity")), None)
if gapped_closure is None or not gapped_closure["pass"]:
    failures.append(f"gapped ring rejected: {gapped_closure}")
print(f"  gaps detail: {gapped_closure['detail'] if gapped_closure else 'missing'}")

print("bad cap")
bad = build({**BASE, "cap_height": 0.4})
if "error" not in bad:
    failures.append("cap taller than the wall was accepted")
print(f"  [{'ok' if 'error' in bad else 'FAIL'}] cap above height rejected: "
      f"{bad.get('error', 'accepted')!r}")

proc.terminate()

if failures:
    print(f"\nKERB TESTS FAILED ({len(failures)}):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("\nKERB TESTS OK")
