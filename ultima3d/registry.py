"""Single shared MCPServer for Ultima3D."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import MCPServer

ULTIMA_INSTRUCTIONS = """Ultima3D: procedural asset compiler (ultima3d worker) + live Blender GUI + Godot editor.

Procedural tools (create_3d, refine_asset, render_asset, validate_asset, finalize_asset) build
game-ready GLBs in a background Blender worker. Live tools (get_scene_info, execute_blender_code,
asset libraries) drive the user's open Blender GUI over :9876. Godot tools (godot_*) drive the
running Godot editor bridge. The LLM owns intent and visual judgement; Python measures results."""

LIVE_INSTRUCTIONS = """Live Blender drives the user's open Blender instance. execute_blender_code runs
arbitrary Python there, so scripts must not assume anything about the user's Blender.
Before writing code, call get_addon_status() for blender_version and get_scene_info() for
existing objects. Look shader nodes up by type, never by localized name. Never hardcode enum
identifiers; read them from bl_rna first. After changing anything, call get_viewport_screenshot()
and get_scene_info() to confirm. Call the asset_creation_strategy prompt for the asset-library
workflow (Poly Haven, Sketchfab, Poly Pizza, Hyper3D Rodin, Hunyuan3D)."""


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[dict[str, Any]]:
    """Startup checks + shutdown flush for the unified server."""
    import logging

    log = logging.getLogger("ultima3d")
    log.info("Ultima3D server starting up")
    try:
        from .live.server import live_startup

        live_startup()
    except Exception as e:  # offline backends are state, not fatal
        log.warning(f"Live-Blender startup check skipped: {e}")
    try:
        yield {}
    finally:
        try:
            from .live.trajectory import get_trajectory_recorder

            recorder = get_trajectory_recorder()
            recorder.close_episode("session_end")
            recorder.flush(2.0)
        except Exception:
            pass
        try:
            from .live.server import live_shutdown

            live_shutdown()
        except Exception:
            pass
        log.info("Ultima3D server shut down")


mcp = MCPServer(
    "ultima3d",
    lifespan=lifespan,
    instructions=ULTIMA_INSTRUCTIONS + "\n\n" + LIVE_INSTRUCTIONS,
)
