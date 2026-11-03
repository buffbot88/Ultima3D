"""Skill package: frontmatter must satisfy the Agent Skills spec and install cleanly.

Validated locally rather than trusting the writer: name/dir match, field limits,
and the install command copying a runnable skill into a skills directory.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "skills", "ultima3d")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


def parse_frontmatter(text):
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return None, text
    fields = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip('"')
    return fields, text[match.end():]


def main():
    with open(SKILL_MD, encoding="utf-8") as handle:
        text = handle.read()
    fields, body = parse_frontmatter(text)

    check("SKILL.md opens with YAML frontmatter", fields is not None)
    if fields is None:
        return finish()

    name = fields.get("name", "")
    check("name is present", bool(name))
    check("name matches its directory", name == os.path.basename(SKILL_DIR), name)
    check("name uses lowercase letters, digits and hyphens only",
          bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)), name)
    check("name is at most 64 characters", len(name) <= 64, str(len(name)))

    description = fields.get("description", "")
    check("description is present", bool(description))
    check("description is at most 1024 characters", len(description) <= 1024, str(len(description)))

    compatibility = fields.get("compatibility")
    if compatibility:
        check("compatibility is at most 500 characters", len(compatibility) <= 500,
              str(len(compatibility)))
    check("license is declared", bool(fields.get("license")))

    check("body stays under the 500-line guidance", len(body.splitlines()) < 500,
          f"{len(body.splitlines())} lines")
    check("body states the local-only constraint", "own computer" in body)
    check("body documents the bridge command", "python -m ultima3d.skill call" in body)
    check("body documents the 40-minute cap", "40-minute" in body or "40 minutes" in body)
    check("body names every failure escape hatch",
          all(token in body for token in ("output/daemon.log", "ULTIMA3D_BLENDER")))

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [sys.executable, "-m", "ultima3d.skill", "install", "--dir", tmp],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        check("install exits cleanly", result.returncode == 0, result.stderr.strip()[:200])
        target = os.path.join(tmp, "ultima3d", "SKILL.md")
        check("install copies SKILL.md into <dir>/ultima3d", os.path.isfile(target), target)
        if os.path.isfile(target):
            check("installed copy is byte-identical", open(target, encoding="utf-8").read() == text)
        payload = {}
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
        check("install reports the target path", payload.get("ok") is True, result.stdout[:160])

    return finish()


def finish():
    print()
    if failures:
        print(f"SKILL TEST FAILED ({len(failures)}):")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    print("SKILL TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
