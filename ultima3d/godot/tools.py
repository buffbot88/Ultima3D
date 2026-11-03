"""Godot tools registered on the unified Ultima3D server."""

from __future__ import annotations

import json

from ..registry import mcp
from .bridge import call


@mcp.tool()
def godot_status() -> str:
    """Get the running Godot editor/project status (offline-tolerant)."""
    try:
        return json.dumps(call("status"))
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def godot_scene_tree() -> str:
    """Inspect the currently edited Godot scene tree (offline-tolerant)."""
    try:
        return json.dumps(call("scene_tree"))
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def godot_run_project() -> str:
    """Run the current Godot project in the editor."""
    try:
        return json.dumps(call("run_project"))
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def godot_stop_project() -> str:
    """Stop the currently running Godot game."""
    try:
        return json.dumps(call("stop_project"))
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def godot_debug_output() -> str:
    """Read buffered Godot editor/game output since the last read."""
    try:
        return json.dumps(call("debug_output"))
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def godot_screenshot():
    """Capture the Godot editor/game viewport as PNG (offline raises ToolError)."""
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.mcpserver.utilities.types import Image

    try:
        data = call("screenshot")
    except Exception as e:
        raise ToolError(str(e))
    if data.get("error"):
        raise ToolError(str(data["error"]))
    return Image(path=str(data["_image_path"]))
