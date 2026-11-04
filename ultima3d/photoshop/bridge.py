"""CS3 bridge client (stdlib only): locate the exe, probe COM briefly, drive JSX.

Nothing here blocks on the Photoshop GUI. COM probing runs in a thread with a
short timeout because Dispatch() will try to spin up the GUI app, and every
script runs through the live bridge inside Photoshop — CS3 cannot be handed a
script on the command line. The bridge speaks one JSON object per line, and the
health check is a real ping, so a wedged bridge is reported instead of assumed.
"""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path

EXE_ENV = "ULTIMA3D_PHOTOSHOP"
DEFAULT_EXE = (
    r"C:\Users\buffb\Desktop\GitHub\Adobe Photoshop CS3 Portable\App\Photoshop.exe"
)
PORT_ENV = "ULTIMA3D_PHOTOSHOP_PORT"
DEFAULT_PORT = 8767
SCRIPTS_ENV = "ULTIMA3D_PHOTOSHOP_SCRIPTS"

_PACKAGE_DIR = Path(__file__).resolve().parent      # ultima3d/photoshop
_REPO_ROOT = _PACKAGE_DIR.parents[1]                # repo root


def scripts_root() -> Path:
    """Where the JSX sources live: env override, repo `photoshop/`, then packaged.

    The repo-root tree is the single source of truth — the packaged copy this
    module used to read drifted from the one the README told users to run.
    """
    override = os.environ.get(SCRIPTS_ENV)
    if override:
        return Path(override)
    repo = _REPO_ROOT / "photoshop"
    if (repo / "daemon" / "ultima3d_bridge.jsx").is_file():
        return repo
    return _PACKAGE_DIR


DAEMON_SCRIPT = scripts_root() / "daemon" / "ultima3d_bridge.jsx"
SCRIPT_DIR = scripts_root() / "scripts"

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


def _read_line(sock, limit: int = 1_000_000) -> bytes:
    """Read one newline-terminated frame (or whatever arrives before close)."""
    buf = bytearray()
    while b"\n" not in buf and len(buf) < limit:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    return bytes(buf).split(b"\n", 1)[0]


def live(timeout: float = 1.5) -> bool:
    """Is the bridge script running inside Photoshop? Asks with a real ping.

    The old check connected and closed without sending anything; the bridge then
    blocked in read() on that empty socket and Photoshop froze. A bare connect is
    now harmless, but only an answered ping proves the bridge is healthy.
    """
    import json
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port()), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(json.dumps({"id": 0, "op": "ping"}).encode("utf-8") + b"\n")
            raw = _read_line(s).decode("utf-8", "replace")
    except OSError:
        return False
    if '"ok":true' not in raw.replace(" ", ""):
        return False
    return True


def call(code: str | None = None, op: str = "exec", timeout: float = 60.0) -> dict:
    """Run ExtendScript in the open Photoshop via the bridge; return its reply.

    Raises RuntimeError with next-step instructions when the bridge script is
    not running, does not answer, or when Photoshop reports an ExtendScript
    error. The reply is one JSON line, so a timeout here means the bridge is
    wedged rather than slow.
    """
    import json
    import socket

    p = port()
    req = json.dumps({"id": 1, "op": op, "code": code or ""}).encode("utf-8") + b"\n"
    try:
        s = socket.create_connection(("127.0.0.1", p), timeout=min(timeout, 10.0))
    except OSError:
        raise RuntimeError(
            f"Photoshop bridge is not listening on 127.0.0.1:{p}. In the open "
            f"Photoshop: File > Scripts > Browse > {DAEMON_SCRIPT}") from None
    with s:
        s.settimeout(timeout)
        try:
            s.sendall(req)
            raw = _read_line(s).decode("utf-8", "replace")
        except OSError as e:
            raise RuntimeError(
                f"the Photoshop bridge did not answer within {timeout:.0f}s ({e}). "
                f"Quit the old bridge and reload it: File > Scripts > Browse > "
                f"{DAEMON_SCRIPT}") from None
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


def run_script(name_or_code: str, timeout: float = 60.0) -> dict:
    """Run a JSX preset (by stem) or inline ExtendScript in the open Photoshop.

    Goes through the live bridge, so the result comes back like photoshop_exec.
    Portable CS3 has no COM server and Photoshop.exe cannot be handed a script
    as an argument — launching it that way is what wedged the app — so this
    refuses with the setup step instead of starting a second Photoshop.
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
    resp = call(code, timeout=timeout)
    return {"ok": True, "source": source, "result": resp.get("result")}
