"""Skill registry + module toggles: registry round-trips offline, config gates tools.

Uses temporary registry/config copies (ULTIMA3D_CONFIG) so the real tree is
never touched, except for read-only GETs against a throwaway daemon.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT)

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


from ultima3d import config as uconfig  # noqa: E402
from ultima3d import skills as uskills  # noqa: E402

tmp = tempfile.mkdtemp(prefix="ultima3d-skills-test-")
os.environ["ULTIMA3D_CONFIG"] = os.path.join(tmp, "config.json")
reg_copy = os.path.join(tmp, "registry.json")
shutil.copy(os.path.join(ROOT, "skills", "registry.json"), reg_copy)

# -- registry ---------------------------------------------------------------
listed = uskills.list_skills(reg_copy)
check("registry lists", listed.get("ok") and len(listed.get("skills", [])) == 4,
      str([s.get("id") for s in listed.get("skills", [])]))
ids = {s["id"] for s in listed.get("skills", [])}
check("all four seeds present", ids == {"ultima3d", "ponytail", "pullstack", "brain"}, str(ids))

off = uskills.set_enabled("brain", True, reg_copy)
check("enable unknown id fails cleanly",
      uskills.set_enabled("nope", True, reg_copy).get("ok") is False)
check("set_enabled round-trips", off.get("ok") and off.get("enabled") is True, str(off))
check("registry file records the flag",
      json.load(open(reg_copy, encoding="utf-8"))["skills"][3]["enabled"] is True)
check("config file mirrors the skill flag",
      json.load(open(os.environ["ULTIMA3D_CONFIG"], encoding="utf-8"))["skills"]["brain"] is True)
uskills.set_enabled("brain", False, reg_copy)

# -- updater (dry runs: no network) ------------------------------------------
for sid in ("ultima3d", "brain", "ponytail", "pullstack"):
    res = uskills.update_skill(sid, reg_copy, dry_run=True)
    check(f"dry-run update {sid} ok", res.get("ok") is True, str(res)[:140])
all_res = uskills.update_all(reg_copy, dry_run=True)
check("dry-run update-all ok",
      all_res.get("ok") is True and len(all_res.get("results", [])) == 4)

# -- config ------------------------------------------------------------------
defaults = uconfig.load(os.path.join(tmp, "missing.json"))
check("defaults enable every module",
      all(defaults["modules"].values()), str(defaults["modules"]))
uconfig.set_flag("modules", "gimp", False, os.path.join(tmp, "config.json"))
check("module flag persists",
      uconfig.module_enabled("gimp", uconfig.load(os.path.join(tmp, "config.json"))) is False)
check("unknown module defaults to enabled", uconfig.module_enabled("zzz") is True)

# -- tool map -----------------------------------------------------------------
# Re-enable gimp first: the import below snapshots ULTIMA3D_CONFIG at load time.
uconfig.set_flag("modules", "gimp", True, os.path.join(tmp, "config.json"))
from ultima3d.server import LIVE_TOOLS, MODULES, tool_module  # noqa: E402

check("server loaded all modules by default",
      MODULES == {"live": True, "godot": True, "gimp": True, "procedural": True}, str(MODULES))
check("tool_module routes prefixes",
      tool_module("gimp_exec") == "gimp" and tool_module("godot_status") == "godot"
      and tool_module("create_3d") == "procedural"
      and tool_module("execute_blender_code") == "live")


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(url, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


# -- daemon routes (throwaway daemon, isolated OUT + config) ------------------
port = free_port()
base = f"http://127.0.0.1:{port}"
env = dict(os.environ, ULTIMA3D_OUT=os.path.join(tmp, "out"),
           ULTIMA3D_SESSION_MINUTES="5", ULTIMA3D_CONFIG=os.path.join(tmp, "config.json"),
           ULTIMA3D_NO_DASHBOARD="1")
uconfig.set_flag("modules", "gimp", True, os.path.join(tmp, "config.json"))
proc = subprocess.Popen(
    [sys.executable, "-m", "ultima3d.server", "--http", f"127.0.0.1:{port}"],
    cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
try:
    deadline = time.monotonic() + 120
    status = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            check("daemon starts", False, f"exited {proc.returncode}")
            print(proc.stdout.read()[-2000:])
            break
        try:
            _, status = request(f"{base}/status", timeout=5)
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.5)
    else:
        check("daemon starts", False, "never answered /status")
    if status is not None:
        check("/status carries busy flag", "busy" in status, str(sorted(status)))
        check("/status carries modules", "modules" in status)
        _, skills_body = request(f"{base}/skills", timeout=10)
        check("GET /skills lists registry",
              skills_body.get("ok") and len(skills_body.get("skills", [])) == 4)
        _, mods = request(f"{base}/modules", timeout=10)
        check("GET /modules shows flags", mods.get("ok") and mods["flags"].get("gimp") is True)
        _, tools = request(f"{base}/tools", timeout=30)
        names = {t["name"] for t in tools.get("tools", [])}
        check("/tools hides nothing by default", len(names) == 65, f"{len(names)} tools")
        check("/tools includes BlenderKit",
              {"search_blenderkit_assets", "download_blenderkit_asset",
               "get_blenderkit_status", "get_blenderkit_asset_preview"} <= names,
              f"{len(names)} tools")
        _, dis = request(f"{base}/modules", {"module": "procedural", "enabled": False}, timeout=10)
        check("POST /modules disables", dis.get("ok") and dis.get("enabled") is False)
        _, tools2 = request(f"{base}/tools", timeout=30)
        names2 = {t["name"] for t in tools2.get("tools", [])}
        check("/tools hides procedural when disabled",
              "create_3d" not in names2 and "gimp_exec" in names2, f"{len(names2)} tools")
        code, refused = request(f"{base}/call", {"tool": "ultima3d_status", "args": {}}, timeout=30)
        check("call into disabled module refused",
              refused.get("ok") is False and "disabled" in refused.get("error", ""), str(refused)[:120])
        _, reen = request(f"{base}/modules", {"module": "procedural", "enabled": True}, timeout=10)
        check("POST /modules re-enables", reen.get("ok") and reen.get("enabled") is True)
        _, bad = request(f"{base}/modules", {"module": "nope", "enabled": True}, timeout=10)
        check("unknown module rejected", bad.get("ok") is False)
        try:
            request(f"{base}/shutdown", {}, timeout=10)
        except (urllib.error.URLError, ConnectionError):
            pass
finally:
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()

print()
if failures:
    print(f"SKILLS MANAGER TEST FAILED ({len(failures)}):")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("SKILLS MANAGER TEST OK")
