"""Godot bridge client: tool round-trip against a stub addon + screenshot path rules.

The editor addon is stubbed on loopback, so the client contract is checked
without Godot running: every tool answer passes through untouched, and a
screenshot payload is only accepted from ultima3d/godot/captures as a PNG
under 20 MiB. A static guard keeps the addon source free of the Godot 3 API
calls that made it fail to load on 4.x, and a live check runs only when an
editor bridge is actually listening.
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, ".")

from ultima3d.godot import bridge as godot_bridge

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DEFAULT_URL = godot_bridge.BRIDGE_URL

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StubEditor:
    """Minimal stand-in for the addon: answers one JSON body per POST."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(payload)
                tool = str(payload.get("tool", ""))
                result = outer.answers.get(tool, {"error": f"Unknown tool: {tool}"})
                raw = json.dumps(result).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/mcp" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def main():
    capture_dir = godot_bridge.CAPTURE_DIR
    scratch = ROOT / "output" / "godot_test"
    scratch.mkdir(parents=True, exist_ok=True)
    inside = capture_dir / "godot_test_small.png"
    outside = scratch / "outside.png"
    wrong_suffix = capture_dir / "godot_test_small.txt"
    oversized = capture_dir / "godot_test_big.png"
    created = [inside, outside, wrong_suffix, oversized]

    # 1. The six-tool contract the server registers.
    check("bridge exposes the six editor tools",
          sorted(t["name"] for t in godot_bridge.TOOLS) == sorted(
              ["status", "scene_tree", "run_project", "stop_project",
               "debug_output", "screenshot"]),
          str(len(godot_bridge.TOOLS)))

    # 2. Static guard: the Godot 4 fixes stay in the shipped addon.
    addon = (ROOT / "godot" / "addon" / "bridge.gd").read_text(encoding="utf-8")
    check("addon: get_available_bytes() takes no argument",
          "get_available_bytes(8" not in addon)
    check("addon: no Godot 3 viewport accessor",
          "get_editor_viewport" not in addon)
    check("addon: screenshot returns an absolute path",
          "globalize_path" in addon)
    plugin = (ROOT / "godot" / "addon" / "plugin.gd").read_text(encoding="utf-8")
    check("plugin starts the bridge on 8765", "8765" in plugin)

    # 3. Every tool answer passes through the client untouched.
    inside.write_bytes(PNG_MAGIC + b"stub-pixels")
    answers = {
        "status": {"ok": True, "editor": {"string": "4.7.2-stable (official)"},
                   "project": "Ultima3D Bridge Project",
                   "main_scene": "res://main.tscn"},
        "scene_tree": {"ok": True, "tree": [{"depth": 0, "name": "Main",
                                             "type": "Node3D", "children": 3}]},
        "run_project": {"ok": True},
        "stop_project": {"ok": True},
        "debug_output": {"ok": True, "lines": ["bridge starting", "tool: status"]},
        "screenshot": {"ok": True, "path": str(inside)},
    }
    with StubEditor(answers) as stub:
        godot_bridge.BRIDGE_URL = stub.url
        try:
            status = godot_bridge.call("status")
            check("status round-trips with editor + project",
                  status.get("ok") is True
                  and status.get("project") == "Ultima3D Bridge Project"
                  and status.get("main_scene") == "res://main.tscn",
                  str(status)[:120])
            tree = godot_bridge.call("scene_tree")
            node = (tree.get("tree") or [{}])[0]
            check("scene_tree round-trips the node tree",
                  node.get("name") == "Main" and node.get("type") == "Node3D",
                  str(node)[:120])
            check("run_project acknowledges",
                  godot_bridge.call("run_project").get("ok") is True)
            check("stop_project acknowledges",
                  godot_bridge.call("stop_project").get("ok") is True)
            logs = godot_bridge.call("debug_output")
            check("debug_output returns buffered lines",
                  logs.get("lines") == ["bridge starting", "tool: status"],
                  str(logs.get("lines")))

            shot = godot_bridge.call("screenshot")
            import base64
            payload = base64.b64decode(shot.get("_image_b64", ""))
            check("screenshot inside captures is inlined as base64",
                  payload == inside.read_bytes(),
                  f"{len(payload)}B")
            check("screenshot reports the resolved capture path",
                  Path(shot.get("_image_path", "")).name == inside.name,
                  shot.get("_image_path", ""))

            unknown = godot_bridge.call("no_such_tool")
            check("unknown tool surfaces the addon error",
                  "Unknown tool" in str(unknown.get("error")),
                  str(unknown.get("error"))[:80])

            check("requests carry only the tool name",
                  all(set(r) == {"tool"} for r in stub.requests),
                  str(stub.requests[:2]))
        finally:
            godot_bridge.BRIDGE_URL = DEFAULT_URL

    # 4. Screenshot payloads outside the capture directory are refused.
    for label, answer, needle in (
        ("outside the capture directory", {"ok": True, "path": str(outside)}, "outside"),
        ("wrong suffix", {"ok": True, "path": str(wrong_suffix)}, "outside"),
    ):
        with StubEditor({"screenshot": answer}) as stub:
            godot_bridge.BRIDGE_URL = stub.url
            try:
                godot_bridge.call("screenshot")
                failures.append(f"screenshot {label} should raise")
            except RuntimeError as e:
                check(f"screenshot {label} is refused", needle in str(e), str(e)[:90])
            finally:
                godot_bridge.BRIDGE_URL = DEFAULT_URL

    # 5. Oversized captures are refused before they are read into memory.
    with open(oversized, "wb") as handle:
        handle.write(PNG_MAGIC)
        handle.truncate(20 * 1024 * 1024 + 1)
    with StubEditor({"screenshot": {"ok": True, "path": str(oversized)}}) as stub:
        godot_bridge.BRIDGE_URL = stub.url
        try:
            godot_bridge.call("screenshot")
            failures.append("oversized screenshot should raise")
        except RuntimeError as e:
            check("oversized screenshot is refused", "20 MiB" in str(e), str(e)[:90])
        finally:
            godot_bridge.BRIDGE_URL = DEFAULT_URL

    # 6. A dead bridge is a clear error, not a hang.
    port = free_port()
    godot_bridge.BRIDGE_URL = f"http://127.0.0.1:{port}/mcp"
    try:
        godot_bridge.call("status")
        failures.append("unreachable bridge should raise")
    except RuntimeError as e:
        check("unreachable bridge raises with the URL in the message",
              "Cannot reach Godot editor bridge" in str(e), str(e)[:100])
    finally:
        godot_bridge.BRIDGE_URL = DEFAULT_URL

    # 7. The real bridge, only when an editor is actually listening.
    probe = socket.socket()
    probe.settimeout(1.0)
    try:
        probe.connect(("127.0.0.1", 8765))
        live = True
    except OSError:
        live = False
    finally:
        probe.close()
    if live:
        try:
            status = godot_bridge.call("status")
            check("live editor answers status",
                  status.get("ok") is True and "editor" in status,
                  str(status.get("project", ""))[:60])
        except Exception as e:
            failures.append(f"live godot: {e}")
    else:
        print("  [skip] no Godot editor on 127.0.0.1:8765; live check not run")

    for path in created:
        try:
            path.unlink()
        except OSError:
            pass
    try:
        scratch.rmdir()
    except OSError:
        pass
    return finish()


def finish():
    print()
    if failures:
        print(f"GODOT TEST FAILED ({len(failures)}):")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("GODOT TEST OK (6 tools round-trip + screenshot path rules enforced)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
