"""Agentic 3D layer: pure helpers over Blender/Godot backends (no I/O here)."""

from __future__ import annotations

import json


def combine_status(ultima: dict, godot: dict, live: dict) -> dict:
    """Merge per-backend status payloads into one director-facing snapshot."""
    ok = bool(ultima.get("ok"))  # ultima3d worker is the only hard dependency
    return {"ok": ok, "ultima3d": ultima, "godot": godot, "live_blender": live}


def pick_export_source(files: dict, name: str) -> str | None:
    """Find the main GLB path inside a finalize_asset files dict."""
    if not isinstance(files, dict):
        return None
    for key in (f"{name}.glb", "main", "glb"):
        candidate = files.get(key)
        if isinstance(candidate, str):
            return candidate
    for value in files.values():
        if isinstance(value, str) and value.endswith(".glb") and "_LOD" not in value and "_collision" not in value:
            return value
    return None


def review_gate(validate_json: str, assertions_json: str | None = None) -> dict:
    """Decide done vs fix from machine validation + critic output."""
    try:
        validation = json.loads(validate_json)
    except (json.JSONDecodeError, TypeError):
        validation = {}
    checks = validation.get("checks", {}) if isinstance(validation, dict) else {}
    failed = sorted(k for k, v in checks.items() if v is False)
    critic_failed: list = []
    critic_manual: list = []
    if assertions_json:
        try:
            critic = json.loads(assertions_json)
            results = critic.get("results", critic) if isinstance(critic, dict) else critic
            for entry in results if isinstance(results, list) else []:
                status = entry.get("status")
                if status == "fail":
                    critic_failed.append(entry)
                elif status == "manual":
                    critic_manual.append(entry)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
    done = not failed and not critic_failed and validation.get("pass", True) is not False
    fixes = [f"validate:{k}" for k in failed]
    fixes += [f"critic:{e.get('check', '?')}:{e.get('name', '')}" for e in critic_failed]
    return {"done": done, "failed_checks": failed,
            "critic_failures": critic_failed, "critic_manual": critic_manual,
            "next_fixes": fixes}
