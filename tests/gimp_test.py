"""GIMP backend test: headless texture synthesis + offline-tolerant batch bridge."""
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")

from ultima3d.gimp import bridge
from ultima3d.textures import PRESETS, make_texture

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


# 1. status() never raises, reports exe/version/scripts
try:
    s = bridge.status()
    assert s["ok"] and "exe_present" in s and "scripts" in s, s
    assert "prep_image" in s["scripts"] and "export_png" in s["scripts"], s["scripts"]
    print(f"  [ok] status exe_present={s['exe_present']} scripts={s['scripts']}")
except Exception as e:
    failures.append(f"status: {e}")

# 2. missing exe -> clean RuntimeError, no hang, no launch
os.environ["ULTIMA3D_GIMP"] = r"C:\nonexistent\gimp.exe"
try:
    bridge.run_script("prep_image", image="in.png", output="out.png")
    failures.append("run_script with missing exe should raise")
except RuntimeError as e:
    print(f"  [ok] missing-exe raises: {e}")
except Exception as e:
    failures.append(f"run_script wrong error: {e}")
finally:
    del os.environ["ULTIMA3D_GIMP"]

# 3. guard: code that never touches GIMP is refused without launching
os.environ["ULTIMA3D_GIMP"] = sys.executable  # any real file passes the is_file gate
try:
    bridge.run_script("1+1")
    failures.append("run_script should refuse code without gimp- calls")
except ValueError as e:
    print(f"  [ok] guard refuses inert code: {e}")
except Exception as e:
    failures.append(f"run_script guard wrong error: {e}")
finally:
    del os.environ["ULTIMA3D_GIMP"]

# 4. every preset synthesizes a valid trio, deterministically
for preset in PRESETS:
    try:
        r1 = make_texture(preset, size=64, seed=7, out_dir="output/gimp_test")
        r2 = make_texture(preset, size=64, seed=7, out_dir="output/gimp_test_rerun")
        assert set(r1["files"]) == {"albedo", "rough", "normal"}, r1["files"]
        for kind, p in r1["files"].items():
            raw = open(p, "rb").read()
            assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{preset}/{kind}: bad PNG magic"
            raw2 = open(r2["files"][kind], "rb").read()
            assert raw == raw2, f"{preset}/{kind}: same seed gave different bytes"
            assert len(raw) > 1000, f"{preset}/{kind}: suspiciously small PNG"
        print(f"  [ok] {preset:12s} trio ok, deterministic ({len(open(r1['files']['albedo'],'rb').read())}B albedo)")
    except Exception as e:
        failures.append(f"make_texture {preset}: {e}")

# 5. unknown preset is a clean ValueError
try:
    make_texture("velvet", size=64, out_dir="output/gimp_test")
    failures.append("unknown preset should raise")
except ValueError:
    print("  [ok] unknown preset raises ValueError")

# 6. presets exist and take %%INPUT%%/%%OUTPUT%% slots, filled purely
try:
    for name in ("prep_image", "export_png"):
        src = Path(f"gimp/scripts/{name}.scm").read_text(encoding="utf-8")
        assert "%%INPUT%%" in src and "%%OUTPUT%%" in src, name
    code, source = bridge.wrap_preset("prep_image", "C:\\in\\a.png", "C:/out/b.png")
    assert "%%INPUT%%" not in code and "%%OUTPUT%%" not in code, code[:120]
    assert "C:/in/a.png" in code and "C:/out/b.png" in code, code[:160]
    assert source == "preset:prep_image", source
    try:
        bridge.wrap_preset("prep_image", None, "C:/out/b.png")
        failures.append("wrap_preset should require image")
    except ValueError:
        pass
    print("  [ok] preset slot substitution is pure and validated")
except Exception as e:
    failures.append(f"preset wrap: {e}")

# 7. batch failure surfaces as text: a doomed exe exits nonzero, no hang
os.environ["ULTIMA3D_GIMP"] = sys.executable
try:
    bridge.run_batch(['(gimp-message "hi")'], timeout=30.0)
    failures.append("run_batch with failing exe should raise")
except RuntimeError as e:
    print(f"  [ok] batch failure surfaces: {str(e)[:90]}")
except Exception as e:
    failures.append(f"run_batch wrong error: {e}")
finally:
    del os.environ["ULTIMA3D_GIMP"]

# 8. script-fu server absent -> fast, clean False (no hang)
os.environ["ULTIMA3D_GIMP_PORT"] = "59998"
try:
    assert bridge.live(timeout=1.0) is False
    print("  [ok] live() False when no script-fu server answers")
except Exception as e:
    failures.append(f"live: {e}")
finally:
    del os.environ["ULTIMA3D_GIMP_PORT"]

# 9. create-image codegen is pure and validated
try:
    code = bridge.build_create(1024, 768, "transparent", "C:/out/hero.png")
    assert "gimp-image-new 1024 768" in code and "TRANSPARENT-FILL" in code, code[:160]
    assert "C:/out/hero.png" in code, code[:200]
    good = dict(width=64, height=64, background="white", out_path="x.png")
    for bad in (dict(background="plaid"), dict(width=0), dict(height=20000)):
        try:
            bridge.build_create(**{**good, **bad})
            failures.append(f"build_create should reject {bad}")
        except ValueError:
            pass
    print("  [ok] build_create codegen valid; bad inputs rejected")
except Exception as e:
    failures.append(f"build_create: {e}")

# 10. the real thing, only when GIMP is actually installed
if bridge.exe_exists():
    try:
        v = bridge.version(timeout=60.0)
        assert v and "GIMP" in v.upper() or "GNU" in (v or "").upper(), v
        print(f"  [ok] gimp --version: {v}")
        out = bridge.run_batch(['(display "ultima3d-ok")'], timeout=180.0)
        assert "ultima3d-ok" in (out["stdout"] + out["stderr"]), out
        print("  [ok] batch Script-Fu runs and its output comes back")
    except Exception as e:
        failures.append(f"live gimp: {e}")
else:
    print("  [skip] GIMP not installed; batch smoke not run")

if failures:
    print(f"\nGIMP TEST FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"\nGIMP TEST OK ({len(PRESETS)} presets synthesize + batch bridge offline-tolerant)")
