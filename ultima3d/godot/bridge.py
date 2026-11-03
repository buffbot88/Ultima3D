"""Godot editor bridge client (stdlib only)."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BRIDGE_URL = os.environ.get("GODOT_MCP_URL", "http://127.0.0.1:8765/mcp")
CAPTURE_DIR = (Path(__file__).resolve().parent / "captures").resolve()

TOOLS = [
    {"name": "status", "description": "Get the running Godot editor/project status."},
    {"name": "scene_tree", "description": "Inspect the currently edited scene tree."},
    {"name": "run_project", "description": "Run the current Godot project in the editor."},
    {"name": "stop_project", "description": "Stop the currently running Godot game."},
    {"name": "debug_output", "description": "Read captured Godot editor/game output since the last read."},
    {"name": "screenshot", "description": "Capture the current editor/game viewport as PNG."},
]


def bridge(tool: str, timeout: float = 10.0) -> dict:
    """POST one tool call to the Godot editor addon bridge."""
    request = urllib.request.Request(
        BRIDGE_URL,
        data=json.dumps({"tool": tool}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot reach Godot editor bridge at {BRIDGE_URL}: {exc}") from exc


def call(tool: str) -> dict:
    """Call a bridge tool; screenshot payloads are validated + inlined as base64."""
    data = bridge(tool)
    if tool == "screenshot" and not data.get("error"):
        candidate = Path(str(data.get("path", ""))).resolve()
        if candidate.parent != CAPTURE_DIR or candidate.suffix.lower() != ".png":
            raise RuntimeError("Screenshot path rejected: outside the MCP capture directory")
        raw = candidate.read_bytes()
        if len(raw) > 20 * 1024 * 1024:
            raise RuntimeError("Screenshot exceeds the 20 MiB limit")
        data["_image_b64"] = base64.b64encode(raw).decode("ascii")
        data["_image_path"] = str(candidate)
    return data
