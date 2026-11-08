"""Managed skills: registry, enable flags, and the updater.

``skills/registry.json`` is the source of truth: one entry per skill with its
source, license, enabled flag, installed flag and version pin. ``git`` skills
are vendored under ``skills/<id>/`` as sparse checkouts of their upstream
repo; the updater fast-forwards them and records the new commit SHA as the
pin. ``local`` skills (ultima3d itself) need no update. ``manual-node`` skills
(brain) are dropped in by hand and only verified, never fetched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "skills" / "registry.json"


def _run_git(args: list[str], cwd: Path, timeout: float = 300) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def _rmtree(path: Path) -> None:
    """Remove a tree, coping with git's read-only pack files on Windows."""
    def _onerror(func: Any, target: str, _exc: Any) -> None:
        try:
            os.chmod(target, 0o666)
            func(target)
        except OSError:
            pass
    shutil.rmtree(path, onerror=_onerror, ignore_errors=True)


def _valid_checkout(dest: Path) -> bool:
    """A usable git checkout: .git dir with a HEAD (guards half-removed trees)."""
    return (dest / ".git").is_dir() and (dest / ".git" / "HEAD").is_file()


def load_registry(path: Path | str | None = None) -> dict:
    target = Path(path) if path else REGISTRY
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"registry unreadable at {target}: {exc}"}
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        return {"ok": False, "error": f"registry at {target} has no skills list"}
    return {"ok": True, "registry": str(target), **data}


def _save(entries: list[dict], path: Path | None = None) -> Path:
    target = path or REGISTRY
    payload = {"version": 1, "skills": entries}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def _entries(path: Path | None = None) -> tuple[list[dict], Path]:
    loaded = load_registry(path)
    if not loaded.get("ok"):
        raise RuntimeError(loaded.get("error", "registry unreadable"))
    return loaded["skills"], Path(loaded["registry"])


def list_skills(path: Path | None = None) -> dict:
    """Every registered skill with its state; never touches the network."""
    try:
        entries, target = _entries(path)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "registry": str(target), "skills": entries}


def set_enabled(skill_id: str, enabled: bool, path: Path | None = None) -> dict:
    """Flip a skill's enabled flag in the registry and the runtime config."""
    from . import config as _config

    try:
        entries, target = _entries(path)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    for entry in entries:
        if entry.get("id") == skill_id:
            entry["enabled"] = bool(enabled)
            _save(entries, target)
            _config.set_flag("skills", skill_id, bool(enabled))
            return {"ok": True, "id": skill_id, "enabled": bool(enabled)}
    return {"ok": False, "error": f"unknown skill {skill_id!r}"}


def install_skill(skill_id: str, path: Path | None = None, timeout: float = 600) -> dict:
    """Clone (sparse) a git skill's documented paths into skills/<id>/."""
    try:
        entries, target = _entries(path)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    entry = next((e for e in entries if e.get("id") == skill_id), None)
    if entry is None:
        return {"ok": False, "error": f"unknown skill {skill_id!r}"}
    if entry.get("kind") != "git":
        return {"ok": False, "error": f"skill {skill_id!r} is not git-installable (kind={entry.get('kind')})"}
    dest = ROOT / "skills" / skill_id
    if dest.exists():
        _rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = entry["source"]
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", url, str(dest)],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
        # --skip-checks: paths mix directories and files (e.g. AGENTS.md);
        # fresh cone-mode checkouts reject bare file patterns without it.
        sparse = ["git", "sparse-checkout", "set", "--skip-checks",
                  *(entry.get("paths") or ["skills"])]
        _run_git(sparse[1:], dest)
        pin = _run_git(["rev-parse", "HEAD"], dest)
        # `clone --sparse` also leaves every top-level file behind; prune to
        # exactly the vendored entries so the tree stays lean and reviewable.
        keep = {p.split("/")[0] for p in (entry.get("paths") or ["skills"])} | {".git"}
        for child in dest.iterdir():
            if child.name not in keep:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        pass
    except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
        if dest.exists():
            _rmtree(dest)
        return {"ok": False, "id": skill_id, "error": f"clone failed: {exc}"}
    entry["installed"] = True
    entry["pin"] = pin
    _save(entries, target)
    return {"ok": True, "id": skill_id, "installed": str(dest), "pin": pin}


