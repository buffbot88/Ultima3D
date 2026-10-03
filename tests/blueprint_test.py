"""Blueprint compiler + assertion checker, driven through the real worker.

compile_blueprint is pure (no scene needed): valid blueprints compile to
build-consumable recipes, invalid ones fail loudly. check_assertions measures
against the live asset: mechanical checks compute, vision-side ones stay manual.
"""
import json
import os
import subprocess
import sys

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
WORKER = os.path.join(os.path.dirname(__file__), "..", "ultima3d", "blender_worker.py")

STEEL = {"color": [0.5, 0.5, 0.55], "roughness": 0.4, "metallic": 0.8}


def spec(builder, **kw):
    d = {"builder": builder, "material": "steel", "confidence": 0.9}
    d.update(kw)
    return d


GOOD_BP = {
    "meta": {"version": 1},
    "asset": {"name": "hut", "type": "hut"},
    "scale": {"basis": "door", "height_m": 2.1},
    "parts": [
        spec("box", node="body", params={"size": [2, 1, 1]}, center=[0, 0, 0.5]),
        spec("box", node="cap", params={"size": [1, 0.8, 0.3]}, center=[0, 0, 1.15],
             confidence=0.6),
    ],
    "relations": [
        {"subject": "cap", "predicate": "sits_on", "object": "body"},
    ],
    "ratios": {"cap_to_body": 0.3},
    "materials": {"steel": STEEL},
    "detail": {"profile": "game"},
    "assertions": [{"check": "part_count", "expected": 2}],
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


failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


print("ping:", call("ping")["blender"])

print("compile:")
r = call("compile_blueprint", {"blueprint": GOOD_BP})
check("valid blueprint compiles", "error" not in r, str(r.get("error", ""))[:100])
recipe = r.get("recipe", {})
check("recipe has both nodes", set(recipe) == {"body", "cap"}, str(sorted(recipe)))
check("builders carried through",
      recipe.get("body", {}).get("builder") == "box" and recipe.get("cap", {}).get("builder") == "box")
check("center defaults to location", recipe.get("body", {}).get("location") == [0, 0, 0.5],
      str(recipe.get("body", {}).get("location")))
check("material inlined with color",
      (recipe.get("body", {}).get("material") or {}).get("color") == STEEL["color"])
check("detail profile attached", recipe.get("body", {}).get("detail") == "game")
warns = r.get("warnings", [])
check("low-confidence part warned, not dropped",
      len(warns) == 1 and "cap" in warns[0], str(warns))

print("invalid blueprints fail loudly:")
bad = dict(GOOD_BP)
bad["parts"] = [spec("boxx", node="body", params={"size": [1, 1, 1]})]
e = call("compile_blueprint", {"blueprint": bad})
check("unknown builder errors", "error" in e and "boxx" in e["error"], str(e)[:100])

bad = json.loads(json.dumps(GOOD_BP))
bad["parts"][0]["material"] = "unobtainium"
e = call("compile_blueprint", {"blueprint": bad})
check("dangling material ref errors", "error" in e and "unobtainium" in e["error"])

bad = json.loads(json.dumps(GOOD_BP))
bad["relations"] = [{"subject": "cap", "predicate": "sits_on", "object": "ghost"}]
e = call("compile_blueprint", {"blueprint": bad})
check("bad relation target errors", "error" in e and "ghost" in e["error"])

e = call("compile_blueprint", {"blueprint": {"parts": [
    {"node": "rod", "builder": "beam", "material": "steel", "confidence": 0.9}]}})
check("missing required params named", "error" in e and "start" in e["error"], str(e)[:120])

bad = json.loads(json.dumps(GOOD_BP))
bad["parts"].append(spec("box", node="body", params={"size": [1, 1, 1]}))
e = call("compile_blueprint", {"blueprint": bad})
check("duplicate node errors", "error" in e and "duplicate" in e["error"])

print("check_assertions against a symmetric build:")
b = call("build", {"recipe": recipe, "name": "hut", "join": False})
check("compiled recipe builds", "error" not in b, str(b.get("error", ""))[:100])
a = call("check_assertions", {"assertions": [
    {"check": "part_count", "expected": 2},
    {"check": "ratio", "name": "cap_to_body",
     "of": ["body.z", "cap.z"], "expected": 1.0 / 0.3, "tolerance": 0.15},
    {"check": "symmetry_x", "min": 0.8},
    {"check": "color_present", "material": "steel"},
    {"check": "teleport", "expected": True},
    {"check": "ratio", "name": "wrong",
     "of": ["body.z", "cap.z"], "expected": 99.0, "tolerance": 0.15},
]})
res = {(x.get("check"), x.get("name")): x for x in a.get("results", [])}
check("overall fails (two bad assertions)", a.get("pass") is False)
check("part_count passes", res.get(("part_count", None), {}).get("status") == "pass",
      str(res.get(("part_count", None))))
check("ratio passes", res.get(("ratio", "cap_to_body"), {}).get("status") == "pass",
      str(res.get(("ratio", "cap_to_body"))))
check("symmetry passes on symmetric build",
      res.get(("symmetry_x", None), {}).get("status") == "pass",
      str(res.get(("symmetry_x", None))))
check("color stays manual, never a fake pass",
      res.get(("color_present", None), {}).get("status") == "manual")
check("unknown check fails loudly", res.get(("teleport", None), {}).get("status") == "fail")
check("wrong expectation fails", res.get(("ratio", "wrong"), {}).get("status") == "fail",
      str(res.get(("ratio", "wrong"))))

print("symmetry discriminates on an offset rebuild:")
rod_recipe = dict(recipe)
rod_recipe["finial"] = {"builder": "box", "size": [0.2, 0.2, 0.4],
                          "location": [0.7, 0, 1.2],
                          "material": {"name": "steel", **STEEL}, "detail": "game"}
b2 = call("build", {"recipe": rod_recipe, "name": "hut2", "join": False})
check("offset rebuild works", "error" not in b2)
s = call("check_assertions", {"assertions": [
    {"check": "part_count", "expected": 3},
    {"check": "symmetry_x", "min": 0.8},
]})
res2 = {(x.get("check"), x.get("name")): x for x in s.get("results", [])}
check("part_count passes on rebuild",
      res2.get(("part_count", None), {}).get("status") == "pass")
check("offset mass fails symmetry",
      res2.get(("symmetry_x", None), {}).get("status") == "fail",
      str(res2.get(("symmetry_x", None))))

print("joined scenes report honestly:")
bj = call("build", {"recipe": recipe, "name": "hut3", "join": True})
check("joined build works", "error" not in bj)
j = call("check_assertions", {"assertions": [
    {"check": "part_count", "expected": 2},
    {"check": "ratio", "name": "cap_to_body",
     "of": ["body.z", "cap.z"], "expected": 1.0 / 0.3, "tolerance": 0.15},
]})
res3 = {(x.get("check"), x.get("name")): x for x in j.get("results", [])}
check("part_count still passes joined",
      res3.get(("part_count", None), {}).get("status") == "pass")
check("merged-away node stays manual, never a fake pass",
      res3.get(("ratio", "cap_to_body"), {}).get("status") == "manual",
      str(res3.get(("ratio", "cap_to_body"))))

print("color coverage over renders:")
c0 = call("check_assertions", {
    "assertions": [{"check": "color_present", "material": "steel", "min_coverage": 0.1}],
    "materials": {"steel": STEEL}})
r0 = c0["results"][0]
check("no renders yet stays manual",
      r0.get("status") == "manual" and "render" in r0.get("detail", ""), str(r0))
rr = call("render_views", {"views": ["front", "back"], "resolution": 128,
                           "dir": "output/blueprint/renders"})
check("test renders produced",
      "error" not in rr and len(rr.get("renders", {})) == 2, str(rr)[:150])
c1 = call("check_assertions", {
    "assertions": [{"check": "color_present", "material": "steel", "min_coverage": 0.1}],
    "materials": {"steel": STEEL}})
r1 = c1["results"][0]
check("present color passes mechanically", r1.get("status") == "pass", str(r1))
check("measured coverage is substantial", (r1.get("measured") or 0) > 0.3,
      str(r1.get("measured")))
c2 = call("check_assertions", {
    "assertions": [{"check": "color_present", "color": [0.1, 0.9, 0.15],
                     "min_coverage": 0.05}]})
r2 = c2["results"][0]
check("absent color fails near zero",
      r2.get("status") == "fail" and (r2.get("measured") if r2.get("measured") is not None else 1) < 0.02,
      str(r2))

print("depth section validates:")
dbp = json.loads(json.dumps(GOOD_BP))
dbp["depth"] = {"view": "front", "occludes": [{"front": "body", "behind": "cap"}]}
e = call("compile_blueprint", {"blueprint": dbp})
check("depth with known nodes compiles", "error" not in e, str(e.get("error", ""))[:100])
dbp["depth"] = {"view": "front", "occludes": [{"front": "body", "behind": "ghost"}]}
e = call("compile_blueprint", {"blueprint": dbp})
check("depth with unknown node errors", "error" in e and "ghost" in e["error"])
dbp["depth"] = {"view": "sideways", "occludes": []}
e = call("compile_blueprint", {"blueprint": dbp})
check("depth with unknown view errors", "error" in e and "sideways" in e["error"])

print("occlusion from the front:")
obr = {"builder": "box", "material": {"name": "steel", **STEEL}, "detail": "game"}
bo = call("build", {"recipe": {
    "wall": dict(obr, size=[0.2, 2, 2], location=[1, 0, 1]),
    "cube": dict(obr, size=[0.5, 0.5, 0.5], location=[0, 0, 1])},
    "name": "occtest", "join": False})
check("occlusion scene builds", "error" not in bo)
oo = call("check_assertions", {"assertions": [
    {"check": "occlusion", "front": "wall", "behind": "cube", "view": "front", "min": 0.8},
    {"check": "occlusion", "front": "wall", "behind": "cube", "view": "back", "min": 0.5},
    {"check": "occlusion", "front": "wall", "behind": "cube", "view": "sideways", "min": 0.5},
]})
oor = oo.get("results", [])
check("fully occluded pair passes",
      len(oor) == 3 and oor[0].get("status") == "pass", str(oor[0] if oor else oo))
check("reversed view fails",
      len(oor) == 3 and oor[1].get("status") == "fail", str(oor[1] if len(oor) > 1 else oo))
check("unknown view fails loudly",
      len(oor) == 3 and oor[2].get("status") == "fail"
      and "sideways" in oor[2].get("detail", ""),
      str(oor[2] if len(oor) > 2 else oo))

print("mirror depth second opinion:")
mm = call("check_assertions", {"assertions": [
    {"check": "mirror_depth", "axis": "x", "resolution": 16, "min": 0.9},
]})
rm = mm["results"][0]
check("asymmetric asset fails mirror depth",
      rm.get("status") == "fail" and (rm.get("measured") or 1) < 0.5, str(rm))
bh = call("build", {"recipe": recipe, "name": "hut", "join": False})
check("symmetric rebuild works", "error" not in bh)
mh = call("check_assertions", {"assertions": [
    {"check": "mirror_depth", "axis": "x", "resolution": 16, "min": 0.9},
]})
rh = mh["results"][0]
check("symmetric asset passes mirror depth",
      rh.get("status") == "pass" and (rh.get("measured") or 0) > 0.95, str(rh))

proc.terminate()

print()
if failures:
    print(f"BLUEPRINT TEST FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("BLUEPRINT TEST OK")
