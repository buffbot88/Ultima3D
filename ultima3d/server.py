"""Ultima3D — unified agentic 3D MCP server.

One server, four backends: procedural asset compiler (background Blender
worker), live Blender GUI tools (vendored under ultima3d.live), Godot
editor tools (ultima3d.godot), and GIMP tools (ultima3d.gimp).
Backends degrade gracefully offline.

Config via env:
    ULTIMA3D_BLENDER   path to blender executable
                      (default: C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe)
    ULTIMA3D_OUT       output directory for renders/exports (default: ./output)
    ULTIMA3D_CALL_TIMEOUT
                      per-call deadline in seconds for worker responses
                      (default: 600); a hung worker is killed past it
    GODOT_MCP_URL      Godot bridge URL (default: http://127.0.0.1:8765/mcp)
    BLENDER_HOST/BLENDER_PORT
                      live-Blender GUI addon address (default: localhost:9876)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image

if __package__ in (None, ""):
    # Launched as a bare file path (python .../ultima3d/server.py), e.g. by an
    # MCP client config: anchor the project root on sys.path, then re-enter as
    # the ultima3d.server module so the package-relative imports below resolve.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    import runpy as _runpy
    _runpy.run_module("ultima3d.server", run_name="__main__")
    raise SystemExit

from .registry import mcp
from .session import SESSION, SessionExpired

# Side-effect imports: register live-Blender + Godot + GIMP tools on the same server.
from .live import server as _live_server  # noqa: F401
from .godot import tools as _godot_tools  # noqa: F401
from .gimp import tools as _gimp_tools  # noqa: F401

_HERE = Path(__file__).resolve().parent
# v0.6 moved the geometry worker to blender/worker.py; it still runs inside Blender.
WORKER = Path(os.environ.get("ULTIMA3D_WORKER", _HERE.parent / "blender" / "worker.py"))

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
)
OUT_DIR = Path(os.environ.get("ULTIMA3D_OUT", Path.cwd() / "output"))
CALL_TIMEOUT = float(os.environ.get("ULTIMA3D_CALL_TIMEOUT", "600"))

# ---------------------------------------------------------------------------
# Worker process management: persistent blender --background, JSON lines.
# ---------------------------------------------------------------------------


class Worker:
    """Persistent blender --background process; a reader thread feeds a line queue."""
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self._id = 0
        self._lines: queue.Queue[str] = queue.Queue()
        self._reader: threading.Thread | None = None

    def _start(self):
        if not Path(BLENDER).is_file():
            raise ToolError(f"blender not found at {BLENDER!r}; set ULTIMA3D_BLENDER")
        self.proc = subprocess.Popen(
            [BLENDER, "--background", "--factory-startup", "--python", str(WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
        )
        # Fresh queue per process so late lines from a dead worker never pollute.
        self._lines = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop,
                                        args=(self.proc, self._lines),
                                        daemon=True, name="ultima3d-reader")
        self._reader.start()
        # worker prints a banner to stderr; first request confirms liveness
        self.call("ping")

    @staticmethod
    def _read_loop(proc, lines):
        for raw in proc.stdout:
            lines.put(raw)
        # EOF: the worker died; in-flight callers notice via the died check below.

    def ensure(self):
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        return self

    def stop(self) -> None:
        """Terminate the worker; the next call restarts it with a fresh scene."""
        self._kill()

    def _kill(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None  # ensure() restarts fresh on the next call

    def _timeout_error(self, op: str, timeout: float) -> ToolError:
        return ToolError(
            f"{op} timed out after {timeout:g}s waiting for the blender worker; "
            "the hung worker was terminated, so its scene/recipe state is lost and "
            "the next call restarts fresh. Raise ULTIMA3D_CALL_TIMEOUT for long bakes.")

    def call(self, op: str, params: dict | None = None, timeout: float = CALL_TIMEOUT) -> dict:
        try:
            SESSION.check(op)
        except SessionExpired as e:
            raise ToolError(str(e)) from None
        self.ensure()
        self._id += 1
        rid = self._id
        line = json.dumps({"id": rid, "op": op, "params": params or {}})
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._start()
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise self._timeout_error(op, timeout)
            if self.proc.poll() is not None and self._lines.empty():
                raise ToolError("blender worker died; check blender stderr")
            try:
                raw = self._lines.get(timeout=min(remaining, 5.0))
            except queue.Empty:
                continue
            raw = raw.strip()
            if not raw.startswith("@JSON@"):
                continue  # Blender progress/log noise on stdout
            resp = json.loads(raw[len("@JSON@"):])
            if resp.get("id") != rid:
                continue  # stale response from a crashed previous call
            if not resp.get("ok"):
                # ToolError (not RuntimeError) so mcp forwards the text to the model instead
                # of collapsing it to "Error executing tool <name>".
                raise ToolError(f"{op} failed: {resp.get('error')}\n{resp.get('trace', '')}")
            return resp["result"]


_w = Worker()


def _out(sub: str = "") -> Path:
    p = OUT_DIR / sub if sub else OUT_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_3d(
    recipe: dict,
    name: str = "asset",
    blueprint: dict | None = None,
    join: bool = True,
) -> str:
    """Build a 3D asset in Blender from a parametric recipe and (optionally) a design blueprint.

    The recipe maps node names to builder specs, e.g.:
      {"body": {"builder": "rounded_box", "size": [1, 0.45, 0.5], "bevel": 0.06, "material": {"name": "wood", "color": [0.45, 0.3, 0.18], "roughness": 0.7}},
       "roof": {"builder": "roof", "width": 1.1, "depth": 0.5, "height": 0.35, "location": [0, 0, 0.5]}}

    Available builders: box, rounded_box, cylinder, tapered_cylinder, sphere, torus, arch,
    panel, pipe, curve, extrusion, lathe, rock, beam, barrel, crate, roof, window, door,
    stairs, column, wall, fence, tree, weapon_blade, weapon_handle,
    terrain, scatter, skydome (vistas: scatter stamps an earlier node across a
    terrain node by name; skydome is render-only backdrop, excluded from validate/export).
    Recipe materials accept an optional procedural `texture` preset (wood_grain,
    bark, stone, mossy, grass_blend, worn_metal) with tex_scale/tex_blend/tex_seed.

    Blueprint follows the asset_blueprint v1 contract in README (kept with the asset for its lifecycle).
    Returns created nodes + triangle count. Afterwards use render_asset to inspect visually.
    """
    r = _w.call("build", {"recipe": recipe, "blueprint": blueprint, "name": name, "join": join})
    return json.dumps(r)


@mcp.tool()
def compile_blueprint(blueprint: dict) -> str:
    """Validate a v1 asset blueprint and compile it to recipe nodes plus warnings. Feed the recipe to create_3d."""
    return json.dumps(_w.call("compile_blueprint", {"blueprint": blueprint}))


@mcp.tool()
def refine_asset(changes: dict | None = None, recipe: dict | None = None) -> str:
    """Parametric refinement: modify recipe params and rebuild.

    Pass {"changes": {"node.param": new_value, ...}} using dotted node names from the last
    build (e.g. {"roof.height": 0.5, "body.radius": 0.5}). A change naming a real node but a
    parameter its builder does not accept is reported under "ignored", not "applied"; the
    response lists the parameters the builder does accept. Or pass {"recipe": {...}} to
    replace the whole recipe.
    """
    try:
        inspect = _w.call("inspect", {})
        last_recipe = inspect.get("recipe") or {}
        name = inspect.get("name", "asset")
        blueprint = inspect.get("blueprint")
    except (RuntimeError, ToolError):
        # empty scene: allow a full-recipe refine as the initial build
        last_recipe, name, blueprint = {}, "asset", None
    if recipe is None:
        recipe = json.loads(json.dumps(last_recipe))
        applied, ignored = [], []
        for dotted, value in (changes or {}).items():
            node, _, key = dotted.partition(".")
            if node in recipe and key:
                recipe[node][key] = value
                applied.append(dotted)
            else:
                ignored.append(dotted)
        result = _w.call("build", {"recipe": recipe, "name": name,
                                   "blueprint": blueprint, "join": True})
        # A change can name a real node yet a parameter the builder does not accept; the
        # builder swallows it and keeps the previous geometry. Report those honestly.
        dropped = {f"{node}.{key}"
                   for node, info in (result.get("ignored_params") or {}).items()
                   for key in info["ignored"]}
        result["applied"] = [d for d in applied if d not in dropped]
        result["ignored"] = ignored + sorted(dropped)
    else:
        result = _w.call("build", {"recipe": recipe, "name": name,
                                   "blueprint": blueprint, "join": True})
    return json.dumps(result)


@mcp.tool()
def inspect_asset() -> str:
    """Return machine-readable facts about the current asset: triangles, dimensions, materials, UVs, recipe."""
    return json.dumps(_w.call("inspect", {}))


@mcp.tool()
def render_asset(views: list[str] | None = None, resolution: int = 512) -> list[Image]:
    """Render the asset from standard angles (front/back/left/right/34s/top/wire) for visual evaluation.

    Returns the images inline so a multimodal LLM can judge proportions, silhouette and detail.
    """
    d = _out("renders")
    r = _w.call("render_views", {"views": views, "resolution": resolution, "dir": str(d)})
    images = []
    for v in (views or ["front", "back", "left", "right", "front_34", "back_34", "top", "wire"]):
        p = Path(r["renders"].get(v, ""))
        if p.is_file():
            images.append(Image(path=str(p)))
    return images


@mcp.tool()
def set_material(name: str, color: list[float] | None = None,
                 roughness: float | None = None, metallic: float | None = None,
                 object: str | None = None, texture: str | None = None,
                 tex_scale: float = 3.0, tex_blend: float = 0.5,
                 tex_seed: int = 0) -> str:
    """Apply a PBR material to the asset (or a named object). color = [r,g,b] 0-1.

    texture names a procedural preset (wood_grain, bark, stone, mossy,
    grass_blend, worn_metal); tex_scale/tex_blend/tex_seed tune it. Unknown
    names fall back to flat color. Recipe materials accept the same keys.
    """
    return json.dumps(_w.call("set_material", {
        "name": name, "color": color, "roughness": roughness,
        "metallic": metallic, "object": object, "texture": texture,
        "tex_scale": tex_scale, "tex_blend": tex_blend, "tex_seed": tex_seed}))


@mcp.tool()
def set_geometry(builder: str, node_name: str, params: dict | None = None) -> str:
    """Add one node with a given builder to the current recipe and rebuild (additive edit)."""
    insp = json.loads(inspect_asset())
    recipe = insp.get("recipe") or {}
    recipe[node_name] = {"builder": builder, **(params or {})}
    return json.dumps(_w.call("build", {"recipe": recipe, "name": insp.get("name", "asset"),
                                        "blueprint": insp.get("blueprint"), "join": True}))


@mcp.tool()
def validate_asset(triangle_budget: int = 8000) -> str:
    """Machine-checked validation: duplicates, zero-area faces, budget, UVs, materials, scale, manifoldness."""
    return json.dumps(_w.call("validate", {"triangle_budget": triangle_budget}))


@mcp.tool()
def check_assertions(assertions: list, materials: dict | None = None) -> str:
    """Verify blueprint assertions against the live asset: part count, ratios, symmetry, and color coverage over renders are measured in bpy. Unmeasurable checks return manual status for the director to verify against renders."""
    return json.dumps(_w.call("check_assertions", {"assertions": assertions, "materials": materials}))


@mcp.tool()
def finalize_asset(
    name: str | None = None,
    triangle_budget: int = 8000,
    lods: list[int] | None = None,
    collision: str = "auto",
    texture_resolution: int = 1024,
    channels: list[str] | None = None,
    bake: bool = True,
) -> str:
    """Game-ready export: clean, UV, decimate to budget, bake textures, LODs, collision hull, GLB + asset.json.

    lods e.g. [8000, 4000, 1500] (first = main budget; later entries become LOD files).
    channels from: albedo, normal, roughness, metallic. Returns file paths and stats.
    """
    r = _w.call("finalize", {
        "name": name, "triangle_budget": triangle_budget, "lods": lods,
        "collision": collision, "texture_resolution": texture_resolution,
        "channels": channels or ["albedo", "normal", "roughness"],
        "bake": bake, "dir": str(_out("export")),
    })
    return json.dumps(r)


@mcp.tool()
def rig_asset(bones: int | None = None) -> str:
    """Multi-bone auto-rig: mass-adaptive vertical bone chain with automatic vertex weights."""
    return json.dumps(_w.call("rig", {"bones": bones}))


@mcp.tool()
def save_recipe(path: str) -> str:
    """Save the current parametric recipe + blueprint as JSON for later editing/rebuilding."""
    return json.dumps(_w.call("save_recipe", {"path": path}))


@mcp.tool()
def load_recipe(path: str, rebuild: bool = True) -> str:
    """Load a saved recipe; optionally rebuild the asset from it immediately."""
    r = _w.call("load_recipe", {"path": path})
    if rebuild:
        with open(path) as f:
            data = json.load(f)
        r["build"] = _w.call("build", {"recipe": data["recipe"], "name": data.get("asset") or "asset",
                                       "blueprint": data.get("blueprint"), "join": True})
    return json.dumps(r)


@mcp.tool()
def ultima3d_status() -> str:
    """Check the Blender worker: version, liveness, current scene state."""
    try:
        ping = _w.call("ping")
        return json.dumps({"ok": True, "blender": ping.get("blender"), "worker": "running",
                           "session": SESSION.snapshot()})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e), "session": SESSION.snapshot()})


@mcp.tool()
def reset_scene() -> str:
    """Clear the Blender scene and forget the current recipe/blueprint."""
    return json.dumps(_w.call("clear", {}))


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Agentic pipeline over the three backends.
# ---------------------------------------------------------------------------


@mcp.tool()
def ultima3d_status_all() -> str:
    """One snapshot: worker, Godot bridge, live-Blender GUI."""
    from . import agentic
    from .godot.bridge import call as godot_call
    try:
        ping = _w.call("ping")
        ultima = {"ok": True, "blender": ping.get("blender"), "worker": "running"}
    except Exception as e:
        ultima = {"ok": False, "error": str(e)}
    try:
        godot = godot_call("status")
    except Exception as e:
        godot = {"ok": False, "error": str(e)}
    try:
        from .live.server import get_blender_connection

        conn = get_blender_connection()
        live = {"ok": True, "host": conn.host, "port": conn.port}
    except Exception as e:
        live = {"ok": False, "error": str(e)}
    return json.dumps(agentic.combine_status(ultima, godot, live))


@mcp.tool()
def export_asset_to_godot(name: str | None = None, dest_dir: str | None = None) -> str:
    """Copy the finalized GLB into a Godot project folder for import."""
    import shutil
    dest = Path(dest_dir) if dest_dir else _out("godot_import")
    dest.mkdir(parents=True, exist_ok=True)
    src_name = name
    if not src_name:
        try:
            src_name = _w.call("inspect", {}).get("name", "asset")
        except Exception:
            src_name = "asset"
    src = _out("export") / f"{src_name}.glb"
    if not src.is_file():
        raise ToolError(f"no finalized GLB at {src}; run finalize_asset(name={src_name!r}) first")
    out = dest / src.name
    shutil.copy2(src, out)
    return json.dumps({"ok": True, "src": str(src), "dest": str(out),
                       "note": "import it in Godot (auto .godot import on scan); no .tscn is created"})


@mcp.tool()
def review_asset_loop(triangle_budget: int = 8000, assertions: list | None = None,
                      materials: dict | None = None) -> str:
    """Run validate (+ optional critic) and report done vs next fixes."""
    from . import agentic
    validation = _w.call("validate", {"triangle_budget": triangle_budget})
    assertions_json = None
    if assertions:
        assertions_json = json.dumps(_w.call("check_assertions", {
            "assertions": assertions, "materials": materials}))
    return json.dumps(agentic.review_gate(json.dumps(validation), assertions_json))


def _on_session_deadline() -> None:
    """Stop the server when the session budget is spent, so nothing runs continuously."""
    limit = SESSION.limit_minutes
    try:
        _w.stop()
    except Exception:
        pass
    try:
        with open(_out() / "session.log", "a", encoding="utf-8") as handle:
            print(f"session budget of {limit:g} minutes exhausted; server stopped", file=handle)
    except OSError:
        pass
    os._exit(0)


def cli():
    """Run stdio MCP by default, or the loopback JSON daemon with --http."""
    parser = argparse.ArgumentParser(
        description="Ultima3D host: stdio MCP server, or the loopback daemon skills drive.")
    parser.add_argument("--http", nargs="?", const="127.0.0.1:8791", default=None,
                        metavar="HOST:PORT",
                        help="run the loopback JSON daemon instead of stdio MCP")
    parser.add_argument("--session-minutes", type=float, default=None,
                        help=f"session cap in minutes (default {SESSION.limit_minutes:g})")
    args, unknown = parser.parse_known_args()
    if unknown:
        print(f"ignoring unrecognized arguments: {unknown}", file=sys.stderr)
    if args.session_minutes is not None:
        SESSION.set_limit_minutes(args.session_minutes)
    SESSION.start()
    SESSION.enforce(_on_session_deadline)
    if args.http:
        from .daemon import serve

        try:
            serve(args.http)
        except ValueError as exc:
            raise SystemExit(f"error: {exc}") from None
    else:
        mcp.run()


if __name__ == "__main__":
    cli()
