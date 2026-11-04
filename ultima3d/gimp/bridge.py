"""GIMP backend client (stdlib only): batch Script-Fu plus a server probe.

GIMP ships a real CLI, so scripting goes through batch mode:
`gimp --new-instance -i --batch-interpreter=plug-in-script-fu-eval [--quit]
-b '<script-fu>'` (`--quit` is GIMP 3+, which otherwise stays resident as a
background process; 2.x exits via `(gimp-quit 0)`). Batch runs to completion with an
exit code — a failure arrives as text on stderr, and a hung GIMP is killed past
the deadline. Nothing here can wedge the editor the way a socket bridge could.
`live()` probes 127.0.0.1:10008 for a user-started Script-Fu server.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

EXE_ENV = "ULTIMA3D_GIMP"
PORT_ENV = "ULTIMA3D_GIMP_PORT"
DEFAULT_PORT = 10008
SCRIPTS_ENV = "ULTIMA3D_GIMP_SCRIPTS"

_PACKAGE_DIR = Path(__file__).resolve().parent      # ultima3d/gimp
_REPO_ROOT = _PACKAGE_DIR.parents[1]                # repo root


def scripts_root() -> Path:
    """Where the Script-Fu sources live: env override, repo `gimp/`, then packaged."""
    override = os.environ.get(SCRIPTS_ENV)
    if override:
        return Path(override)
    repo = _REPO_ROOT / "gimp"
    if (repo / "scripts").is_dir():
        return repo
    return _PACKAGE_DIR


SCRIPT_DIR = scripts_root() / "scripts"

PRESETS = sorted(p.stem for p in SCRIPT_DIR.glob("*.scm")) if SCRIPT_DIR.is_dir() else []


def exe_candidates() -> list[Path]:
    """GIMP executables, most to least preferred. Survives 2.x/3.x renames."""
    override = os.environ.get(EXE_ENV)
    if override:
        return [Path(override)]
    found: list[Path] = []
    patterns = ["C:/Program Files/GIMP*/bin/gimp*.exe",
                "C:/Program Files (x86)/GIMP*/bin/gimp*.exe"]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        patterns.append(local.replace("\\", "/") + "/Programs/GIMP*/bin/gimp*.exe")
    for pattern in patterns:
        found += [Path(m) for m in glob.glob(pattern)]
    found.sort(key=lambda p: ("console" not in p.name.lower(), p.name.lower()))
    for name in ("gimp", "gimp-2.10", "gimp-3.0", "gimp-console-2.10"):
        which = shutil.which(name)
        if which:
            found.append(Path(which))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in found:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def exe_path() -> Path | None:
    """First existing GIMP executable, or None when GIMP is not installed."""
    for candidate in exe_candidates():
        if candidate.is_file():
            return candidate
    return None


def exe_exists() -> bool:
    return exe_path() is not None


def port() -> int:
    try:
        return int(os.environ.get(PORT_ENV, DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def version(timeout: float = 60.0) -> str | None:
    """`gimp --version` first line, e.g. 'GNU Image Manipulation Program version 3.0.4'."""
    exe = exe_path()
    if exe is None:
        return None
    try:
        r = subprocess.run([str(exe), "--version"],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    lines = (r.stdout or r.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else None


_GIMP_MAJOR: int | None = None


def _major(timeout: float = 60.0) -> int:
    """GIMP major version, probed once; assumes 3 when the probe says nothing."""
    global _GIMP_MAJOR
    if _GIMP_MAJOR is None:
        m = re.search(r"version\s+(\d+)", version(timeout=timeout) or "")
        _GIMP_MAJOR = int(m.group(1)) if m else 3
    return _GIMP_MAJOR


def live(timeout: float = 1.5) -> bool:
    """Is a Script-Fu server answering on 127.0.0.1:PORT? Connect-and-close only."""
    import socket

    try:
        s = socket.create_connection(("127.0.0.1", port()), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def run_batch(snippets: list[str], timeout: float = 180.0) -> dict:
    """Run Script-Fu snippets in a throwaway batch GIMP; return console output.

    Raises RuntimeError when GIMP is absent, when the run times out (the process
    is killed), or when GIMP exits nonzero — with stderr attached so the failure
    is readable instead of silent.
    """
    exe = exe_path()
    if exe is None:
        raise RuntimeError(f"GIMP not found; set {EXE_ENV} to a gimp exe")
    cmd = [str(exe), "--new-instance", "-i",
           "--batch-interpreter=plug-in-script-fu-eval"]
    if _major() >= 3:
        cmd.append("--quit")
    for snippet in snippets:
        cmd += ["-b", snippet]
    cmd += ["-b", "(gimp-quit 0)"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"GIMP batch timed out after {timeout:.0f}s and was killed") from None
    except OSError as e:
        raise RuntimeError(f"failed to launch GIMP: {e}") from e
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[-2000:]
        raise RuntimeError(f"GIMP batch failed (exit {r.returncode}): {err}")
    return {"ok": True,
            "stdout": (r.stdout or "")[-4000:],
            "stderr": (r.stderr or "")[-2000:]}


def _q(path: str | Path) -> str:
    """Quote a filesystem path as a Script-Fu string (forward slashes)."""
    return '"' + str(path).replace("\\", "/").replace('"', '\\"') + '"'


def build_create(width: int, height: int, background: str, out_path: str | Path) -> str:
    """Script-Fu that creates a new RGB image and saves it as PNG."""
    fills = {"white": "(255 255 255)", "background": "(200 200 200)"}
    key = str(background).lower()
    if key not in ("white", "transparent", "background"):
        raise ValueError(f"unknown background {background!r}; choose white/transparent/background")
    w, h = int(width), int(height)
    if not (1 <= w <= 10000 and 1 <= h <= 10000):
        raise ValueError("width/height must each be 1..10000")
    # Fill types as numbers: GIMP 3 script-fu never registered the *-FILL
    # constants (BACKGROUND-FILL et al. are unbound there). Values are stable
    # across 2.x/3.x: 0 foreground, 1 background, 2 white, 3 transparent.
    if key == "transparent":
        fill = "(gimp-drawable-fill layer 3)"
    else:
        fill = (f"(gimp-context-set-background '{fills[key]}) "
                "(gimp-drawable-fill layer 1)")
    if _major() >= 3:
        # 3.x moved the layer name to argument 2 and dropped the drawable
        # from gimp-file-save.
        layer_new = (f'(gimp-layer-new img "background" {w} {h} RGBA-IMAGE '
                       '100 LAYER-MODE-NORMAL)')
        save = (f"(gimp-file-save RUN-NONINTERACTIVE img {_q(out_path)} "
                f"{_q(Path(out_path).name)})")
    else:
        layer_new = (f'(gimp-layer-new img {w} {h} RGBA-IMAGE "background" '
                       '100 LAYER-MODE-NORMAL)')
        save = (f"(gimp-file-save RUN-NONINTERACTIVE img layer {_q(out_path)} "
                f"{_q(Path(out_path).name)})")
    return (f"(let* ((img (car (gimp-image-new {w} {h} RGB))) "
            f"(layer (car {layer_new}))) "
            f"(gimp-image-insert-layer img layer 0 -1) {fill} {save} "
            f"(gimp-image-delete img))")


def wrap_preset(name: str, image: str | Path | None,
                output: str | Path | None) -> tuple[str, str]:
    """Fill a bundled .scm preset's %%INPUT%%/%%OUTPUT%% slots. Pure: no GIMP runs."""
    candidate = SCRIPT_DIR / f"{name}.scm"
    if not candidate.is_file():
        raise ValueError(f"unknown GIMP preset {name!r}; choose from {PRESETS}")
    code = candidate.read_text(encoding="utf-8", errors="replace")
    needs_in = "%%INPUT%%" in code
    needs_out = "%%OUTPUT%%" in code
    if needs_in and not image:
        raise ValueError(f"preset {name} needs an input image path")
    if needs_out and not output:
        raise ValueError(f"preset {name} needs an output path")
    if needs_in:
        code = code.replace("%%INPUT%%", _q(image))  # type: ignore[arg-type]
    if needs_out:
        code = code.replace("%%OUTPUT%%", _q(output))  # type: ignore[arg-type]
    # GIMP 3 renamed the query: gimp-image-get-base-type vs 2.x gimp-image-base-type.
    code = code.replace("%%BASETYPE%%",
                        "gimp-image-get-base-type" if _major() >= 3
                        else "gimp-image-base-type")
    # GIMP 3 dropped the drawable from gimp-file-save.
    if _major() >= 3:
        save = (f"(gimp-image-flatten img) (gimp-file-save RUN-NONINTERACTIVE img "
                f"{_q(output)} {_q(Path(output).name)})")  # type: ignore[arg-type]
    else:
        save = ("(let ((layer (car (gimp-image-flatten img)))) (gimp-file-save "
                f"RUN-NONINTERACTIVE img layer {_q(output)} "  # type: ignore[arg-type]
                f"{_q(Path(output).name)})")  # type: ignore[arg-type]
    code = code.replace("%%FLATTENSAVE%%", save)
    return code, f"preset:{name}"


def run_script(name_or_code: str, image: str | Path | None = None,
               output: str | Path | None = None, timeout: float = 180.0) -> dict:
    """Run a bundled .scm preset (by stem) or inline Script-Fu in batch GIMP.

    Returns the console output, so the result comes back instead of being
    assumed. Refuses code that never touches GIMP instead of launching blind.
    """
    candidate = SCRIPT_DIR / f"{name_or_code}.scm"
    if candidate.is_file():
        code, source = wrap_preset(name_or_code, image, output)
    else:
        code, source = name_or_code, "inline"
    if "gimp-" not in code:
        raise ValueError("refusing to launch GIMP with code that never touches gimp- procedures")
    out = run_batch([code], timeout=timeout)
    return {"ok": True, "source": source, "stdout": out["stdout"]}


def status() -> dict:
    exe = exe_path()
    return {
        "ok": True,
        "exe": str(exe) if exe else None,
        "exe_present": exe is not None,
        "version": version(timeout=30),
        "live": live(),
        "port": port(),
        "scripts": PRESETS,
    }
