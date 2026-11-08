"""Loopback JSON daemon: lets shells and Freebuff skills drive the registered tools.

The daemon exists because Freebuff Desktop no longer loads MCP servers; a skill can
only shell out. It binds 127.0.0.1 only, dispatches through the same tool manager the
stdio server uses (so validation and error text match), shares the session budget,
and answers in JSON with image blocks written beside the other outputs.

Routes:
    GET  /health    liveness + session snapshot + busy flag
    GET  /status    session snapshot + registered tool count + busy flag
    GET  /tools     tool names and one-line descriptions (disabled modules hidden)
    GET  /skills    managed-skill registry state
    GET  /modules   backend-module enable flags (effective after restart)
    POST /call      {"tool": "<name>", "args": {...}} -> the tool's result
    POST /skills/enable  {"id": ..., "enabled": bool}
    POST /skills/update  {"id": ...} or {"all": true} (updater; no-op offline)
    POST /modules   {"module": ..., "enabled": bool} (restart to apply)
    POST /shutdown  stop the daemon (the session is not resumed by a new process)
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .registry import mcp
from .session import SESSION, SessionExpired

DEFAULT_ADDR = "127.0.0.1:8791"
TOKEN_ENV = "ULTIMA3D_DAEMON_TOKEN"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _tools() -> list:
    """Registered tools, tolerant of mcp versions that list them asynchronously."""
    tools = mcp.list_tools()
    if inspect.isawaitable(tools):
        tools = asyncio.run(tools)
    return list(tools)


def _visible_tools() -> list:
    """Registered tools minus any whose module is disabled in config."""
    from . import config as _config

    cfg = _config.load()
    return [tool for tool in _tools()
            if _config.module_enabled(_tool_module(tool.name), cfg)]


def split_address(addr: str) -> tuple[str, int]:
    """Split HOST:PORT, refusing anything that is not a loopback bind."""
    host, _, raw_port = addr.rpartition(":")
    host = host or "127.0.0.1"
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            f"refusing to bind {host!r}: the daemon serves this machine only (use 127.0.0.1)"
        )
    try:
        port = int(raw_port or 8791)
    except ValueError as exc:
        raise ValueError(f"bad port in {addr!r}") from exc
    return host, port


def payload_of(result: Any, out_dir: Path) -> dict:
    """Turn an MCP CallToolResult into JSON, saving image blocks as file paths."""
    blocks = getattr(result, "content", None) or []
    values: list[Any] = []
    for index, block in enumerate(blocks):
        kind = getattr(block, "type", "")
        if kind == "text":
            text = getattr(block, "text", "")
            try:
                values.append(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                values.append(text)
        elif kind == "image":
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"daemon_image_{index}.png"
            path.write_bytes(base64.b64decode(getattr(block, "data", "") or ""))
            values.append({"image": str(path), "mime": getattr(block, "mimeType", "image/png")})
        else:
            values.append({"type": kind, "value": repr(block)})
    body = values[0] if len(values) == 1 else values
    return {"ok": not getattr(result, "isError", False), "result": body}


def _tool_module(name: str) -> str:
    """Which backend module owns a tool (server.py when loaded, prefixes otherwise)."""
    try:
        from .server import tool_module  # already imported in every daemon flow

        return tool_module(name)
    except Exception:
        if name.startswith("gimp_"):
            return "gimp"
        if name.startswith("godot_"):
            return "godot"
        return "procedural"


def _modules_snapshot() -> dict:
    """Enable flags plus which modules actually registered tools this boot."""
    from . import config as _config

    cfg = _config.load()
    try:
        from . import server as _server

        loaded = dict(getattr(_server, "MODULES", {}))
    except Exception:
        loaded = {}
    return {"flags": cfg.get("modules", {}), "loaded": loaded,
            "note": "flag changes apply on daemon restart"}


def make_handler(out_dir: Path) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one output directory."""
    import threading as _threading

    flight = {"count": 0}
    flight_lock = _threading.Lock()

    def _busy() -> bool:
        with flight_lock:
            return flight["count"] > 0

    class Handler(BaseHTTPRequestHandler):
        server_version = "Ultima3D-Daemon"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib hook
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                with open(out_dir / "daemon.log", "a", encoding="utf-8") as fh:
                    fh.write(f"{self.log_date_time_string()} {fmt % args}\n")
            except OSError:
                pass

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            token = getattr(self.server, "ultima3d_token", None)
            if not token:
                return True
            return self.headers.get("X-Ultima3D-Token") == token

        def _deny(self) -> None:
            self._send(403, {"ok": False, "error": "bad or missing X-Ultima3D-Token"})

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            if not self._authorized():
                return self._deny()
            if self.path == "/health":
                return self._send(200, {"ok": True, "skill": "ultima3d",
                                        "session": SESSION.snapshot(), "busy": _busy()})
            if self.path == "/status":
                return self._send(200, {
                    "ok": True,
                    "session": SESSION.snapshot(),
                    "tools": len(_tools()),
                    "busy": _busy(),
                    "modules": _modules_snapshot(),
                })
            if self.path == "/tools":
                return self._send(200, {
                    "ok": True,
                    "tools": [
                        {"name": tool.name, "description": (tool.description or "").splitlines()[0]}
                        for tool in _visible_tools()
                    ],
                })
            if self.path == "/skills":
                from . import skills as _skills

                return self._send(200, _skills.list_skills())
            if self.path == "/modules":
                return self._send(200, {"ok": True, **_modules_snapshot()})
            return self._send(404, {"ok": False, "error": f"unknown route {self.path}"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            if not self._authorized():
                return self._deny()
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._send(400, {"ok": False, "error": "body must be JSON"})
            if not isinstance(body, dict):
                return self._send(400, {"ok": False, "error": "body must be a JSON object"})
            if self.path == "/call":
                return self._call(body)
            if self.path == "/skills/enable":
                from . import skills as _skills

                ident, enabled = body.get("id"), body.get("enabled", True)
                if not ident:
                    return self._send(400, {"ok": False, "error": 'body needs {"id": ..., "enabled": bool}'})
                return self._send(200, _skills.set_enabled(ident, bool(enabled)))
            if self.path == "/skills/update":
                from . import skills as _skills

                if body.get("all"):
                    return self._send(200, _skills.update_all(
                        dry_run=bool(body.get("dry_run", True))))
                ident = body.get("id")
                if not ident:
                    return self._send(400, {"ok": False,
                                           "error": 'body needs {"id": ...} or {"all": true}'})
                return self._send(200, _skills.update_skill(
                    ident, dry_run=bool(body.get("dry_run", True))))
            if self.path == "/modules":
                from . import config as _config

                mod, enabled = body.get("module"), body.get("enabled", True)
                if mod not in _config.MODULES:
                    return self._send(400, {"ok": False,
                                           "error": f"unknown module {mod!r}; want one of {list(_config.MODULES)}"})
                cfg = _config.set_flag("modules", mod, bool(enabled))
                snap = _modules_snapshot()
                snap["flags"] = cfg.get("modules", {})
                return self._send(200, {"ok": True, "module": mod, "enabled": bool(enabled), **snap})
            if self.path == "/shutdown":
                self._send(200, {"ok": True, "stopping": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
            return self._send(404, {"ok": False, "error": f"unknown route {self.path}"})

        def _call(self, body: dict) -> None:
            tool = body.get("tool") or body.get("name")
            if not isinstance(tool, str) or not tool:
                return self._send(400, {
                    "ok": False,
                    "error": 'body needs {"tool": "<name>", "args": {...}}',
                })
            args = body.get("args") or {}
            if not isinstance(args, dict):
                return self._send(400, {"ok": False, "error": "args must be a JSON object"})
            from . import config as _config

            module = _tool_module(tool)
            if not _config.module_enabled(module):
                return self._send(200, {
                    "ok": False,
                    "tool": tool,
                    "error": f"module {module!r} is disabled (enable it, then restart the daemon)",
                    "session": SESSION.snapshot(),
                })
            try:
                SESSION.check(tool)
            except SessionExpired as exc:
                return self._send(410, {"ok": False, "error": str(exc), "session": SESSION.snapshot()})
            with flight_lock:
                flight["count"] += 1
            try:
                result = asyncio.run(mcp.call_tool(tool, args))
            except Exception as exc:  # ToolError, UnexpectedToolError, validation
                return self._send(200, {
                    "ok": False,
                    "tool": tool,
                    "error": f"{type(exc).__name__}: {exc}",
                    "session": SESSION.snapshot(),
                })
            finally:
                with flight_lock:
                    flight["count"] -= 1
            payload = payload_of(result, out_dir)
            payload["tool"] = tool
            payload["session"] = SESSION.snapshot()
            return self._send(200, payload)

    return Handler


def serve(addr: str = DEFAULT_ADDR) -> None:
    """Run the daemon in the foreground until /shutdown or the session watchdog fires."""
    host, port = split_address(addr)
    out_dir = Path(os.environ.get("ULTIMA3D_OUT", Path.cwd() / "output"))
    server = ThreadingHTTPServer((host, port), make_handler(out_dir))
    server.daemon_threads = True
    token = os.environ.get(TOKEN_ENV) or None
    server.ultima3d_token = token  # type: ignore[attr-defined]
    print(json.dumps({"ok": True, "listening": f"http://{host}:{port}", "token_required": bool(token)}),
          flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    from . import server as _server  # noqa: F401  (registers the full tool surface)

    serve()
