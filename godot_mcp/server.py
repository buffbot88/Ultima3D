#!/usr/bin/env python3
"""Small stdio MCP server for the local Godot editor bridge.

No third-party packages are required. Expose this file as an MCP stdio server.
The Godot editor plugin must be enabled and listening on 127.0.0.1:8765.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BRIDGE_URL = os.environ.get("GODOT_MCP_URL", "http://127.0.0.1:8765/mcp")
CAPTURE_DIR = (Path(__file__).resolve().parent / "captures").resolve()
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {"name": "godot_status", "description": "Get the running Godot editor/project status.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "godot_scene_tree", "description": "Inspect the currently edited scene tree.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "godot_run_project", "description": "Run the current Godot project in the editor.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "godot_stop_project", "description": "Stop the currently running Godot game.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "godot_debug_output", "description": "Read captured Godot editor/game output since the last read.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "godot_screenshot", "description": "Capture the current editor/game viewport as a PNG file under godot_mcp/captures.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
]


def bridge(tool: str) -> dict:
    request = urllib.request.Request(
        BRIDGE_URL,
        data=json.dumps({"tool": tool}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot reach Godot editor bridge at {BRIDGE_URL}: {exc}") from exc


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle(message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    if method == "notifications/initialized" or method == "notifications/cancelled":
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "local-godot-editor", "version": "0.1.0"},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = str(params.get("name", ""))
        allowed = {tool["name"] for tool in TOOLS}
        if name not in allowed:
            result = {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}
        else:
            try:
                data = bridge(name.removeprefix("godot_"))
                if name == "godot_screenshot" and not data.get("error"):
                    candidate = Path(str(data.get("path", ""))).resolve()
                    if candidate.parent != CAPTURE_DIR or candidate.suffix.lower() != ".png":
                        raise RuntimeError("Screenshot path rejected: outside the MCP capture directory")
                    image = candidate.read_bytes()
                    if len(image) > 20 * 1024 * 1024:
                        raise RuntimeError("Screenshot exceeds the 20 MiB limit")
                    import base64
                    result = {"content": [
                        {"type": "text", "text": json.dumps({"path": str(candidate), "size_bytes": len(image)})},
                        {"type": "image", "mimeType": "image/png", "data": base64.b64encode(image).decode("ascii")},
                    ]}
                else:
                    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": bool(data.get("error"))}
            except Exception as exc:  # report a tool error, keep MCP transport alive
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if msg_id is not None:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return None


def main() -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if isinstance(message, dict):
                response = handle(message)
                if response is not None:
                    send(response)
        except Exception as exc:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}})


if __name__ == "__main__":
    main()
