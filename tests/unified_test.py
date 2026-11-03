"""Ultima3D unified-server regression test (needs mcp>=2.0 + httpx installed)."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ultima3d import agentic
from ultima3d.godot import bridge as godot_bridge
from ultima3d.registry import mcp


def main():
    # 1. Single shared registry across all backends.
    import ultima3d.server as proc
    import ultima3d.live.server as live
    import ultima3d.godot.tools as godot_tools

    assert proc.mcp is live.mcp is godot_tools.mcp is mcp, "split registries"

    # 2. All three backends registered on the one server.
    tools = mcp.list_tools()
    if asyncio.iscoroutine(tools):
        tools = asyncio.run(tools)
    names = {t.name for t in tools}
    for required in ["create_3d", "finalize_asset", "validate_asset",
                     "get_scene_info", "execute_blender_code", "get_addon_status",
                     "godot_status", "godot_scene_tree", "godot_screenshot",
                     "ultima3d_status_all", "export_asset_to_godot", "review_asset_loop"]:
        assert required in names, f"missing tool: {required}"
    assert "live_blender_call" not in names, "proxy tool survived the fold-in"
    assert len(names) >= 50, f"expected 50+ folded tools, got {len(names)}"

    # 3. Prompt survived the port.
    prompts = mcp.list_prompts()
    if asyncio.iscoroutine(prompts):
        prompts = asyncio.run(prompts)
    assert any(p.name == "asset_creation_strategy" for p in prompts), "prompt missing"

    # 4. Agentic helpers unchanged in behavior.
    snap = agentic.combine_status({"ok": True}, {"ok": False}, {"ok": False})
    assert snap["ok"] is True, snap
    assert agentic.combine_status({"ok": False}, {}, {})["ok"] is False
    files = {"m.glb": "e/m.glb", "x": "e/m_LOD1.glb", "y": "e/m_collision.glb"}
    assert agentic.pick_export_source(files, "m") == "e/m.glb"
    ok = agentic.review_gate(json.dumps({"pass": True, "checks": {"has_uv": True}}))
    assert ok["done"] is True and ok["next_fixes"] == [], ok

    # 5. Godot bridge keeps its 6-tool contract.
    assert sorted(t["name"] for t in godot_bridge.TOOLS) == sorted(
        ["status", "scene_tree", "run_project", "stop_project", "debug_output", "screenshot"])

    # 6. Old standalone servers are gone.
    assert not os.path.exists("godot_mcp/server.py"), "godot_mcp standalone survived"
    assert not os.path.exists("ultima3d/live_blender.py"), "proxy module survived"
    assert not os.path.exists("ultima3d/godot_bridge.py"), "bridge shim survived"

    print(f"unified_test: PASS ({len(names)} tools, 1 registry)")


if __name__ == "__main__":
    main()
