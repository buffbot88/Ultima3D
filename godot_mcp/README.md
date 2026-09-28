# Godot MCP bridge

This repository-local bridge connects an MCP client to the **currently running Godot editor** through a loopback-only editor plugin. It does not install packages or expose an external network listener.

## Enable in the running editor

The plugin is registered as enabled in `client/project.godot`. Since Godot may not apply external project-setting changes immediately, use **Project > Reload Current Project** (or restart the editor) to activate it. The editor console should print `Godot MCP Bridge listening on http://127.0.0.1:8765`. If it does not, open **Project > Project Settings > Plugins** and enable **Godot MCP Bridge** (`client/addons/godot_mcp/plugin.cfg`).

The addon serves only a fixed allowlist: editor status, current scene-tree inspection, run/stop main scene, buffered Godot log lines, and an editor viewport screenshot. It cannot execute arbitrary scripts or edit project files. Screenshot captures are saved under `godot_mcp/captures/`.

## Configure an MCP-capable client

Use a stdio MCP configuration pointing at this repository's `godot_mcp/server.py`:

```json
{
  "mcpServers": {
    "godot": {
      "command": "python",
      "args": ["/absolute/path/to/TWOR/godot_mcp/server.py"]
    }
  }
}
```

The adapter uses Python's standard library only. Restart/refresh the MCP client after saving its config. It exposes `godot_status`, `godot_scene_tree`, `godot_run_project`, `godot_stop_project`, `godot_debug_output`, and `godot_screenshot`.

Optional bridge URL override: set `GODOT_MCP_URL` (default `http://127.0.0.1:8765/mcp`).