def _remote_head(entry: dict, dest: Path) -> str | None:
    try:
        out = _run_git(["ls-remote", entry["source"], "HEAD"], dest.parent, timeout=60)
        return out.split()[0] if out else None
    except RuntimeError:
        return None


def update_skill(skill_id: str, path: Path | None = None, dry_run: bool = False,
                 timeout: float = 600) -> dict:
    """Fast-forward one skill to its upstream HEAD and record the new pin.

    dry_run reports the installed pin and the remote HEAD without changing
    anything (the offline-safe path the test suite uses).
    """
    try:
        entries, target = _entries(path)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    entry = next((e for e in entries if e.get("id") == skill_id), None)
    if entry is None:
        return {"ok": False, "error": f"unknown skill {skill_id!r}"}
    kind = entry.get("kind")
    if kind == "local":
        return {"ok": True, "id": skill_id, "updated": False,
                "note": "local skill; it versions with this repo"}
    if kind == "manual-node":
        dest = ROOT / "skills" / skill_id
        # The slot ships a README placeholder; only real starter files count.
        present = dest.is_dir() and ((dest / "package.json").is_file()
                                     or (dest / "SKILL.md").is_file())
        entry["installed"] = bool(present)
        _save(entries, target)
        if not present:
            return {"ok": True, "id": skill_id, "updated": False,
                    "note": entry.get("note") or "drop the starter files into skills/brain/"}
        return {"ok": True, "id": skill_id, "updated": False,
                "note": "manual skill present; verify with its own CLI"}
    if kind != "git":
        return {"ok": False, "error": f"skill {skill_id!r} has no updater (kind={kind})"}

    dest = ROOT / "skills" / skill_id
    if not _valid_checkout(dest):
        if dry_run:
            return {"ok": True, "id": skill_id, "updated": False,
                    "note": "not installed; run install first (dry run changes nothing)"}
        return install_skill(skill_id, path, timeout=timeout)

    current = entry.get("pin")
    try:
        current = current or _run_git(["rev-parse", "HEAD"], dest)
    except RuntimeError as exc:
        return {"ok": False, "id": skill_id, "error": str(exc)}
    remote = _remote_head(entry, dest)
    if dry_run or remote is None:
        return {"ok": True, "id": skill_id, "updated": False,
                "pin": current, "remote": remote,
                "note": "dry run" if dry_run else "remote unreachable; kept local pin"}
    if remote == current:
        entry["pin"] = current
        entry["installed"] = True
        _save(entries, target)
        return {"ok": True, "id": skill_id, "updated": False, "pin": current}
    try:
        _run_git(["fetch", "--depth", "1", "origin", "HEAD"], dest, timeout=timeout)
        _run_git(["checkout", "--force", "FETCH_HEAD"], dest, timeout=timeout)
        sparse = entry.get("paths") or ["skills"]
        _run_git(["sparse-checkout", "set", "--skip-checks", *sparse], dest)
        pin = _run_git(["rev-parse", "HEAD"], dest)
    except RuntimeError as exc:
        return {"ok": False, "id": skill_id, "error": str(exc), "pin": current}
    entry["pin"] = pin
    entry["installed"] = True
    _save(entries, target)
    return {"ok": True, "id": skill_id, "updated": True,
            "previous": current, "pin": pin}


def update_all(path: Path | None = None, dry_run: bool = False) -> dict:
    """Update every skill; one failure never blocks the rest."""
    try:
        entries, _ = _entries(path)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    results = [update_skill(e.get("id", ""), path, dry_run=dry_run) for e in entries]
    return {"ok": all(r.get("ok") for r in results), "results": results}


def check_remote_file(url: str, timeout: float = 30) -> dict:
    """HEAD-probe a URL (brain starter link check); no download."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "url": url, "status": resp.status,
                    "type": resp.headers.get("Content-Type")}
    except Exception as exc:  # network down, 404, DNS: all reported, none fatal
        return {"ok": False, "url": url, "error": str(exc)[:200]}
