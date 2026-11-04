"""Photoshop CS3 module test: headless texture synthesis + offline-tolerant bridge."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, ".")

from ultima3d.photoshop import bridge
from ultima3d.photoshop.textures import PRESETS, make_texture

failures = []

# 1. status() never raises, reports scripts
try:
    s = bridge.status()
    assert s["ok"] and "exe_present" in s and "scripts" in s, s
    assert "prep_texture" in s["scripts"] and "export_png" in s["scripts"], s["scripts"]
    print(f"  [ok] status exe_present={s['exe_present']} scripts={s['scripts']}")
except Exception as e:
    failures.append(f"status: {e}")

# 2. missing exe -> clean RuntimeError, no hang
os.environ["ULTIMA3D_PHOTOSHOP"] = r"C:\nonexistent\Photoshop.exe"
try:
    bridge.run_script("prep_texture")
    failures.append("run_script with missing exe should raise")
except RuntimeError as e:
    print(f"  [ok] missing-exe raises: {e}")
except Exception as e:
    failures.append(f"run_script wrong error: {e}")

# 3. guard: code that never touches Photoshop is refused without launching
os.environ["ULTIMA3D_PHOTOSHOP"] = sys.executable  # any real file passes the is_file gate
try:
    bridge.run_script("1+1")
    failures.append("run_script should refuse code without app/activeDocument")
except ValueError as e:
    print(f"  [ok] guard refuses inert code: {e}")
except Exception as e:
    failures.append(f"run_script guard wrong error: {e}")
finally:
    del os.environ["ULTIMA3D_PHOTOSHOP"]

# 4. every preset synthesizes a valid trio, deterministically
for preset in PRESETS:
    try:
        r1 = make_texture(preset, size=64, seed=7, out_dir="output/photoshop_test")
        r2 = make_texture(preset, size=64, seed=7, out_dir="output/photoshop_test_rerun")
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
    make_texture("velvet", size=64, out_dir="output/photoshop_test")
    failures.append("unknown preset should raise")
except ValueError:
    print("  [ok] unknown preset raises ValueError")

# 6. daemon script exists and speaks the line-framed bridge protocol
try:
    daemon = Path("photoshop/daemon/ultima3d_bridge.jsx")
    src = daemon.read_text(encoding="utf-8")
    assert daemon.is_file()
    for token in ("listen", '"exec"', '"ping"', '"quit"', "readln", "writeln",
                  "IDLE_SECONDS", "MAX_MINUTES", "app.displayDialogs = prevDialogs"):
        assert token in src, f"daemon missing {token}"
    assert "conn.read(" not in src, "daemon still uses the blocking counted read"
    assert bridge.DAEMON_SCRIPT.resolve() == daemon.resolve(), bridge.DAEMON_SCRIPT
    print("  [ok] bridge daemon line-framed, idle hand-back, single source of truth")
except Exception as e:
    failures.append(f"daemon: {e}")


# 6b. the client speaks that protocol: one request line out, one reply line in
def stub(reply_line: bytes):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    seen = {}

    def serve():
        conn, _ = srv.accept()
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        seen["line"] = buf
        if reply_line:
            conn.sendall(reply_line)
        conn.close()
        srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return srv.getsockname()[1], seen, t


try:
    p, seen, t = stub(b'{"id":1,"ok":true,"result":"1"}\n')
    os.environ["ULTIMA3D_PHOTOSHOP_PORT"] = str(p)
    r = bridge.call("app.documents.length", timeout=5.0)
    t.join(2)
    assert r["ok"] and r["result"] == "1", r
    assert seen["line"].endswith(b"\n"), seen["line"][:80]
    assert json.loads(seen["line"])["code"] == "app.documents.length"
    print("  [ok] call() sends one newline-framed request and parses the reply")

    p, seen, t = stub(b'{"id":0,"ok":true,"result":"pong CS3 10.0"}\n')
    os.environ["ULTIMA3D_PHOTOSHOP_PORT"] = str(p)
    assert bridge.live(timeout=3.0) is True
    t.join(2)
    assert json.loads(seen["line"])["op"] == "ping", seen["line"][:80]
    print("  [ok] live() verifies with a ping instead of a bare connect")

    p, seen, t = stub(b'{"id":1,"ok":true,"result":"wrote C:/x.png"}\n')
    os.environ["ULTIMA3D_PHOTOSHOP_PORT"] = str(p)
    os.environ["ULTIMA3D_PHOTOSHOP"] = sys.executable
    r = bridge.run_script("export_png", timeout=5.0)
    t.join(2)
    assert r["source"] == "preset:export_png" and r["result"].startswith("wrote"), r
    assert b"ultima3d export_png" in seen["line"], seen["line"][:120]
    print("  [ok] run_script() routes the preset through the bridge, no second exe")
except Exception as e:
    failures.append(f"protocol: {e}")
finally:
    os.environ.pop("ULTIMA3D_PHOTOSHOP_PORT", None)
    os.environ.pop("ULTIMA3D_PHOTOSHOP", None)

# 7. live bridge absent -> fast, clean error naming the setup step (no hang)
os.environ["ULTIMA3D_PHOTOSHOP_PORT"] = "59998"
try:
    assert bridge.live(timeout=1.0) is False
    print("  [ok] live() False when Photoshop has no bridge running")
except Exception as e:
    failures.append(f"live: {e}")
try:
    bridge.call("app.documents.length", timeout=5.0)
    failures.append("call with no bridge should raise")
except RuntimeError as e:
    assert "Scripts > Browse" in str(e), str(e)[:120]
    print("  [ok] call() without bridge raises with setup instructions")
except Exception as e:
    failures.append(f"call wrong error: {e}")
finally:
    del os.environ["ULTIMA3D_PHOTOSHOP_PORT"]

# 8. create-image codegen is pure and validated
try:
    code = bridge.build_create(1024, 768, "transparent", "hero")
    assert "documents.add(1024, 768" in code and "TRANSPARENT" in code, code
    good = dict(width=64, height=64, background="white")
    for bad in (dict(background="plaid"), dict(width=0), dict(height=20000)):
        try:
            bridge.build_create(**{**good, **bad})
            failures.append(f"build_create should reject {bad}")
        except ValueError:
            pass
    print("  [ok] build_create codegen valid; bad inputs rejected")
except Exception as e:
    failures.append(f"build_create: {e}")

# 9. the JSX sources parse (ExtendScript is ES3, a subset Node accepts;
#    node --check refuses the .jsx extension, so check a .js copy)
import tempfile

node = shutil.which("node")
if node:
    for jsx in sorted(Path("photoshop").rglob("*.jsx")):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                probe = Path(tmp) / "probe.js"
                probe.write_text(jsx.read_text(encoding="utf-8"), encoding="utf-8")
                r = subprocess.run([node, "--check", str(probe)],
                                   capture_output=True, text=True, timeout=30)
            assert r.returncode == 0, (r.stderr or r.stdout).strip()[:200]
            print(f"  [ok] {jsx.as_posix()} parses")
        except Exception as e:
            failures.append(f"jsx syntax {jsx}: {e}")
else:
    print("  [skip] node not on PATH; JSX syntax not checked")

if failures:
    print(f"\nPHOTOSHOP TEST FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"\nPHOTOSHOP TEST OK ({len(PRESETS)} presets synthesize + line-framed bridge)")
