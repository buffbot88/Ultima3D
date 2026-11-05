"""Bake-export: finalized GLBs must carry their baked PBR textures.

Regression cover for the white-export defect: procedural materials
(wood_grain, stone, ...) render correctly in Blender but the glTF exporter
cannot see through linked node chains, so every material exported with a
default white base color. finalize must re-point the export copies at the
baked albedo / roughness / normal PNGs, and the GLB must embed them.

Also covers the `text` signage builder end to end: extruded lettering must
survive build, validate, and export as geometry.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
WORKER = os.path.join(os.path.dirname(__file__), "..", "blender", "worker.py")

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


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

out_dir = tempfile.mkdtemp(prefix="ultima3d-bake-export-")
built = call("build", {"name": "bake_probe", "join": True, "recipe": {
    "plank": {"builder": "box", "location": [0, 0, 0.5], "size": [1, 1, 1],
              "material": {"name": "wood_probe", "texture": "wood_grain"}},
    "block": {"builder": "box", "location": [1.6, 0, 0.5], "size": [1, 1, 1],
              "material": {"name": "stone_probe", "texture": "stone"}},
    "letters": {"builder": "text", "text": "POST OFFICE", "max_width": 3,
                "location": [0, 1.2, 1.1],
                "material": {"name": "gold_probe", "color": [0.95, 0.75, 0.25],
                             "metallic": 0.3, "roughness": 0.5}},
}})
check("build succeeds", "error" not in built, str(built)[:120])
check("text node registered",
      "letters" in (built.get("manifest", {}).get("objects", {}) if "error" not in built else {}))

validated = call("validate", {"triangle_budget": 8000})
check("validate succeeds", "error" not in validated, str(validated)[:120])

final = call("finalize", {"name": "bake_probe", "triangle_budget": 8000,
                          "texture_resolution": 128, "lods": None,
                          "channels": ["albedo", "normal", "roughness"],
                          "dir": out_dir})
check("finalize succeeds", "error" not in final, str(final)[:200])
check("baked materials wired", bool(final.get("baked_materials")),
      str(final.get("baked_materials")))
for ch in ("albedo", "normal", "roughness"):
    p = (final.get("textures", {}) or {}).get(ch)
    check(f"baked {ch} png on disk", p and os.path.isfile(p), str(p))

main = (final.get("files", {}) or {}).get("main")
check("main glb written", main and os.path.isfile(main), str(main))

if main and os.path.isfile(main):
    with open(main, "rb") as f:
        data = f.read()
    clen, _ctype = struct.unpack("<II", data[12:20])
    glb = json.loads(data[20:20 + clen])
    images = glb.get("images", [])
    check("glb embeds baked images", len(images) >= 3,
          f"{len(images)} images")
    tex_sources = {t.get("source") for t in glb.get("textures", [])}
    check("glb textures reference embedded images",
          bool(tex_sources) and tex_sources <= set(range(len(images))),
          f"sources={sorted(s for s in tex_sources if s is not None)}")
    for m in glb.get("materials", []):
        pbr = m.get("pbrMetallicRoughness", {})
        factor = [round(v, 3) for v in pbr.get("baseColorFactor", [1, 1, 1, 1])]
        has_tex = "baseColorTexture" in pbr
        # Baked materials carry albedo in the texture; a white factor with no
        # texture is exactly the old defect.
        check(f"material {m.get('name')} not white-untextured",
              has_tex or factor != [1, 1, 1, 1],
              f"factor={factor} textured={has_tex}")
    meshes = glb.get("meshes", [])
    prims = sum(len(m.get("primitives", [])) for m in meshes)
    check("lettering exported as geometry", prims >= 3, f"{prims} primitives")

proc.terminate()

print()
if failures:
    print(f"BAKE EXPORT TEST FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("BAKE EXPORT TEST OK")
