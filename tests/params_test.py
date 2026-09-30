"""Recipe/refine parameter reporting, driven through the real MCP server over stdio.

Builders all end in `**_`, so a misspelled recipe parameter used to be dropped without
complaint and the asset was built with the default — success that looks like success. And
`refine_asset` declared a single wrapper argument, so the `{"changes": {...}}` form its own
docstring documents was rejected by the client before reaching the server.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = os.path.join(os.path.dirname(__file__), "..", "ultima3d", "server.py")
BLENDER = os.environ.get("ULTIMA3D_BLENDER")
if not BLENDER:
    print("set ULTIMA3D_BLENDER to a Blender executable")
    sys.exit(2)

failures = []


def check(label, passed, detail=""):
    print(f"  [{'ok' if passed else 'FAIL'}] {label}{f' -- {detail}' if detail else ''}")
    if not passed:
        failures.append(label)


async def main():
    env = dict(os.environ)
    env["ULTIMA3D_OUT"] = str(Path(os.path.dirname(__file__), "..", "output", "params").resolve())
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            async def call(name, args=None):
                res = await s.call_tool(name, args or {})
                text = "".join(getattr(c, "text", "")
                               for c in (getattr(res, "content", None) or [])
                               if getattr(c, "type", "") == "text")
                try:
                    return json.loads(text), bool(getattr(res, "isError", False)), text
                except Exception:
                    return {}, bool(getattr(res, "isError", False)), text

            print("unrecognized recipe parameters:")
            await call("reset_scene")
            r, err, _ = await call("create_3d", {"recipe": {"body": {"builder": "box",
                                                                     "siz": [2, 1, 1]}},
                                                 "name": "typo"})
            ignored = (r.get("ignored_params") or {}).get("body") or {}
            check("typo'd 'siz' is reported as ignored", not err and ignored.get("ignored") == ["siz"],
                  f"ignored_params={r.get('ignored_params')}")
            check("the accepted parameter list is returned with it",
                  "size" in (ignored.get("accepted") or []), f"accepted={ignored.get('accepted')}")
            check("the typo'd size did not apply (stays a 1x1x1 cube)", r.get("triangles") == 12,
                  f"triangles={r.get('triangles')}")

            r, err, _ = await call("create_3d", {"recipe": {"body": {"builder": "box",
                                                                    "size": [2, 1, 1]}},
                                                 "name": "spelled"})
            check("a correctly spelled recipe reports nothing ignored",
                  not err and r.get("ignored_params") == {}, f"ignored_params={r.get('ignored_params')}")

            r, err, _ = await call("create_3d", {"recipe": {"body": {"builder": "sphere",
                                                                    "detail": "game"}},
                                                 "name": "detail_ok"})
            check("per-node 'detail' is not mistaken for an unknown parameter",
                  not err and r.get("ignored_params") == {}, f"ignored_params={r.get('ignored_params')}")

            r, err, _ = await call("create_3d", {"recipe": {"body": {"builder": "box",
                                                                    "triangle_budget": 500}},
                                                 "name": "budget_ok"})
            check("recipe-level 'triangle_budget' is not mistaken for an unknown parameter",
                  not err and r.get("ignored_params") == {}, f"ignored_params={r.get('ignored_params')}")

            print("\nrefine_asset argument shape (the form its docstring documents):")
            await call("reset_scene")
            await call("create_3d", {"recipe": {"body": {"builder": "barrel", "radius": 0.45,
                                                         "height": 0.9}},
                                     "name": "barrel"})
            before, _, _ = await call("inspect_asset")
            r, err, text = await call("refine_asset", {"changes": {"body.height": 1.4}})
            check("refine_asset accepts {'changes': {...}} without a wrapper",
                  not err, text[:200])
            check("the change is reported as applied", r.get("applied") == ["body.height"],
                  f"applied={r.get('applied')} ignored={r.get('ignored')}")
            after, _, _ = await call("inspect_asset")
            check("the geometry actually changed",
                  (after.get("dimensions") or [0, 0, 0])[2] > (before.get("dimensions") or [0, 0, 0])[2],
                  f"{before.get('dimensions')} -> {after.get('dimensions')}")

            print("\nrefine_asset honesty about parameters the builder rejects:")
            r, err, text = await call("refine_asset", {"changes": {"body.scale_z": 1.35,
                                                                   "body.radius": 0.6}})
            check("a parameter the builder does not accept is not claimed as applied",
                  not err and "body.scale_z" in (r.get("ignored") or []),
                  f"applied={r.get('applied')} ignored={r.get('ignored')}")
            check("it is not listed under applied either",
                  "body.scale_z" not in (r.get("applied") or []), f"applied={r.get('applied')}")
            check("a valid change in the same call still applies",
                  "body.radius" in (r.get("applied") or []) and r.get("applied") == ["body.radius"],
                  f"applied={r.get('applied')}")


asyncio.run(main())

if failures:
    print(f"\nPARAM TEST FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nPARAM TEST OK")
