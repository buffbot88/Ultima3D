"""Ultima3D system-tray controller (Windows, stdlib + pywin32 only).

Run:  python -m ultima3d.tray        (from the project root)

A tray icon owns the MCP server subprocess (python -m ultima3d.server with
stdin held open, the same arrangement as the documented background setup):

  menu:   Start / Stop / Restart / Open output folder / Quit
  icon:   gray = stopped | green = online | amber = busy | blue = idle
  chime:  a two-tone beep when the server goes quiet for IDLE_CHIME_SECS
          (default 120s) after having been busy — i.e. a job just finished.

Status model (no MCP traffic snooping needed):
  stopped  server process not running.
  busy     the blender worker child is currently burning CPU (a build,
           render, bake or validate is actually running).
  online   server running, worker quiet, but there was activity recently.
  idle     server running, worker quiet for IDLE_CHIME_SECS or more.

Busy detection samples the blender.exe child's kernel+user CPU time via
win32process.GetProcessTimes; anything above BUSY_CPU_PCT within a poll
window counts as working. The idle chime fires once per busy->idle
transition (re-armed by the next busy period), never on a server that was
simply started and left alone.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

IDLE_CHIME_SECS = 120.0
POLL_SECS = 5.0
BUSY_CPU_PCT = 5.0

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(os.environ.get("ULTIMA3D_OUT", ROOT / "output"))
LOG_FILE = OUT_DIR / "tray.log"


def log(msg: str) -> None:
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Status model: pure logic, no Windows calls (unit-tested).
# ---------------------------------------------------------------------------

STOPPED, ONLINE, BUSY, IDLE = "stopped", "online", "busy", "idle"


class StatusTracker:
    """busy/cpu samples in, status + chime events out."""

    def __init__(self, idle_secs: float = IDLE_CHIME_SECS):
        self.idle_secs = idle_secs
        self.status = STOPPED
        self.last_busy = 0.0
        self.chime_armed = False  # set by busy, fired once on idle
        self.wants_chime = False

    def server_down(self) -> str:
        self.status = STOPPED
        self.chime_armed = False
        self.wants_chime = False
        return self.status

    def sample(self, running: bool, worker_busy: bool, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        if not running:
            return self.server_down()
        if worker_busy:
            self.status = BUSY
            self.last_busy = now
            self.chime_armed = True
            self.wants_chime = False
        elif self.last_busy and now - self.last_busy >= self.idle_secs:
            self.status = IDLE
            if self.chime_armed:
                self.wants_chime = True
                self.chime_armed = False
        else:
            self.status = ONLINE
        return self.status

    def take_chime(self) -> bool:
        c, self.wants_chime = self.wants_chime, False
        return c


# ---------------------------------------------------------------------------
# Icons: 32x32 .ico files generated in stdlib (no Pillow).
# ---------------------------------------------------------------------------

ICON_COLORS = {
    STOPPED: (128, 128, 128),
    ONLINE: (46, 160, 67),
    BUSY: (230, 150, 20),
    IDLE: (50, 120, 220),
}


def _circle_ico(rgb: tuple[int, int, int], size: int = 32) -> bytes:
    r, g, b = rgb
    px = bytearray()
    mask = bytearray()
    rad = size / 2 - 2
    bits = 0
    cur = 0
    for y in range(size):
        for x in range(size):
            dx, dy = x - size / 2 + 0.5, y - size / 2 + 0.5
            inside = dx * dx + dy * dy <= rad * rad
            px += bytes((b, g, r, 255 if inside else 0))
            cur = (cur << 1) | (0 if inside else 1)
            bits += 1
            if bits == 8:
                mask.append(cur)
                bits, cur = 0, 0
        if bits:
            cur <<= 8 - bits
            mask.append(cur)
            bits, cur = 0, 0
    row_pad = (size * 4) % 4
    bh = struct.pack("<IIIHHIIIIII", 40, size, size * 2, 1, 32, 0,
                     len(px), 0, 0, 0, 0)
    bmp = bh + bytes(px) + bytes(mask)
    ofs = 6 + 16
    return (struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32,
            len(bmp), ofs) + bmp)


def icon_files(directory: Path | None = None) -> dict[str, str]:
    directory = directory or Path(tempfile.gettempdir()) / "ultima3d_tray"
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, rgb in ICON_COLORS.items():
        p = directory / f"ultima3d_{name}.ico"
        if not p.is_file():
            p.write_bytes(_circle_ico(rgb))
        paths[name] = str(p)
    return paths


# ---------------------------------------------------------------------------
# Server process management.
# ---------------------------------------------------------------------------

class ServerManager:
    """Owns one `python -m ultima3d.server` child with stdin held open."""

    def __init__(self, command: list[str] | None = None, cwd: Path | None = None):
        self.command = command or [sys.executable, "-m", "ultima3d.server"]
        self.cwd = str(cwd or ROOT)
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        with self._lock:
            if self.is_running():
                return True
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            logf = open(OUT_DIR / "server_run.log", "ab", buffering=0)  # noqa: PTH123
            self.proc = subprocess.Popen(
                self.command, cwd=self.cwd,
                stdin=subprocess.PIPE, stdout=logf, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            log(f"tray: server started pid={self.proc.pid}")
            return True

    def stop(self) -> bool:
        with self._lock:
            proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return True
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        log("tray: server stopped")
        return True

    def restart(self) -> bool:
        self.stop()
        time.sleep(1.0)
        return self.start()

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def pid(self) -> int | None:
        return self.proc.pid if self.is_running() else None


# ---------------------------------------------------------------------------
# Worker-busy sampling via win32process (pywin32); stdlib fallback = quiet.
# ---------------------------------------------------------------------------

try:
    import win32process  # type: ignore
    HAS_WIN32PROC = True
except ImportError:
    HAS_WIN32PROC = False


class WorkerSampler:
    """CPU-% of the blender worker child of a server pid,aktan via GetProcessTimes."""

    def __init__(self):
        self._prev: dict[int, tuple[float, float]] = {}

    def _times(self, pid: int) -> tuple[float, float] | None:
        if not HAS_WIN32PROC:
            return None
        try:
            import win32api  # type: ignore
            import win32con  # type: ignore
            h = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
        except Exception:
            return None
        try:
            _, _, kernel, user = win32process.GetProcessTimes(h)
            to_s = lambda ft: ft.dwHighDateTime * 429.4967296 + ft.dwLowDateTime / 1e7  # noqa: E731
            return to_s(kernel), to_s(user)
        except Exception:
            return None
        finally:
            try:
                import win32api  # type: ignore
                win32api.CloseHandle(h)
            except Exception:
                pass

    def _blender_children(self, parent_pid: int) -> list[int]:
        """Direct blender.exe children of parent_pid via a Toolhelp snapshot."""
        import ctypes
        from ctypes import wintypes

        class PE(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("cntThreads", ctypes.c_ulong),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD),
                        ("szExeFile", wintypes.CHAR * 260)]

        kernel32 = ctypes.windll.kernel32
        snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snap == -1:
            return []
        out = []
        try:
            pe = PE()
            pe.dwSize = ctypes.sizeof(PE)
            if not kernel32.Process32First(snap, ctypes.byref(pe)):
                return []
            while True:
                name = pe.szExeFile.split(b"\x00")[0].decode("ascii", "ignore")
                if (name.lower() == "blender.exe"
                        and pe.th32ParentProcessID == parent_pid):
                    out.append(pe.th32ProcessID)
                if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                    break
        finally:
            kernel32.CloseHandle(snap)
        return out

    def busy_children_cpu(self, parent_pid: int) -> float:
        """Max CPU-% across live blender.exe children; 0.0 when unknown/quiet."""
        if not HAS_WIN32PROC or parent_pid is None:
            return 0.0
        try:
            children = self._blender_children(parent_pid)
        except Exception:
            return 0.0
        return self._max_cpu(children)

    def _max_cpu(self, pids: list[int]) -> float:
        now = time.monotonic()
        best = 0.0
        seen = set()
        for pid in pids:
            t = self._times(pid)
            if t is None:
                continue
            seen.add(pid)
            prev = self._prev.get(pid)
            self._prev[pid] = (now, t[0] + t[1])
            if prev is None:
                continue
            dt = now - prev[0]
            if dt > 0:
                best = max(best, (t[0] + t[1] - prev[1]) / dt * 100.0)
        for pid in list(self._prev):
            if pid not in seen:
                del self._prev[pid]
        return best


# ---------------------------------------------------------------------------
# Chime: two-tone beep, winsound on Windows.
# ---------------------------------------------------------------------------

def chime() -> None:
    try:
        import winsound  # type: ignore
        winsound.Beep(880, 220)
        time.sleep(0.12)
        winsound.Beep(1175, 320)
    except Exception:
        pass
    log("tray: idle chime")


# ---------------------------------------------------------------------------
# Win32 tray icon (raw win32gui; single-instance via mutex).
# ---------------------------------------------------------------------------

try:
    import win32api  # type: ignore
    import win32con  # type: ignore
    import win32event  # type: ignore
    import win32gui  # type: ignore
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

WM_TRAY = win32con.WM_USER + 20 if HAS_WIN32 else 0
MENU_STATUS_ID = 1000
MENU_START_ID = 1001
MENU_STOP_ID = 1002
MENU_RESTART_ID = 1003
MENU_LOG_ID = 1004
MENU_QUIT_ID = 1005
STATUS_TEXT = {STOPPED: "Status: stopped", ONLINE: "Status: online",
               BUSY: "Status: busy", IDLE: "Status: idle"}


class TrayApp:
    def __init__(self, manager: ServerManager | None = None,
                 idle_secs: float = IDLE_CHIME_SECS):
        self.manager = manager or ServerManager()
        self.tracker = StatusTracker(idle_secs=idle_secs)
        self.sampler = WorkerSampler()
        self.icons = icon_files()
        self.hwnd = None
        self._stop = threading.Event()
        self._status = STOPPED

    # -- window + icon plumbing ----------------------------------------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        if msg == WM_TRAY:
            if lparam == win32con.WM_RBUTTONUP:
                self._popup()
            elif lparam == win32con.WM_LBUTTONDBLCLK:
                self._toggle()
            return 0
        if msg == win32con.WM_COMMAND:
            self._command(wparam & 0xFFFF)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _popup(self):
        menu = win32gui.CreatePopupMenu()
        running = self.manager.is_running()
        win32gui.AppendMenu(menu, win32con.MF_STRING | win32con.MF_GRAYED,
                            MENU_STATUS_ID, STATUS_TEXT[self._status])
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING
                            | (win32con.MF_GRAYED if running else 0),
                            MENU_START_ID, "Start server")
        win32gui.AppendMenu(menu, win32con.MF_STRING
                            | (0 if running else win32con.MF_GRAYED),
                            MENU_STOP_ID, "Stop server")
        win32gui.AppendMenu(menu, win32con.MF_STRING
                            | (0 if running else win32con.MF_GRAYED),
                            MENU_RESTART_ID, "Restart server")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_LOG_ID,
                            "Open output folder")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_QUIT_ID, "Quit tray")
        pos = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, pos[0], pos[1],
                                0, self.hwnd, None)
        win32gui.DestroyMenu(menu)

    def _command(self, cmd: int) -> None:
        if cmd == MENU_START_ID:
            self.manager.start()
        elif cmd == MENU_STOP_ID:
            self.manager.stop()
        elif cmd == MENU_RESTART_ID:
            self.manager.restart()
        elif cmd == MENU_LOG_ID:
            os.startfile(str(OUT_DIR))  # noqa: S606 -- user-owned output dir
        elif cmd == MENU_QUIT_ID:
            self._stop.set()
            win32gui.DestroyWindow(self.hwnd)

    def _toggle(self) -> None:
        if self.manager.is_running():
            self.manager.stop()
        else:
            self.manager.start()

    def _set_icon(self, status: str) -> None:
        flags = (win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP)
        hicon = win32gui.LoadImage(0, self.icons[status], win32con.IMAGE_ICON,
                                   0, 0, win32con.LR_LOADFROMFILE)
        tip = f"Ultima3D MCP — {status}"
        win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY,
                                  (self.hwnd, 0, flags, WM_TRAY, hicon, tip))
        try:
            win32gui.DestroyIcon(hicon)
        except Exception:
            pass

    # -- background poll loop ------------------------------------------
    def _poll(self) -> None:
        while not self._stop.is_set():
            running = self.manager.is_running()
            busy = False
            if running:
                pid = self.manager.pid()
                try:
                    busy = self.sampler.busy_children_cpu(pid) >= BUSY_CPU_PCT
                except Exception:
                    busy = False
            self._status = self.tracker.sample(running, busy)
            if self.tracker.take_chime():
                chime()
            try:
                self._set_icon(self._status)
            except Exception:
                pass
            self._stop.wait(POLL_SECS)

    # -- entry point ----------------------------------------------------
    def run(self) -> int:
        mutex = win32event.CreateMutex(None, False, "Ultima3D_Tray_Singleton")
        if win32api.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            log("tray: another instance is already running")
            return 1
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "Ultima3DTray"
        wc.lpfnWndProc = self._wndproc  # noqa: SLF001
        win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(wc.lpszClassName, "Ultima3D Tray",
                                          0, 0, 0, 0, 0, 0, 0,
                                          wc.hInstance, None)
        self.manager.start()
        self._status = ONLINE
        self.tracker.sample(True, False)
        self._set_icon(STOPPED if False else ONLINE)
        threading.Thread(target=self._poll, daemon=True,
                         name="tray-poll").start()
        log("tray: started")
        win32gui.PumpMessages()
        self._stop.set()
        self.manager.stop()
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
        except Exception:
            pass
        log("tray: exited")
        return 0


def main(argv: list[str] | None = None) -> int:
    if not HAS_WIN32:
        print("ultima3d.tray needs Windows + pywin32 (win32gui). "
              "Status logic is importable anywhere; the icon is not.",
              file=sys.stderr)
        return 2
    app = TrayApp()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())