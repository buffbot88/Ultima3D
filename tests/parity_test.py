"""Tool-surface parity: the Python host must register the full 61-tool surface + prompt.

Mirrors rust/crates/ultima3d-server/tests/parity.rs so the restored Python host
cannot silently lose tools, and checks the surface the skill and daemon rely on.
"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ultima3d.server  # noqa: F401  (registers the 18 procedural tools)
from ultima3d.registry import mcp

EXPECTED_COUNT = 61

REQUIRED = {
    # procedural + pipeline
    "create_3d", "compile_blueprint", "refine_asset", "inspect_asset", "render_asset",
    "set_material", "set_geometry", "validate_asset", "check_assertions", "finalize_asset",
    "rig_asset", "save_recipe", "load_recipe", "reset_scene", "ultima3d_status",
    "ultima3d_status_all", "export_asset_to_godot", "review_asset_loop",
    # live Blender
    "get_scene_info", "get_object_info", "execute_blender_code", "get_viewport_screenshot",
    "get_addon_status",
    # Godot
    "godot_status", "godot_scene_tree", "godot_run_project", "godot_stop_project",
    "godot_debug_output", "godot_screenshot",
    # Photoshop CS3
    "photoshop_status", "photoshop_exec", "photoshop_create_image", "photoshop_run_script",
    "photoshop_make_texture",
}

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


def maybe_await(value):
    """Tolerate mcp versions that list tools asynchronously."""
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def main():
    tools = maybe_await(mcp.list_tools())
    names = {tool.name for tool in tools}
    check("registry holds 61 tools", len(names) == EXPECTED_COUNT,
          f"got {len(names)}; missing={sorted(EXPECTED_COUNT and set())}")
    missing = sorted(REQUIRED - names)
    check("every required tool is registered", not missing, f"missing={missing}")
    if len(names) != EXPECTED_COUNT:
        print(f"  registered: {sorted(names)}")

    prompts = maybe_await(mcp.list_prompts())
    check("asset_creation_strategy prompt survived", any(
        prompt.name == "asset_creation_strategy" for prompt in prompts))

    descriptions = {tool.name: (tool.description or "") for tool in tools}
    check("every tool has a description", all(descriptions[name] for name in names))

    # The skill and daemon depend on this import path working without mcp.run().
    from ultima3d import daemon, skill

    host, port = daemon.split_address("127.0.0.1:8791")
    check("daemon address parsing works", (host, port) == ("127.0.0.1", 8791))
    try:
        daemon.split_address("0.0.0.0:8791")
        check("daemon refuses a non-loopback bind", False, "0.0.0.0 accepted")
    except ValueError:
        check("daemon refuses a non-loopback bind", True)
    check("skill bridge targets loopback by default", skill.DEFAULT_ADDR == "127.0.0.1:8791",
          skill.DEFAULT_ADDR)

    print()
    if failures:
        print(f"PARITY TEST FAILED ({len(failures)}):")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    print(f"PARITY TEST OK ({len(names)} tools, 1 prompt)")


if __name__ == "__main__":
    main()
