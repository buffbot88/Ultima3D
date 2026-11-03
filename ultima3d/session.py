"""Session budget: one server run is capped at ULTIMA3D_SESSION_MINUTES (default 40).

An agent loop has no natural end, so the server does not trust one to stop: the
budget is armed at startup, every tool call is refused past the deadline, and a
watchdog thread terminates the process (killing the Blender worker) when it lands.
Restarting the server is the only way to begin a new session.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

DEFAULT_MINUTES = 40.0
ENV_MINUTES = "ULTIMA3D_SESSION_MINUTES"


class SessionExpired(RuntimeError):
    """Raised when a call arrives after the session budget is spent."""


class SessionBudget:
    """Wall-clock budget for one server process (started once, then enforced)."""

    def __init__(self, minutes: float | None = None, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.limit_minutes = self._resolve(minutes)
        self.started_at: float | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _resolve(minutes: float | None) -> float:
        """Take the argument, else the environment, else the 40-minute default."""
        raw = minutes if minutes is not None else os.environ.get(ENV_MINUTES)
        if raw is None or raw == "":
            return DEFAULT_MINUTES
        try:
            return max(float(raw), 0.0)
        except (TypeError, ValueError):
            return DEFAULT_MINUTES

    @property
    def limit_seconds(self) -> float:
        return self.limit_minutes * 60.0

    def set_limit_minutes(self, minutes: float) -> None:
        """Override the limit (used by --session-minutes before the clock starts)."""
        with self._lock:
            self.limit_minutes = max(float(minutes), 0.0)

    def start(self, now: float | None = None) -> float:
        """Start the clock once; later calls are no-ops so restarts cannot extend it."""
        with self._lock:
            if self.started_at is None:
                self.started_at = self.clock() if now is None else float(now)
            return self.started_at

    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        return max(self.clock() - self.started_at, 0.0)

    def remaining_seconds(self) -> float:
        if self.started_at is None:
            return self.limit_seconds
        return max(self.limit_seconds - self.elapsed_seconds(), 0.0)

    def expired(self) -> bool:
        return self.started_at is not None and self.remaining_seconds() <= 0.0

    def message(self) -> str:
        return (
            f"session budget exhausted: this server has run {self.elapsed_seconds() / 60:.1f} of "
            f"{self.limit_minutes:g} minutes and stops automatically. Nothing more will be built or "
            "rendered. Report what exists now; a new session needs an explicit restart "
            "(python -m ultima3d.skill stop, then the next call starts a fresh server)."
        )

    def check(self, op: str = "call") -> None:
        """Raise SessionExpired once the budget is spent."""
        if self.expired():
            raise SessionExpired(f"{op}: {self.message()}")

    def snapshot(self) -> dict:
        """Director-facing view of the budget for status tools."""
        return {
            "limit_minutes": round(self.limit_minutes, 3),
            "started": self.started_at is not None,
            "elapsed_seconds": round(self.elapsed_seconds(), 1),
            "remaining_seconds": round(self.remaining_seconds(), 1),
            "expired": self.expired(),
        }

    def enforce(self, on_expire: Callable[[], None]) -> threading.Thread | None:
        """Run on_expire once the deadline passes, in a daemon watchdog thread."""
        if self.started_at is None:
            self.start()
        if self.limit_seconds <= 0:
            return None

        def watch() -> None:
            while True:
                left = self.remaining_seconds()
                if left <= 0:
                    break
                time.sleep(min(left, 5.0))
            on_expire()

        thread = threading.Thread(target=watch, name="ultima3d-session-watchdog", daemon=True)
        thread.start()
        return thread


SESSION = SessionBudget()
