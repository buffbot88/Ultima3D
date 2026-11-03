"""Vista test: composed environment scene (terrain + scattered assets + sky).

Direct worker test (no MCP layer), following tests/smoke_test.py. A vista is
an ordinary recipe: `terrain` meshes seeded value-noise ground, `scatter`
stamps an earlier node across it (same height function, so instances sit on
the ground by construction), and `skydome` is scene backdrop excluded from
the asset pipeline (inspect/validate/finalize never see it).
"""
import json
import os
import subprocess
import sys

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
WORKER = "blender/worker.py"

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
            continue  # Blender log noise
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

recipe = {
    "ground": {"builder": "terrain", "size": [24, 24], "segments": 32,
               "height": 1.6, "seed": 7,
               "material": {"name": "grass", "color": [0.3, 0.45, 0.2], "roughness": 0.9}},
    "oak": {"builder": "tree", "height": 3.0,
            "material": {"name": "leaf", "color": [0.25, 0.4, 0.18], "roughness": 0.85}},
    "grove": {"builder": "scatter", "source": "oak", "terrain": "ground",
              "count": 6, "seed": 3},
    "stone": {"builder": "rock", "size": [0.5, 0.5, 0.4], "detail": 0, "seed": 11,
              "material": {"name": "granite", "color": [0.5, 0.5, 0.52], "roughness": 0.85}},
    "scree": {"builder": "scatter", "source": "stone", "terrain": "ground",
              "count": 8, "seed": 5, "scale_min": 0.6, "scale_max": 1.4},
    "sky": {"builder": "skydome", "radius": 120},
}

r = call("build", {"recipe": recipe, "name": "meadow_vista", "join": False})
print("build triangles:", r["triangles"], "| nodes:", sorted(r["manifest"]["objects"].keys()))
objs = r["manifest"]["objects"]
check("terrain node built", "ground" in objs and len(objs["ground"]) == 1, f"{objs.get('ground')}")
check("tree node built (trunk + 3 foliage)", "oak" in objs and len(objs["oak"]) == 4,
      f"{objs.get('oak')}")
check("grove stamped 6 trees x 4 objects", "grove" in objs and len(objs["grove"]) == 24,
      f"count={len(objs.get('grove', []))}")
check("scree stamped 8 rocks", "scree" in objs and len(objs.get("scree", [])) == 8,
      f"count={len(objs.get('scree', []))}")
check("skydome recorded with no asset objects (scene backdrop)",
      "sky" in objs and objs["sky"] == [], f"{objs.get('sky')}")

insp = call("inspect", {})
names = insp["objects"]
check("skydome excluded from the asset (renders only)", not any("sky" in n for n in names),
      f"objects={len(names)}")
# join=False leaves per-node offsets, so check the ground snap on a joined build
call("build", {"recipe": recipe, "name": "meadow_vista", "join": True})
insp_joined = call("inspect", {})
check("vista sits on the ground", abs(insp_joined["bounds_min"][2]) < 1e-4,
      f"min_z={insp_joined['bounds_min'][2]}")
rv = call("validate", {"triangle_budget": 40000})
check("vista validates", rv["pass"], f"tris={rv['triangles']}")
if not rv["pass"]:
    for c in rv["checks"]:
        if not c["pass"]:
            print("    failed check:", c)

# determinism: same seed -> same layout -> same triangle count
tris_first = r["triangles"]
r2 = call("build", {"recipe": recipe, "name": "meadow_vista", "join": False})
check("seeded rebuild is deterministic", r2["triangles"] == tris_first,
      f"{tris_first} -> {r2['triangles']}")

# scatter of an unknown node fails loudly, not silently empty. (An op error
# leaves the worker alive; call() exits the test on error, so catch it here.)
call("build", {"recipe": {"ground": recipe["ground"]}, "name": "probe", "join": False})
try:
    call("build", {"recipe": {"ground": recipe["ground"],
                              "oops": {"builder": "scatter", "source": "missing"}},
                   "name": "probe", "join": False})
    check("scatter of an unknown node fails loudly", False, "no error raised")
except SystemExit:
    check("scatter of an unknown node fails loudly", True, "")
    call("build", {"recipe": recipe, "name": "meadow_vista", "join": False})

r = call("render_views", {"views": ["front_34", "top"], "resolution": 256,
                          "dir": "output/vista/renders"})
rendered = [v for v, p in r["renders"].items() if os.path.isfile(p) and os.path.getsize(p) > 0]
check("vista renders (sky visible behind geometry)", sorted(rendered) == ["front_34", "top"],
      f"{rendered}")

print("VISTA TEST " + ("OK" if not failures else f"FAILED ({len(failures)}): {failures}"))
proc.terminate()
sys.exit(1 if failures else 0)
