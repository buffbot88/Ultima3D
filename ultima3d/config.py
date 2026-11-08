"""Runtime configuration: module + skill enable flags.

Defaults live here in code so a fresh checkout works with everything on.
A user override may be written to ``state/config.json`` (or the path in
``ULTIMA3D_CONFIG``); only the keys present there replace the defaults.

Shape:
    {"modules": {"procedural": true, "live": true, "gimp": true, "godot": true},
     "skills": {"ultima3d": true, "ponytail": true, "brain": false, "pullstack": true}}

``procedural`` is the built-in Blender-worker backend (create_3d, refine_asset,
render_asset, validate_asset, finalize_asset, ...). The other three modules
self-register their tools on import, so server.py simply skips the import when
they are off. Procedural tools live in server.py itself, so the daemon refuses
their calls with a clear error instead (see daemon._module_gate).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

MODULES = ("procedural", "live", "gimp", "godot")

DEFAULTS: dict[str, dict[str, bool]] = {
    "modules": {name: True for name in MODULES},
    # Skill flags mirror skills/registry.json; unknown ids are ignored here.
    "skills": {"ultima3d": True, "ponytail": True, "brain": False, "pullstack": True},
}


def config_path() -> Path:
    override = os.environ.get("ULTIMA3D_CONFIG")
    if override:
        return Path(override)
    return ROOT / "state" / "config.json"


def _deep_merge(base: dict[str, Any], override: Any) -> dict[str, Any]:
    if not isinstance(override, dict):
        return base
    merged = {**base}
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)  # type: ignore[arg-type]
        else:
            merged[key] = value
    return merged


def load(path: Path | str | None = None) -> dict[str, dict[str, bool]]:
    """Effective config: defaults with the JSON override applied."""
    cfg: dict[str, Any] = json.loads(json.dumps(DEFAULTS))  # deep copy
    target = Path(path) if path else config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg  # no override, or unreadable: run with defaults
    return _deep_merge(cfg, raw)  # type: ignore[return-value]


def module_enabled(name: str, cfg: dict | None = None) -> bool:
    return bool((cfg if cfg is not None else load()).get("modules", {}).get(name, True))


def skill_enabled(skill_id: str, cfg: dict | None = None) -> bool:
    return bool((cfg if cfg is not None else load()).get("skills", {}).get(skill_id, True))


def set_flag(section: str, name: str, enabled: bool, path: Path | str | None = None) -> dict:
    """Persist one flag (creating state/config.json) and return the new config."""
    target = Path(path) if path else config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError):
        raw = {}
    section_map = raw.setdefault(section, {})
    if not isinstance(section_map, dict):
        section_map = raw[section] = {}
    section_map[name] = bool(enabled)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return load(target)
