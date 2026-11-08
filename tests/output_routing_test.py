"""Outputs must land in the invoking project, never the Ultima3D checkout.

skill.ensure_daemon spawns the daemon with cwd=ROOT (so it finds
blender/worker.py). Regression guard: the spawn must carry ULTIMA3D_OUT
pointing at the *invoker's* ./output, so shared-module use (e.g. from a game
project) cannot mix renders into Ultima3D's own tree.
"""

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ultima3d import skill


def _run_spawn(monkey_env):
    """Force a spawn (first /health misses, second hits) and capture Popen kwargs."""
    health_calls = []

    def fake_request(path, addr=None, timeout=None, body=None):
        if path == "/health":
            health_calls.append(1)
            if len(health_calls) == 1:
                return 0, {"ok": False}
            return 200, {"ok": True}
        raise AssertionError(f"unexpected request {path}")

    proc = mock.Mock()
    proc.poll.return_value = None
    proc.pid = 12345
    captured = {}
    # Dashboard boot is unrelated to output routing; keep it out of the capture.
    monkey_env = {**monkey_env, "ULTIMA3D_NO_DASHBOARD": "1"}
    with mock.patch.dict(os.environ, monkey_env, clear=True):
        with mock.patch.object(skill, "request", side_effect=fake_request):
            with mock.patch("subprocess.Popen", side_effect=lambda *a, **k: (
                    captured.update(k) or proc)):
                with mock.patch("pathlib.Path.mkdir"):
                    with mock.patch("builtins.open", mock.mock_open()):
                        skill.ensure_daemon("127.0.0.1:9")
    return captured


def main():
    project_out = str(Path("C:\\fake\\game-project") / "output")

    # Case 1: no ULTIMA3D_OUT — invoker's ./output must be forwarded.
    with mock.patch.object(skill, "out_dir", return_value=Path(project_out)):
        kwargs = _run_spawn({"PATH": os.environ.get("PATH", "")})
    env = kwargs.get("env") or {}
    assert kwargs.get("cwd") == str(skill.ROOT), "daemon must still start from ROOT"
    assert env.get("ULTIMA3D_OUT") == project_out, \
        f"daemon env ULTIMA3D_OUT={env.get('ULTIMA3D_OUT')!r}, want {project_out!r}"
    print(f"case 1 OK: unset env -> forwarded {env['ULTIMA3D_OUT']}")

    # Case 2: explicit ULTIMA3D_OUT wins over the invoker default.
    custom = str(Path("D:\\renders") / "u3d")
    with mock.patch.object(skill, "out_dir",
                           return_value=Path(project_out)):  # out_dir still project-based
        kwargs = _run_spawn({"PATH": os.environ.get("PATH", ""), "ULTIMA3D_OUT": custom})
    env = kwargs.get("env") or {}
    assert env.get("ULTIMA3D_OUT") == custom, \
        f"explicit ULTIMA3D_OUT lost: {env.get('ULTIMA3D_OUT')!r}"
    print(f"case 2 OK: explicit env preserved ({custom})")

    # Case 3: tray headless default follows cwd, not the checkout.
    import importlib
    import ultima3d.tray as tray
    with mock.patch.dict(os.environ, {}, clear=True):
        importlib.reload(tray)
        assert tray.OUT_DIR == Path.cwd() / "output", \
            f"tray default {tray.OUT_DIR} is not cwd-based"
    importlib.reload(tray)  # restore real env binding
    print("case 3 OK: tray default is cwd-based")

    print("OUTPUT ROUTING TEST OK")


if __name__ == "__main__":
    main()
