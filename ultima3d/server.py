"""ultima3d — agentic 3D asset generator MCP server.

Exposes 13 high-level tools (create_3d, refine_asset, render_asset,
validate_asset, finalize_asset, ...) instead of hundreds of Blender buttons.
The Blender worker (ultima3d/blender_worker.py) owns the mechanics; the LLM
owns intent and visual judgement.

Config via env:
    ULTIMA3D_BLENDER   path to blender executable
                      (default: C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe)
    ULTIMA3D_OUT       output directory for renders/exports (default: ./output)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image

_HERE = Path(__file__).resolve().parent
WORKER = _HERE / "blender_worker.py"

BLENDER = os.environ.get(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
)
OUT_DIR = Path(os.environ.get("ULTIMA3D_OUT", Path.cwd() / "output"))

mcp = MCPServer("ultima3d")


# ---------------------------------------------------------------------------
# Worker process management: persistent blender --background, JSON lines.
# ---------------------------------------------------------------------------


class Worker:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self._id = 0

    def _start(self):
        if not Path(BLENDER).is_file():
            raise ToolError(f"blender not found at {BLENDER!r}; set ULTIMA3D_BLENDER")
        self.proc = subprocess.Popen(
            [BLENDER, "--background", "--factory-startup", "--python", str(WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
        )
        # worker prints a banner to stderr; first request confirms liveness
        self.call("ping")

    def ensure(self):
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        return self

    def call(self, op: str, params: dict | None = None) -> dict:
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
        while True:
            raw = self.proc.stdout.readline()
            if not raw:
                raise ToolError("blender worker died; check blender stderr")
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
    stairs, column, wall, fence, tree, weapon_blade, weapon_handle.

    Blueprint is a free-form hierarchical design doc (kept with the asset for its lifecycle).
    Returns created nodes + triangle count. Afterwards use render_asset to inspect visually.
    """
    r = _w.call("build", {"recipe": recipe, "blueprint": blueprint, "name": name, "join": join})
    return json.dumps(r)


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
                 object: str | None = None) -> str:
    """Apply a PBR material to the asset (or a named object). color = [r,g,b] 0-1."""
    return json.dumps(_w.call("set_material", {
        "name": name, "color": color, "roughness": roughness,
        "metallic": metallic, "object": object}))


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
        return json.dumps({"ok": True, "blender": ping.get("blender"), "worker": "running"})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def reset_scene() -> str:
    """Clear the Blender scene and forget the current recipe/blueprint."""
    return json.dumps(_w.call("clear", {}))


# ---------------------------------------------------------------------------


def cli():
    mcp.run()


if __name__ == "__main__":
    cli()
