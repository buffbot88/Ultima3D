"""GIMP tools registered on the unified Ultima3D server."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..registry import mcp
from . import bridge
from ..textures import PRESETS, make_texture

OUT_DIR = Path(os.environ.get("ULTIMA3D_OUT", Path.cwd() / "output"))


@mcp.tool()
def gimp_status() -> str:
    """Check the GIMP backend: exe presence, version, Script-Fu server, presets."""
    try:
        return json.dumps(bridge.status())
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def gimp_exec(script: str, timeout: float = 180.0) -> str:
    """Run Script-Fu in a throwaway batch GIMP and return its console output.

    Batch runs to completion with an exit code, so a failure arrives as text —
    nothing wedges the editor. e.g. script='(gimp-message "hi")'.
    """
    try:
        return json.dumps(bridge.run_batch([script], timeout=timeout))
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def gimp_create_image(width: int = 1024, height: int = 1024,
                      background: str = "white", name: str = "ultima3d") -> str:
    """Create a new RGB image in batch GIMP and save it as PNG under output/gimp/.

    background is white/transparent/background. Returns the saved file path.
    """
    try:
        out = OUT_DIR / "gimp" / f"{name}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        bridge.run_batch([bridge.build_create(width, height, background, out)])
        return json.dumps({"ok": True, "file": str(out),
                           "size": [int(width), int(height)], "background": background})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def gimp_run_script(script: str, image: str | None = None,
                    output: str | None = None) -> str:
    """Run an .scm preset (prep_image, export_png) against an image file, or inline Script-Fu.

    Presets take %%INPUT%%/%%OUTPUT%% slots filled from image/output; export_png
    requires a .png output. The console output comes back, so the result is read,
    not assumed. Batch GIMP opens the file, runs, saves, and quits.
    """
    try:
        if script == "export_png" and output is not None \
                and not str(output).lower().endswith(".png"):
            return json.dumps({"ok": False,
                               "error": "export_png needs a .png output path"})
        return json.dumps(bridge.run_script(script, image=image, output=output))
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def gimp_make_texture(preset: str, size: int = 256, seed: int = 0) -> str:
    """Synthesize a tileable texture trio (albedo/rough/normal PNGs) headlessly.

    Stdlib only — GIMP never opens. Presets: stone, wood_grain, noise.
    Pair with prep_image/export_png inside GIMP for finishing touches.
    """
    try:
        d = OUT_DIR / "textures"
        r = make_texture(preset, size=size, seed=seed, out_dir=d)
        return json.dumps(r)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e),
                           "presets": list(PRESETS)})
