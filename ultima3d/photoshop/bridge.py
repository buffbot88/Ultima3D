"""CS3 bridge client (stdlib only): locate the exe, probe COM briefly, launch JSX.

Nothing here blocks on the Photoshop GUI. COM probing runs in a thread with a
short timeout because Dispatch() will try to spin up the GUI app; script
launches use Popen without waiting.
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import tempfile
from pathlib import Path

EXE_ENV = "ULTIMA3D_PHOTOSHOP"
DEFAULT_EXE = (
    r"C:\Users\buffb\Desktop\GitHub\Adobe Photoshop CS3 Portable\App\Photoshop.exe"
)
PORT_ENV = "ULTIMA3D_PHOTOSHOP_PORT"
DEFAULT_PORT = 8767
DAEMON_SCRIPT = (Path(__file__).resolve().parent / "daemon" / "ultima3d_bridge.jsx")

SCRIPT_DIR = Path(__file__).resolve().parent / "scripts"

PRESETS = sorted(p.stem for p in SCRIPT_DIR.glob("*.jsx")) if SCRIPT_DIR.is_dir() else []


def exe_path() -> Path:
    return Path(os.environ.get(EXE_ENV, DEFAULT_EXE))


def exe_exists() -> bool:
    return exe_path().is_file()


def _com_version() -> str | None:
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return None
    try:
        app = win32com.client.Dispatch("Photoshop.Application")
        return str(app.Version)
    except Exception:
        return None


def com_version(timeout: float = 8.0) -> str | None:
    """Best-effort COM probe; None when CS3 is unreachable (portable builds are)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_com_version)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return None


def port() -> int:
    try:
        return int(os.environ.get(PORT_ENV, DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def live(timeout: float = 1.5) -> bool:
    """Quick connect test: is the bridge script running inside Photoshop?"""
    import socket

    try:
        s = socket.create_connection(("127.0.0.1", port()), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def call(code: str | None = None, op: str = "exec", timeout: float = 60.0) -> dict:
    """Run ExtendScript in the open Photoshop via the bridge; return its reply.

    Raises RuntimeError with next-step instructions when the bridge script is
    not running, or when Photoshop itself reports an ExtendScript error.
    """
    import json
    import socket

    p = port()
    req = json.dumps({"id": 1, "op": op, "code": code or ""})
    try:
        s = socket.create_connection(("127.0.0.1", p), timeout=min(timeout, 10.0))
    except OSError:
        raise RuntimeError(
            f"Photoshop bridge is not listening on 127.0.0.1:{p}. In the open "
            f"Photoshop: File > Scripts > Browse > {DAEMON_SCRIPT}") from None
    with s:
        s.settimeout(timeout)
        s.sendall(req.encode("utf-8"))
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        buf = bytearray()
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    raw = bytes(buf).decode("utf-8", "replace")
    if not raw.strip():
        raise RuntimeError(
            "empty reply from the Photoshop bridge — reload the current daemon: "
            f"quit the old bridge, then File > Scripts > Browse > {DAEMON_SCRIPT}") from None
    try:
        resp = json.loads(raw)
    except ValueError:
        raise RuntimeError(f"unreadable reply from the Photoshop bridge: {raw[:120]!r}") from None
    if not resp.get("ok"):
        raise RuntimeError(f"ExtendScript error: {resp.get('error')}")
    return resp


FILLS = {
    "white": "DocumentFill.WHITE",
    "transparent": "DocumentFill.TRANSPARENT",
    "background": "DocumentFill.BACKGROUNDCOLOR",
}


def build_create(width: int, height: int, background: str = "white",
                 name: str = "ultima3d") -> str:
    """ExtendScript that creates a new RGB document and reports it back."""
    fill = FILLS.get(str(background).lower())
    if fill is None:
        raise ValueError(f"unknown background {background!r}; choose from {sorted(FILLS)}")
    w, h = int(width), int(height)
    if not (1 <= w <= 10000 and 1 <= h <= 10000):
        raise ValueError("width/height must each be 1..10000")
    safe = "".join(c for c in str(name) if c.isalnum() or c in "_-") or "ultima3d"
    return (f'var d = app.documents.add({w}, {h}, 72, "{safe}", '
            f'NewDocumentMode.RGB, {fill}); '
            f'"created " + d.name + " " + d.width + "x" + d.height')


def status() -> dict:
    exe = exe_path()
    return {
        "ok": True,
        "exe": str(exe),
        "exe_present": exe.is_file(),
        "com_version": com_version(),
        "com_note": "portable CS3 registers no COM server; live control uses the bridge script",
        "live": live(),
        "port": port(),
        "bridge_script": str(DAEMON_SCRIPT),
        "scripts": PRESETS,
    }


def run_script(name_or_code: str, timeout: float = 8.0) -> dict:
    """Fire-and-forget a JSX preset (by stem) or inline code via Photoshop.exe.

    Returns the staged script path and whether the GUI was launched; never
    waits for Photoshop to finish, because portable CS3 has no script-done
    signal over this channel.
    """
    exe = exe_path()
    if not exe.is_file():
        raise RuntimeError(f"Photoshop.exe not found at {exe}; set {EXE_ENV}")
    candidate = SCRIPT_DIR / f"{name_or_code}.jsx"
    if candidate.is_file():
        code = candidate.read_text(encoding="utf-8", errors="replace")
        source = f"preset:{candidate.stem}"
    else:
        code = name_or_code
        source = "inline"
    if "app." not in code and "activeDocument" not in code:
        raise ValueError("refusing to launch Photoshop with code that never touches app/activeDocument")
    fd, tmppath = tempfile.mkstemp(prefix="ultima3d_", suffix=".jsx")
    with open(fd, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        subprocess.Popen([str(exe), tmppath])  # noqa: S603 — exe path is user-configured
    except OSError as e:
        raise RuntimeError(f"failed to launch Photoshop.exe: {e}") from e
    return {"launched": True, "source": source, "script": tmppath}
