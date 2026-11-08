#!/usr/bin/env python3
"""Build dist/<name>.skill, the archive claude.ai accepts in Settings -> Capabilities -> Skills.

A .skill file is a zip whose top-level folder is the skill (ticket-creator/SKILL.md, ...).
The build checks SKILL.md's frontmatter first, so a bad description fails here, not on upload.

    python tools/package_skill.py                      # -> dist/ticket-creator.skill
    python tools/package_skill.py --out /tmp/x.skill   # somewhere else
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "ticket-creator"
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
MAX_DESCRIPTION = 1024


def check_skill(skill_dir: Path):
    """Problems with SKILL.md that would make the upload fail or the skill misbehave."""
    path = skill_dir / "SKILL.md"
    if not path.is_file():
        return [f"{path} is missing"]
    text = path.read_text(encoding="utf-8")
    m = re.match(r"\A---\n(.*?)\n---\n", text.replace("\r\n", "\n"), re.S)
    if not m:
        return ["SKILL.md has no frontmatter block"]
    meta = {}
    for line in m.group(1).split("\n"):
        k, sep, v = line.partition(":")
        if sep and not line.startswith(" "):
            meta[k.strip()] = v.strip()
    problems = []
    name, desc = meta.get("name", ""), meta.get("description", "")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
        problems.append(f"name {name!r} must be lowercase words joined by hyphens, at most 64 characters")
    if name != skill_dir.name:
        problems.append(f"name {name!r} doesn't match the folder name {skill_dir.name!r}")
    if not desc:
        problems.append("description is empty")
    if len(desc) > MAX_DESCRIPTION:
        problems.append(f"description is {len(desc)} characters; the limit is {MAX_DESCRIPTION}")
    if "<" in desc or ">" in desc:
        problems.append("description must not contain angle brackets")
    return problems


def files_to_pack(skill_dir: Path):
    for p in sorted(skill_dir.rglob("*")):
        rel = p.relative_to(skill_dir)
        if p.is_dir() or any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.name in SKIP_FILES or p.suffix in SKIP_SUFFIXES:
            continue
        yield p, Path(skill_dir.name) / rel


def build(skill_dir: Path, out: Path):
    problems = check_skill(skill_dir)
    if problems:
        raise SystemExit("can't package the skill:\n  " + "\n  ".join(problems))
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in files_to_pack(skill_dir):
            zf.write(src, arc.as_posix())
            count += 1
    return count


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", default=str(SKILL_DIR), help="skill folder (default: skills/ticket-creator)")
    ap.add_argument("--out", help="output file (default: dist/<name>.skill)")
    args = ap.parse_args(argv)
    skill_dir = Path(args.skill).resolve()
    out = Path(args.out) if args.out else ROOT / "dist" / f"{skill_dir.name}.skill"
    count = build(skill_dir, out)
    print(f"wrote {out} ({count} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
