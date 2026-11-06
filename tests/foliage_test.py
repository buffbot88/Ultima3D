"""Regression tests for the two things the v3 work added.

1. `color_srgb` conversion. The original bug: recipe colours were display sRGB
   values poured into Blender's *linear* Base Color socket, so every green baked
   ~1.6x too light (leaf_mid authored 0.25/0.48/0.20 baked as 137,184,124).
   These tests bake a known colour and read the atlas texel back, so they cover
   the whole chain (socket -> bake -> PNG) rather than just the maths.
2. `foliage_cluster`: determinism, the foliage validate checks, and leaf contact.
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

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
            return {"error": resp["error"]}
        return resp["result"]


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def srgb_encode(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def dominant_colour(path):
    from PIL import Image
    from collections import Counter
    im = Image.open(path).convert("RGB")
    counts = Counter(im.getdata())
    for col, _n in counts.most_common(20):
        if col != (0, 0, 0):          # atlas background is unpainted black
            return col
    return (0, 0, 0)


def baked_colour(name, material, out_dir):
    built = call("build", {"recipe": {"body": {"builder": "box", "size": [1, 1, 1],
                                              "material": material}},
                           "name": name, "join": True})
    if "error" in built:
        return None, built["error"]
    finished = call("finalize", {"name": name, "triangle_budget": 5000,
                                 "collision": "none", "bake": True,
                                 "texture_resolution": 64, "channels": ["albedo"],
                                 "dir": out_dir})
    if "error" in finished:
        return None, finished["error"]
    png = finished.get("textures", {}).get("albedo")
    if not png or not os.path.exists(png):
        png = os.path.join(out_dir, f"{name}_albedo.png")
    return dominant_colour(png), None


print("ping:", call("ping")["blender"])
tmp = tempfile.mkdtemp(prefix="ultima3d_foliage_")

# ---- colour contract -------------------------------------------------------
want = tuple(round(srgb_encode(c) * 255) for c in (0.25, 0.48, 0.20))
got, err = baked_colour("legacy_colour", {"name": "legacy",
                                          "color": [0.25, 0.48, 0.20]}, tmp)
check("color keeps linear meaning", err is None and got is not None and
      all(abs(a - b) <= 3 for a, b in zip(got, want)),
      f"baked {got}, expected ~{want}, err={err}")

got, err = baked_colour("srgb_colour", {"name": "srgb", "color_srgb": [64, 122, 51]}, tmp)
check("color_srgb bakes the authored colour", err is None and got is not None and
      all(abs(a - b) <= 3 for a, b in zip(got, (64, 122, 51))),
      f"baked {got}, expected ~(64, 122, 51), err={err}")

got, err = baked_colour("scale_colour", {"name": "recip",
                                         "color_srgb": [0.25, 0.48, 0.20]}, tmp)
check("color_srgb accepts 0..1 floats", err is None and got is not None and
      all(abs(a - b) <= 3 for a, b in zip(got, (64, 122, 51))),
      f"baked {got}, expected ~(64, 122, 51)")

bad = call("build", {"recipe": {"b": {"builder": "box", "size": [1, 1, 1],
                                      "material": {"name": "both",
                                                   "color": [0.2, 0.2, 0.2],
                                                   "color_srgb": [50, 50, 50]}}},
                     "name": "both_colour"})
check("rejects color and color_srgb together", "error" in bad,
      str(bad.get("error", ""))[:90])

bad = call("build", {"recipe": {"b": {"builder": "box", "size": [1, 1, 1],
                                      "material": {"name": "range",
                                                   "color_srgb": [300, 20, 20]}}},
                     "name": "bad_colour"})
check("rejects out-of-range color_srgb", "error" in bad,
      str(bad.get("error", ""))[:90])

# ---- foliage builder -------------------------------------------------------
CANOPY = {"builder": "foliage_cluster", "center": [0, 0, 1.7], "radii": [1.3, 1.2, 1.0],
          "seed": 5, "cluster_count": 8, "cluster_size": [0.40, 0.60], "subdiv": 0,
          "leaf": {"count_per_cluster": 2, "leaves_per_rosette": 4, "length": 0.22,
                   "width": 0.13, "thickness": 0.0},
          "skirt": {"enabled": False}}
RECIPE = {"mound": {"builder": "sphere", "radius": 0.5, "segments": 10,
                    "location": [0, 0, 0.05],
                    "material": {"name": "moss", "color_srgb": [74, 92, 52]}},
          "canopy": dict(CANOPY, material=None)}

first = call("build", {"recipe": RECIPE, "name": "foliage_probe", "join": True})
check("foliage_cluster builds one object", "error" not in first and
      first.get("object_count") == 1,
      f"objects={first.get('object_count')}, tris={first.get('triangles')}, "
      f"err={str(first.get('error', ''))[:80]}")

second = call("build", {"recipe": RECIPE, "name": "foliage_probe", "join": True})
check("foliage_cluster is deterministic",
      first.get("triangles") == second.get("triangles") and
      first.get("triangles", 0) > 0,
      f"{first.get('triangles')} vs {second.get('triangles')} tris")

valid = call("validate", {"triangle_budget": 5000})
names = {c["check"] for c in valid.get("checks", [])}
check("validate passes with a canopy", valid.get("pass") is True,
      ", ".join(f"{c['check']}={c['pass']}" for c in valid.get("checks", [])
                if not c["pass"])[:140])
check("foliage checks run", any(n.startswith("foliage_leaf_contact") for n in names) and
      any(n.startswith("foliage_silhouette") for n in names), str(sorted(names)))

stats = (valid.get("foliage") or [{}])[0]
check("leaves are attached, not floating", float(stats.get("leaf_float_max", 9)) <= 0.02,
      f"leaf_float_max={stats.get('leaf_float_max')}, leaves={stats.get('leaves')}")

# ---- material merge --------------------------------------------------------
merged = call("finalize", {"name": "foliage_probe", "triangle_budget": 5000,
                           "collision": "none", "bake": True, "texture_resolution": 64,
                           "channels": ["albedo"], "dir": tmp})
# The response lists the *pre-merge* baked material names, so the authoritative
# check is the exported GLB itself: one atlas + one material per mesh.
candidates = sorted(glob.glob(os.path.join(tmp, "foliage_probe*.glb")))
glb = candidates[0] if candidates else None
glb_mats = None
if glb and os.path.exists(glb):
    with open(glb, "rb") as fh:
        blob = fh.read()
    length = int.from_bytes(blob[12:16], "little")
    doc = json.loads(blob[20:20 + length])
    prims = sum(len(m["primitives"]) for m in doc.get("meshes", []))
    glb_mats = (len(doc.get("materials", [])), prims, len(doc.get("images", [])))
check("shared atlas merges slots into one primitive",
      glb_mats is not None and glb_mats[0] == 1 and glb_mats[1] == 1 and glb_mats[2] == 1,
      f"materials/primitives/images = {glb_mats}")

proc.stdin.close()
proc.wait(timeout=60)
print()
if failures:
    print(f"FAILED ({len(failures)}): {', '.join(failures)}")
    sys.exit(1)
print("foliage + colour tests PASSED")
