#!/usr/bin/env python3
"""ticket-creator draft tool: validate, render, plan, push, and sync ticket drafts.

Standard library only (Python 3.8+), so it runs anywhere the skill does.

Commands
  validate     <path> [--max-size M] [--template FILE] [--root DIR] [--json]
                   Check drafts: structure, references, cycles, and content (secrets, personal
                   data, placeholders, cited paths that don't exist, sizing and length signals).
                   Exit 1 if there are errors.
  render       <path>             Rewrite each epic's generated Plan block (ticket table, size
                                  mix and effort, build waves, Mermaid graph).
  plan         <path> [--json] [--force]
                   Push order (epics first, then tickets by wave) and each item's push state:
                   pending, pushed, changed (edited after its push), or uncertain (a create was
                   started but no key was recorded). Refuses drafts that fail validation.
  diff         <path> [--json]    What changed in pushed drafts since they were pushed.
  body         <file> [--out FILE]
                   The markdown to send to the platform: frontmatter and HTML comments stripped,
                   T-ids replaced by remote keys, a header line added. --out writes UTF-8 (use it
                   on Windows instead of `>`).
  mark-pushing <file>             Record that a create is about to be sent. Run it right before
                                  the create call, so an interrupted push can be reconciled.
  set-remote   <file> --key K --url U [--id I] [--platform P] [--force]
                   Record a pushed item's remote identity and a fingerprint of what was pushed,
                   so re-runs resume instead of duplicating and later edits are detected.
  push         <path> [--dry-run] [--update] [--create-labels] [--adopt] [--allow-public]
                   Push to GitHub Issues through the `gh` CLI in one run: epic, tickets,
                   sub-issues, dependencies, labels, issue types, milestones, project board.
                   Reconciles with the board first, so it never creates a duplicate.
  config       [path] [--json]    Show the resolved .claude/ticket-creator.yml and check it.

<path> is an epic folder (holding a file with `kind: epic`, usually 00-epic.md), a parent folder
holding several epic folders, a folder of standalone tickets (e.g. tickets/_single), or one file.

Exit codes: 0 OK · 1 the drafts have errors · 2 usage problem (the message says what to fix)
            3 push needs a decision from the user · 4 push stopped on a platform error
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

if sys.version_info < (3, 8):  # pragma: no cover
    sys.exit("tickets.py needs Python 3.8 or newer")

SIZES = ["XS", "S", "M", "L", "XL"]
# Effort per size for one developer new to the area, in hours (matches the size definitions).
EFFORT_HOURS = {"XS": 1, "S": 4, "M": 8, "L": 20, "XL": 40}
# Acceptance-criteria counts above these are unusual for the size (measured on real drafts).
AC_LIMITS = {"XS": 5, "S": 8, "M": 11, "L": 14, "XL": 99}
MAX_BODY_WORDS = 800
MAX_NOTES_CODE_CHARS = 400
TYPES = {"feature", "enabler", "bug", "chore", "spike", "task"}
PRIORITIES = {"urgent", "high", "medium", "low", "none"}
STATUSES = {"todo", "in-progress", "done"}
SLICINGS = {"vertical+enablers", "vertical", "horizontal"}

# Required ticket sections -> accepted heading spellings (lowercase, matched as prefixes).
TICKET_SECTIONS = {
    "Context": ("context", "background"),
    "Scope": ("scope",),
    "Acceptance criteria": ("acceptance criteria",),
    "Technical notes": ("technical notes", "technical context", "implementation notes"),
    "Verification": ("verification", "test plan", "testing"),
}
# Sections that are optional for some ticket types (non-code work has no technical notes).
OPTIONAL_FOR = {"Technical notes": {"task"}}
EPIC_SECTIONS = {
    "Goal": ("goal",),
    "Non-goals": ("non-goals", "non goals", "out of scope"),
}
# Frontmatter keys people write by habit -> the key this tooling reads. Dependency aliases are
# errors, because silently ignoring a dependency produces a wrong build order.
KEY_ALIASES = {
    "depends_on": "blocked_by", "dependencies": "blocked_by", "blocked-by": "blocked_by",
    "blockedby": "blocked_by", "blockers": "blocked_by", "depends": "blocked_by",
    "estimate": "size", "points": "size", "story_points": "size",
    "label": "labels", "tags": "labels", "state": "status",
    "assignees": "assignee", "owner": "assignee", "due_date": "due", "duedate": "due",
    "deadline": "due", "link": "links", "urls": "links", "component": "components",
}

# Bump when the rendered Plan block's layout changes, so validate can tell "made by another version"
# apart from "tickets changed since the last render" and "edited by hand".
FORMAT_VERSION = 3
GEN_START = "<!-- ticket-creator:generated:start (rewritten by `tickets.py render`; edit the ticket files instead) -->"
GEN_START_RE = re.compile(r"<!-- ticket-creator:generated:start.*?-->")
GEN_END = "<!-- ticket-creator:generated:end -->"
GUIDE_RE = re.compile(r"<!--\s*guide:.*?-->", re.S)
COMMENT_RE = re.compile(r"[ \t]*<!--.*?-->[ \t]*", re.S)
FM_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
ID_RE = re.compile(r"^T\d{2,}$")
# A ticket id in prose, optionally qualified by its epic folder: "T05" or "walking-skeleton/T05".
QUALIFIED_TID_RE = re.compile(r"(?<![\w/.-])(?:([a-z0-9][a-z0-9-]*)/)?(T\d{2,})\b")
# Tickets that already exist on a platform: #12, owner/repo#12, ENG-123, PROJ-7
EXTERNAL_REF_RE = re.compile(r"^(?:[\w.-]+/[\w.-]+)?#\d+$|^[A-Z][A-Z0-9_]*-\d+$")
INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
CONFIG_REL = Path(".claude") / "ticket-creator.yml"


class UserError(Exception):
    """A problem the user can fix; printed without a traceback."""


# --------------------------------------------------------------------------- parsing


def _strip_comment(value: str) -> str:
    """Drop a trailing ` # comment` that is not inside quotes."""
    quote_ch = None
    for i, ch in enumerate(value):
        if ch in "\"'" and quote_ch is None:
            quote_ch = ch
        elif ch == quote_ch:
            quote_ch = None
        elif ch == "#" and quote_ch is None and (i == 0 or value[i - 1] in " \t"):
            return value[:i].rstrip()
    return value.strip()


def _scalar(raw: str):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        if raw[0] == '"':
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw[1:-1]
        return raw[1:-1].replace("''", "'")
    if raw in ("", "~", "null", "Null", "NULL"):
        return None
    if raw in ("true", "True", "TRUE"):
        return True
    if raw in ("false", "False", "FALSE"):
        return False
    return raw


def _inline_list(raw: str):
    inner = raw.strip()[1:-1]
    items, buf, quote_ch = [], "", None
    for ch in inner:
        if ch in "\"'" and quote_ch is None:
            quote_ch = ch
        elif ch == quote_ch:
            quote_ch = None
        if ch == "," and quote_ch is None:
            items.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        items.append(buf)
    return [_scalar(i) for i in items if i.strip() != ""]


def parse_frontmatter(text: str):
    """Parse the flat YAML subset the drafts use. Returns (meta, body, errors)."""
    m = FM_RE.match(text)
    if not m:
        return None, text, ["no frontmatter block (the file must start with a line containing only ---)"]
    meta, errors, list_key = {}, [], None
    for n, line in enumerate(m.group(1).split("\n"), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if line[:1] in " \t" and stripped.startswith("- ") and list_key:
            if not isinstance(meta.get(list_key), list):
                meta[list_key] = []
            meta[list_key].append(_scalar(_strip_comment(stripped[2:])))
            continue
        km = re.match(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$", line)
        if not km:
            errors.append(f"frontmatter line {n} not understood: {line.strip()!r} "
                          "(keep frontmatter flat: `key: value`, `key: [a, b]`, or a `- item` list)")
            list_key = None
            continue
        key, rest = km.group(1), _strip_comment(km.group(2) or "")
        if key in meta:
            errors.append(f"frontmatter key `{key}` appears twice")
        if rest.startswith("[") and rest.endswith("]"):
            meta[key], list_key = _inline_list(rest), None
        elif rest == "":
            meta[key], list_key = None, key
        else:
            meta[key], list_key = _scalar(rest), None
    return meta, text[m.end():], errors


def as_list(value):
    """Normalise a frontmatter value to a list of non-empty strings ("T01, T02" -> [T01, T02])."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out = []
    for v in items:
        if v is None or isinstance(v, bool):
            continue
        out += [p.strip() for p in str(v).split(",") if p.strip()]
    return out


def url_list(value):
    """Like as_list, but never splits on commas (URLs may contain them)."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [str(v).strip() for v in items if v is not None and not isinstance(v, bool) and str(v).strip()]


def sections(body: str):
    """Map lowercase '## heading' -> section text (without the heading line). Ignores code fences."""
    out, current, buf, in_code = {}, None, [], False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        hm = None if in_code else re.match(r"^##\s+(.+?)\s*#*\s*$", line)
        if hm and not line.startswith("###"):
            if current is not None:
                out[current] = "\n".join(buf)
            current, buf = hm.group(1).strip().lower(), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf)
    return out


def find_section(secs: dict, aliases):
    for heading, content in secs.items():
        if any(heading.startswith(a) for a in aliases):
            return content
    return None


def meaningful(text: str) -> bool:
    return bool(re.sub(r"<!--.*?-->", "", text or "", flags=re.S).strip())


def strip_generated(text: str) -> str:
    start = GEN_START_RE.search(text)
    if not start:
        return text
    end = text.find(GEN_END, start.end())
    return text[:start.start()] + (text[end + len(GEN_END):] if end != -1 else "")


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- config


DEFAULT_CONFIG = {
    "version": 1,
    "platform": None,
    "drafts_dir": "tickets",
    "template": None,
    "conventions": None,
    "defaults": {"priority": "medium", "max_size": "M", "labels": [], "create_missing_labels": "ask",
                 "slicing": None},
    "type_labels": {t: t for t in sorted(TYPES)},
    "estimates": {"XS": 1, "S": 2, "M": 3, "L": 5, "XL": 8},
}


def _split_flow(s: str):
    """Split the inside of a [..] or {..} flow collection on top-level commas."""
    items, buf, depth, quote_ch = [], "", 0, None
    for ch in s:
        if quote_ch:
            buf += ch
            if ch == quote_ch:
                quote_ch = None
            continue
        if ch in "\"'":
            quote_ch = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            items.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        items.append(buf)
    return [i.strip() for i in items if i.strip()]


def _split_key(text: str):
    """'key: value' -> (key, ':', value). Keys may be quoted. No colon -> (text, '', '')."""
    m = re.match(r"""^("(?:[^"\\]|\\.)*"|'[^']*'|[^:"'\[\]{}]+?)\s*:(?:\s+(.*)|$)""", text.strip())
    if not m:
        return text, "", ""
    key = m.group(1)
    if key[:1] in "\"'":
        key = _scalar(key)
    return str(key).strip(), ":", (m.group(2) or "")


def _yaml_value(raw: str, where: str):
    raw = raw.strip()
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise UserError(f"{where}: a [ list isn't closed on the same line")
        return [_yaml_value(x, where) for x in _split_flow(raw[1:-1])]
    if raw.startswith("{"):
        if not raw.endswith("}"):
            raise UserError(f"{where}: a {{ mapping isn't closed on the same line")
        out = {}
        for part in _split_flow(raw[1:-1]):
            key, sep, rest = _split_key(part)
            if not sep:
                raise UserError(f"{where}: expected `key: value` inside {{ }}, got {part!r}")
            out[key] = _yaml_value(rest, where) if rest.strip() else None
        return out
    value = _scalar(raw)
    if isinstance(value, str) and raw[:1] not in "\"'" and re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _yaml_block(lines, i, indent, source):
    _, _, content = lines[i]
    if content == "-" or content.startswith("- "):
        out = []
        while i < len(lines) and lines[i][1] == indent and (lines[i][2] == "-" or lines[i][2].startswith("- ")):
            n, _, content = lines[i]
            item = content[1:].strip()
            i += 1
            if not item:
                if i < len(lines) and lines[i][1] > indent:
                    value, i = _yaml_block(lines, i, lines[i][1], source)
                else:
                    value = None
                out.append(value)
                continue
            if _split_key(item)[1] and item[:1] not in "[{\"'":
                raise UserError(f"{source} line {n}: lists of `key: value` items aren't supported here")
            out.append(_yaml_value(item, f"{source} line {n}"))
        return out, i
    out = {}
    while i < len(lines) and lines[i][1] == indent:
        n, _, content = lines[i]
        if content == "-" or content.startswith("- "):
            raise UserError(f"{source} line {n}: a list item where a `key:` was expected")
        key, sep, rest = _split_key(content)
        if not sep:
            raise UserError(f"{source} line {n}: expected `key: value`, got {content!r}")
        if key in out:
            raise UserError(f"{source} line {n}: key `{key}` appears twice")
        i += 1
        if rest.strip():
            out[key] = _yaml_value(rest, f"{source} line {n}")
        elif i < len(lines) and lines[i][1] > indent:
            out[key], i = _yaml_block(lines, i, lines[i][1], source)
        elif i < len(lines) and lines[i][1] == indent and (lines[i][2] == "-" or lines[i][2].startswith("- ")):
            out[key], i = _yaml_block(lines, i, indent, source)
        else:
            out[key] = None
    if i < len(lines) and lines[i][1] > indent:
        raise UserError(f"{source} line {lines[i][0]}: unexpected indentation")
    return out, i


def parse_yaml_subset(text: str, source: str = "config"):
    """Parse the YAML the config uses: nested mappings, block and flow lists, flow mappings,
    scalars, and comments. Anchors, multi-line strings, and lists of mappings aren't supported."""
    lines = []
    for n, raw in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        left = raw.lstrip(" ")
        if left.startswith("\t"):
            raise UserError(f"{source} line {n}: indent with spaces, not tabs")
        content = _strip_comment(raw.strip())
        if content in ("---", "...") or not content:
            continue
        lines.append((n, len(raw) - len(left), content))
    if not lines:
        return {}
    value, i = _yaml_block(lines, 0, lines[0][1], source)
    if i < len(lines):
        raise UserError(f"{source} line {lines[i][0]}: unexpected indentation")
    if not isinstance(value, dict):
        raise UserError(f"{source}: the top level must be `key: value` pairs")
    return value


def _deep_merge(base, over):
    if not isinstance(base, dict) or not isinstance(over, dict):
        return over
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(base.get(k), v) if isinstance(base.get(k), dict) and isinstance(v, dict) else v
    return out


def find_config(start):
    """The nearest .claude/ticket-creator.yml at or above `start`, stopping at the git root."""
    p = Path(start).resolve()
    if p.is_file() or not p.exists():
        p = p.parent
    for d in [p, *p.parents]:
        candidate = d / CONFIG_REL
        if candidate.is_file():
            return candidate
        if (d / ".git").exists():
            return None
    return None


def load_config(start=".", explicit=None):
    """Returns (config with defaults merged, config path or None, project root or None)."""
    path = Path(explicit) if explicit else find_config(start)
    if path is None:
        return _deep_merge(DEFAULT_CONFIG, {}), None, None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise UserError(f"can't read {path}: {exc}")
    data = parse_yaml_subset(text, str(path))
    path = path.resolve()
    root = path.parent.parent if path.parent.name == ".claude" else path.parent
    return _deep_merge(DEFAULT_CONFIG, data), path, root


def check_config(cfg):
    """Problems in a loaded config, as (level, key, message)."""
    out = []

    def bad(key, value, allowed):
        out.append(("error", key, f"`{value}` isn't one of: {', '.join(allowed)}"))

    platform = cfg.get("platform")
    if platform not in (None, "github", "linear", "jira", "markdown"):
        bad("platform", platform, ["github", "linear", "jira", "markdown"])
    d = cfg.get("defaults") or {}
    if str(d.get("max_size", "M")).upper() not in SIZES[:-1]:
        bad("defaults.max_size", d.get("max_size"), SIZES[:-1])
    if str(d.get("priority", "medium")).lower() not in PRIORITIES:
        bad("defaults.priority", d.get("priority"), sorted(PRIORITIES))
    if str(d.get("create_missing_labels", "ask")).lower() not in ("ask", "always", "never"):
        bad("defaults.create_missing_labels", d.get("create_missing_labels"), ["ask", "always", "never"])
    if d.get("slicing") is not None and d.get("slicing") not in SLICINGS:
        bad("defaults.slicing", d.get("slicing"), sorted(SLICINGS))
    g = cfg.get("github") or {}
    if platform == "github" and not re.fullmatch(r"[\w.-]+/[\w.-]+", str(g.get("repo") or "")):
        out.append(("warning", "github.repo", "not set to `owner/name`; pushing needs it"))
    if g.get("size_as") not in (None, "label", "project_field"):
        bad("github.size_as", g.get("size_as"), ["label", "project_field"])
    if g.get("priority_as") not in (None, "label", "project_field", "issue_field"):
        bad("github.priority_as", g.get("priority_as"), ["label", "project_field", "issue_field"])
    known = set(DEFAULT_CONFIG) | {"github", "linear", "jira"}
    for key in cfg:
        if key not in known:
            out.append(("warning", key, "unknown key (ignored)"))
    return out


def _cfg_path(value, cfg_root):
    if not value:
        return None
    p = Path(str(value))
    if not p.is_absolute() and cfg_root is not None:
        p = cfg_root / p
    return p


# --------------------------------------------------------------------------- model


@dataclass
class Doc:
    path: Path
    raw: str
    text: str
    meta: dict
    body: str
    parse_errors: list

    def get(self, key, default=None):
        value = self.meta.get(key, default)
        return default if value is None else value

    @property
    def is_epic(self) -> bool:
        return str(self.meta.get("kind", "")).strip().lower() == "epic"

    @property
    def tid(self) -> str:
        return str(self.meta.get("id", "")).strip()


@dataclass
class Epic:
    slug: str
    folder: Path
    doc: Doc
    tickets: list = field(default_factory=list)

    def by_id(self):
        return {t.tid: t for t in self.tickets}


def read_doc(path: Path) -> Doc:
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:  # newline="" keeps CRLF visible
            raw = fh.read()
    except UnicodeDecodeError:
        return Doc(path, "", "", {}, "", ["file is not valid UTF-8 text; re-save it as UTF-8"])
    text = raw.replace("\r\n", "\n")
    meta, body, errs = parse_frontmatter(text)
    return Doc(path, raw, text, meta or {}, body, errs)


def _md_files(folder: Path):
    return [p for p in sorted(folder.glob("*.md")) if p.is_file()]


def load(path: Path):
    """Return (epics, standalone_tickets, problems) for any accepted <path>."""
    problems = []
    path = Path(path).resolve()
    if not path.exists():
        raise UserError(f"{path} does not exist")

    def load_epic(folder: Path):
        docs = [read_doc(p) for p in _md_files(folder)]
        epic_docs = [d for d in docs if d.is_epic]
        if not epic_docs:
            return None, [d for d in docs if d.meta.get("id") is not None]
        if len(epic_docs) > 1:
            problems.append(("error", str(folder), "more than one file has `kind: epic`: "
                             + ", ".join(d.path.name for d in epic_docs)))
        epic = Epic(folder.name, folder, epic_docs[0])
        for d in docs:
            if d is epic_docs[0]:
                continue
            if d.parse_errors and not d.meta:
                problems.append(("warning", str(d.path), f"ignored: {d.parse_errors[0]}"))
            elif d.meta.get("id") is None:
                problems.append(("warning", str(d.path), "ignored: no frontmatter `id` (not a ticket draft)"))
            else:
                epic.tickets.append(d)
        return epic, []

    if path.is_file():
        doc = read_doc(path)
        if doc.is_epic:
            epic, _ = load_epic(path.parent)
            return [epic], [], problems
        return [], [doc], problems

    epic, loose = load_epic(path)
    if epic:
        return [epic], [], problems
    epics, standalone = [], list(loose)
    for sub in sorted(path.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        e, sub_loose = load_epic(sub)
        if e:
            epics.append(e)
        standalone += sub_loose
    if not epics and not standalone:
        raise UserError(f"no drafts found in {path} (need an epic file with `kind: epic`, "
                        "or ticket files with an `id`)")
    return epics, standalone, problems


# --------------------------------------------------------------------------- graph


def all_waves(epics):
    """Wave per (epic slug, ticket id) across the whole set: 1 + the max wave of its blockers,
    following cross-epic refs like other-epic/T03, so waves are comparable between epics.
    None for tickets on a dependency cycle."""
    by_slug = {e.slug: e for e in epics}
    memo, visiting = {}, set()

    def wave(epic, tid):
        key = (epic.slug, tid)
        if key in memo:
            return memo[key]
        if key in visiting:
            return None
        visiting.add(key)
        sub = []
        for d in as_list(epic.by_id()[tid].meta.get("blocked_by")):
            other, dep_id = split_ref(d, by_slug)
            target = other or epic
            if dep_id in target.by_id():
                sub.append(wave(target, dep_id))
        visiting.discard(key)
        memo[key] = None if any(s is None for s in sub) else 1 + max(sub, default=0)
        return memo[key]

    for e in epics:
        for tid in e.by_id():
            wave(e, tid)
    return memo


def waves_for(epic: Epic, epics=None):
    memo = all_waves(epics or [epic])
    return {tid: memo.get((epic.slug, tid)) for tid in epic.by_id()}


def find_cycle(nodes, edges):
    """edges: node -> nodes it depends on. Returns one cycle as a list, or None."""
    color, stack = {}, []

    def dfs(n):
        color[n] = 1
        stack.append(n)
        for d in edges.get(n, ()):
            if d not in nodes:
                continue
            if color.get(d) == 1:
                return stack[stack.index(d):] + [d]
            if color.get(d) is None:
                found = dfs(d)
                if found:
                    return found
        color[n] = 2
        stack.pop()
        return None

    for n in sorted(nodes):
        if color.get(n) is None:
            found = dfs(n)
            if found:
                return found
    return None


def epic_order(epics):
    slugs = {e.slug: e for e in epics}
    deps = {e.slug: [d for d in as_list(e.doc.meta.get("blocked_by")) if d in slugs] for e in epics}
    done, order = set(), []

    def visit(s, trail=()):
        if s in done or s in trail:
            return
        for d in deps[s]:
            visit(d, trail + (s,))
        done.add(s)
        order.append(slugs[s])

    for e in epics:
        visit(e.slug)
    return order


def sorted_tickets(epic: Epic, epics=None):
    waves = waves_for(epic, epics)
    return sorted(epic.tickets, key=lambda t: (waves.get(t.tid) or 99, t.tid)), waves


def split_ref(ref: str, epics_by_slug):
    """'other-epic/T03' -> (other_epic, 'T03') when that epic exists, else (None, ref)."""
    if "/" in ref and not ref.startswith("#"):
        slug, tid = ref.split("/", 1)
        if slug in epics_by_slug:
            return epics_by_slug[slug], tid
    return None, ref


# --------------------------------------------------------------------------- content checks

# High-confidence credential formats: an error, so `plan` and `push` refuse the drafts.
SECRET_PATTERNS = [
    ("an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("a Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("a Stripe key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("an AI provider API key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}")),
    ("a Linear API key", re.compile(r"\blin_api_[A-Za-z0-9]{30,}\b")),
    ("an Atlassian API token", re.compile(r"\bATATT[A-Za-z0-9_=-]{30,}")),
    ("a private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("a JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
]
URL_CREDENTIALS_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://([^\s/:@`]+):([^\s/@`]+)@([^\s/:`]*)")
LOCAL_HOST_RE = re.compile(r"^(?:localhost|127\.0\.0\.1|0\.0\.0\.0|host\.docker\.internal|\[::1\])$", re.I)
SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"private[_-]?key)\b[\"']?\s*[:=]\s*[\"']?([^\s\"'`,;)]+)")
SECRET_PLACEHOLDER_RE = re.compile(
    r"^(?:<[^>]*>|\$\{?[\w.]+\}?|%\w+%|\*+|x{3,}|\.\.\.|…|null|none|nil|undefined|true|false|changeme|"
    r"change-me|redacted|example|dummy|test|secret|password|pass|placeholder|your[\w-]*|my[\w-]*|"
    r"process\.env\.\w+|os\.environ\S*|env\.\w+|\{\{.*\}\})$", re.I)
EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")
SAFE_EMAIL_DOMAIN_RE = re.compile(
    r"(?:^|\.)(?:example\.(?:com|org|net)|example|test|invalid|localhost|local|users\.noreply\.github\.com)$", re.I)
ROLE_MAILBOXES = {"noreply", "no-reply", "donotreply", "do-not-reply", "support", "help", "info", "team",
                  "admin", "hello", "notifications", "alerts", "security", "postmaster", "webmaster", "bot",
                  "git", "reminders", "billing", "sales", "contact"}
PHONE_RE = re.compile(r"(?<![\w+])(?:\+\d{1,3}[ .-]?)?\(\d{3}\)[ .-]?\d{3}[ .-]\d{4}(?!\w)"
                      r"|(?<![\w+])\d{3}[ .-]\d{3}[ .-]\d{4}(?![\w-])"
                      r"|(?<![\w+])\+\d{1,3}(?:[ .-]?\d{2,4}){3,4}(?!\w)")
# "Not decided yet" words: a placeholder anywhere. "Nothing here" words: a placeholder only where a
# section must have content (a ticket's Verification can't be "n/a", but an epic's Non-goals can be "none").
UNKNOWN_WORDS = {"tbd", "tbc", "todo", "to do", "...", "…", "?", "??", "???", "to be determined",
                 "to be decided", "fill in", "fill me in", "placeholder", "lorem ipsum"}
NOTHING_WORDS = {"n/a", "na", "none", "nothing", "-", "—", "see above", "same as above"}
PLACEHOLDER_WORDS = UNKNOWN_WORDS | NOTHING_WORDS
SKELETON_LABEL_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\[[ xX]\]\s*)?(?:\*\*)?(?:in scope|out of scope|relevant code|approach|data|"
    r"watch out for|automated|manual|steps to reproduce|actual|expected|cause)(?:\*\*)?\s*:?\s*(?:\*\*)?\s*$", re.I)
SKELETON_PREFIX_RE = re.compile(
    r"^(?:\*\*)?(?:relevant code|approach|data|watch out for|automated|manual)(?:\*\*)?\s*:\s*", re.I)
EMPTY_SKELETON_LINE_RE = re.compile(
    r"^\s*[-*]\s+(Relevant code|Approach|Data|Watch out for|Automated|Manual):\s*$", re.M)
INLINE_PLACEHOLDER_RE = re.compile(r"(?<![\w`])(?:TBD|TBC)(?![\w`])|\?\?\?|(?i:lorem ipsum)")
TEMPLATE_VALUE_RE = re.compile(r"^<[^<>]+>$")
SENSITIVE_RE = re.compile(
    r"\b(?:vulnerab\w*|exploit\w*|CVE-\d{4}-\d+|security (?:hole|issue|bug|flaw)s?|"
    r"(?:SQL|command|code|header|template|prompt|LDAP|XML|NoSQL) injection|XSS|cross-site scripting|"
    r"CSRF|SSRF|RCE|remote code execution|privilege escalation|auth(?:entication|orization)? bypass|"
    r"leak(?:s|ed|ing)? (?:secrets?|credentials?|tokens?|passwords?|personal data)|"
    r"exposed (?:secrets?|credentials?|tokens?|keys?)|hard-?coded (?:secrets?|credentials?|passwords?|keys?|tokens?)|"
    r"unsafe deseriali[sz]ation|path traversal|open redirect)\b", re.I)
RISK_CATEGORIES = {
    "time zones/DST": re.compile(r"time ?zones?|timezone|\bDST\b|daylight[- ]saving|\bIANA\b", re.I),
    "concurrency or idempotency": re.compile(
        r"concurren\w*|race condition|overlap\w*|\block(?:s|ing)?\b|mutex|idempoten\w*|exactly[- ]once|"
        r"at[- ]most[- ]once|deduplicat\w*", re.I),
    "data migration/backfill": re.compile(r"backfill|data migration|migrate existing", re.I),
    "retries and partial failure": re.compile(r"\bretr(?:y|ies|ied)\b|backoff|dead[- ]letter|partial failure", re.I),
    "scheduling": re.compile(r"\bcron\b|scheduler|scheduled job|background job|queue worker", re.I),
    "cryptography": re.compile(r"encrypt\w*|hash(?:ed|ing)? (?:token|password)s?|\bHMAC\b|signature", re.I),
    "an external service": re.compile(r"webhook|third[- ]party|external API|\bSMTP\b|payment provider", re.I),
}

# Cited-path checks
NEW_HINT_RE = re.compile(
    r"\b(?:new|likely|probably|possibly|perhaps|for example|such as|creates?|created|creating|"
    r"adds?|added|adding|introduc\w*|generat\w*|renam\w*|moves? to|proposed|planned|future|scaffold\w*|"
    r"(?:not|isn't|doesn't|don't|no) (?:yet )?exist\w*)\b|\be\.g\.", re.I)
TID_IN_TEXT_RE = re.compile(r"(?<![\w/.-])(?:[a-z0-9][a-z0-9-]*/)?T\d{2,}\b")
DOMAIN_RE = re.compile(r"^[\w-]+(?:\.[\w-]+)+$")
EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,10}$")
NO_CODEBASE_MARK = "drafted without the codebase"
# Generated or vendored folders: often absent from a checkout, so never checked.
BUILD_DIRS = {"node_modules", "dist", "build", "out", "target", "coverage", ".next", ".nuxt", "bin", "obj",
              "vendor", "__pycache__", ".venv", "venv", ".cache", "tmp"}
GREENFIELD_IGNORE = {".git", ".claude", ".github", ".gitignore", ".gitattributes", ".editorconfig", ".vscode",
                     ".idea", ".ds_store", "license", "license.md", "license.txt", "readme", "readme.md",
                     "readme.txt"}


@dataclass
class SectionRule:
    name: str
    aliases: tuple
    optional_for: frozenset = frozenset()  # ticket types it's optional for; "*" = always optional


DEFAULT_RULES = [SectionRule(n, a, frozenset(OPTIONAL_FOR.get(n, ()))) for n, a in TICKET_SECTIONS.items()]


def load_template_rules(path: Path):
    """Required sections from a team's ticket template: every `## Heading`, in order.
    `## Heading <!-- optional -->` is optional; `<!-- optional-for: task, spike -->` is per type."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except OSError as exc:
        raise UserError(f"can't read the ticket template {path}: {exc}")
    meta, body, _ = parse_frontmatter(text)
    rules, in_code = [], False
    for line in (body if meta is not None else text).split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        m = None if in_code else re.match(r"^##\s+(.+?)\s*$", line)
        if not m or line.startswith("###"):
            continue
        raw = m.group(1)
        name = re.sub(r"<!--.*?-->", "", raw).strip().rstrip("#").strip()
        if not name:
            continue
        opt = re.search(r"<!--\s*optional(?:[-\s]for\s*:\s*(.*?))?\s*-->", raw, re.I)
        optional_for = frozenset()
        if opt:
            optional_for = (frozenset(t.strip().lower() for t in opt.group(1).split(",") if t.strip())
                            if opt.group(1) else frozenset({"*"}))
        aliases = (name.lower(),)
        for builtin, builtin_aliases in TICKET_SECTIONS.items():
            if name.lower() == builtin.lower() or name.lower() in builtin_aliases:
                aliases = tuple(dict.fromkeys((name.lower(),) + builtin_aliases))
        rules.append(SectionRule(name, aliases, optional_for))
    if not rules:
        raise UserError(f"{path}: the ticket template has no `## ` section headings")
    return rules


@dataclass
class Checks:
    """Everything validation needs beyond the drafts themselves."""
    max_idx: int = 2
    rules: list = field(default_factory=lambda: list(DEFAULT_RULES))
    root: Path = None            # repo root for cited-path checks; None skips them
    drafts_top: str = None       # first path segment of the drafts folder (never checked as code)
    declared: set = field(default_factory=set)       # paths some draft says it creates
    declared_dirs: set = field(default_factory=set)  # directories some draft says it creates

    def rule(self, name):
        return next((r for r in self.rules if r.name.lower() == name.lower()), None)


def _git_root(path: Path):
    p = Path(path).resolve()
    if p.is_file() or not p.exists():
        p = p.parent
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return None


def _looks_greenfield(root: Path, drafts_top):
    """No code yet: only docs, dotfiles, and the drafts folder at the top level."""
    ignore = set(GREENFIELD_IGNORE) | ({drafts_top.lower()} if drafts_top else set())
    try:
        for entry in root.iterdir():
            name = entry.name.lower()
            if name in ignore or name.startswith("."):
                continue
            if entry.is_dir() or entry.suffix.lower() not in (".md", ".txt", ".rst", ".adoc", ""):
                return False
        return True
    except OSError:
        return True


def build_checks(path, *, max_size=None, template=None, root=None, cfg=None, cfg_root=None):
    cfg = cfg or {}
    size = str(max_size or (cfg.get("defaults") or {}).get("max_size") or "M").upper()
    if size not in SIZES[:-1]:
        raise UserError(f"max size `{size}` must be one of: {', '.join(SIZES[:-1])}")
    rules = list(DEFAULT_RULES)
    tpl = Path(template) if template else _cfg_path(cfg.get("template"), cfg_root)
    if tpl is not None:
        rules = load_template_rules(tpl)
    repo_root = Path(root).resolve() if root else (cfg_root or _git_root(path))
    drafts_top = None
    if repo_root is not None:
        try:
            rel = Path(path).resolve().relative_to(repo_root)
            drafts_top = rel.parts[0] if rel.parts else None
        except ValueError:
            pass
        if drafts_top is None and cfg.get("drafts_dir"):
            drafts_top = Path(str(cfg["drafts_dir"])).parts[0]
        if _looks_greenfield(repo_root, drafts_top):
            repo_root = None  # nothing to check paths against yet
    return Checks(max_idx=SIZES.index(size), rules=rules, root=repo_root, drafts_top=drafts_top)


def logical_lines(body: str):
    """Paragraphs and list items joined into single lines, outside code fences and comments."""
    in_code = False
    for line in unwrap_paragraphs(COMMENT_RE.sub(" ", body)).split("\n"):
        if line.strip().startswith(("```", "~~~")):
            in_code = not in_code
            continue
        if not in_code:
            yield line


def path_token(span: str):
    """A repo-relative file or directory path from an inline-code span, or None."""
    span = span.strip()
    if not span:
        return None
    token = span.split()[0].rstrip(":,.;)")
    token = re.sub(r"#L\d+(?:-L?\d+)?$", "", token)
    token = re.sub(r":\d+(?:-\d+)?$", "", token)
    if token.startswith("./"):
        token = token[2:]
    if "/" not in token or "://" in token:
        return None
    if token.startswith(("/", "../", "~", "$", "@", "-")) or token.split("/", 1)[0] in BUILD_DIRS:
        return None
    if re.search(r"[*?{}\[\]<>|%=;'\"`\\()]", token):
        return None
    first = token.split("/", 1)[0]
    if DOMAIN_RE.match(first):
        return None  # github.com/x/y, example.org/path
    if token.endswith("/"):
        return token
    return token if EXT_RE.search(token.rsplit("/", 1)[-1]) else None


def collect_declared(docs, checks: Checks):
    """Paths that some draft says will be created ("new file `src/x.ts`"), so citing them
    elsewhere ("the mailer from T02, `src/x.ts`") isn't flagged."""
    for doc in docs:
        for line in logical_lines(doc.body):
            if not NEW_HINT_RE.search(line):
                continue
            for m in INLINE_CODE_RE.finditer(line):
                tok = path_token(m.group(2))
                if not tok:
                    continue
                norm = tok.rstrip("/")
                checks.declared.add(norm)
                if tok.endswith("/"):
                    checks.declared_dirs.add(norm)
                parts = norm.split("/")
                for i in range(1, len(parts)):
                    checks.declared.add("/".join(parts[:i]))


PATH_SECTIONS = ("technical notes", "technical context", "implementation notes", "verification", "test plan",
                 "testing", "background")


def check_paths(doc: Doc, checks: Checks, problems):
    """Cited code that doesn't exist. Only the sections that point at code are checked; paths in
    acceptance criteria and context are usually example data ("Given `notes/a.md`...")."""
    if checks.root is None or NO_CODEBASE_MARK in doc.body.lower():
        return
    text = "\n\n".join(content for heading, content in sections(doc.body).items()
                       if heading.startswith(PATH_SECTIONS))
    missing = []
    for line in logical_lines(text):
        if NEW_HINT_RE.search(line) or TID_IN_TEXT_RE.search(line):
            continue
        for m in INLINE_CODE_RE.finditer(line):
            tok = path_token(m.group(2))
            if not tok or tok in missing:
                continue
            norm = tok.rstrip("/")
            if norm in checks.declared or any(norm.startswith(d + "/") for d in checks.declared_dirs):
                continue
            if checks.drafts_top and (norm == checks.drafts_top or norm.startswith(checks.drafts_top + "/")):
                continue
            target = checks.root / norm
            if (target.is_dir() if tok.endswith("/") else target.exists()):
                continue
            missing.append(tok)
    if missing:
        shown = ", ".join(f"`{p}`" for p in missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
        problems.append(("warning", str(doc.path),
                         f"cites paths that aren't in the repo: {shown}. Fix them, or label files a ticket "
                         "will create as \"(new file)\""))


def _mask(secret: str) -> str:
    return (secret[:4] + "…") if len(secret) > 4 else "…"


def _scan_text(doc: Doc) -> str:
    extra = [f"{k}: {v}" for k, v in doc.meta.items()
             if not str(k).startswith("remote_") and k not in ("assignee", "push_started")]
    return doc.body + "\n" + "\n".join(extra)


def check_secrets(doc: Doc, problems):
    text = _scan_text(doc)
    p = str(doc.path)
    for what, rx in SECRET_PATTERNS:
        m = rx.search(text)
        if m:
            problems.append(("error", p, f"looks like it contains {what} ({_mask(m.group(0))}). Tickets end up "
                             "on shared boards: remove it and refer to it by name (\"the STRIPE_KEY env var\")"))
    for m in URL_CREDENTIALS_RE.finditer(text):
        user, password, host = m.group(1), m.group(2), m.group(3)
        if SECRET_PLACEHOLDER_RE.match(password):
            continue
        local = LOCAL_HOST_RE.match(host) or password == user
        problems.append(("warning" if local else "error", p,
                         f"a URL contains a password ({user}:{_mask(password)}@{host or '…'}): replace it with a "
                         "placeholder like <password>" + (" (it looks like a local dev default)" if local else "")))
        break
    for m in SECRET_ASSIGN_RE.finditer(text):
        value = m.group(2)
        if (SECRET_PLACEHOLDER_RE.match(value) or re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", value)
                or len(value) < 6 or not (re.search(r"\d", value) or len(value) >= 12)):
            continue
        problems.append(("warning", p, f"`{m.group(1)}` seems to be given a real value ({_mask(value)}): "
                         "if it's a credential, remove it and refer to it by name"))
        break


def check_personal_data(doc: Doc, problems):
    text = _scan_text(doc)
    found = []
    for m in EMAIL_RE.finditer(text):
        local, domain = m.group(1).lower(), m.group(2)
        if SAFE_EMAIL_DOMAIN_RE.search(domain) or local in ROLE_MAILBOXES:
            continue
        found.append(f"an email address ({_mask(m.group(0))})")
        break
    if PHONE_RE.search(text):
        found.append("a phone number")
    if found:
        problems.append(("warning", str(doc.path), f"may contain personal data ({', '.join(found)}). Tickets end "
                         "up on shared boards: use a role or a handle, or an example.com address"))


def placeholder_only(text: str, words=PLACEHOLDER_WORDS):
    """'empty' if a section holds only template labels, the placeholder if it holds only a
    placeholder ('TBD', 'n/a'), else None."""
    kept = []
    for line in COMMENT_RE.sub("", text or "").split("\n"):
        if not line.strip() or SKELETON_LABEL_RE.match(line):
            continue
        s = re.sub(r"^\s*(?:[-*+]\s+)?(?:\[[ xX]\]\s*)?", "", line).strip()
        s = SKELETON_PREFIX_RE.sub("", s).strip().strip("*_").strip()
        kept.append(s)
    if not kept:
        return "empty"
    norm = [k.lower().rstrip(".").strip() for k in kept]
    if all(n in words for n in norm):
        return kept[0]
    return None


def check_placeholders(doc: Doc, problems):
    p = str(doc.path)
    for key, value in doc.meta.items():
        if isinstance(value, str) and TEMPLATE_VALUE_RE.match(value.strip()):
            problems.append(("error", p, f"`{key}` still holds the template placeholder {value.strip()!r}"))
    hits = set()
    for line in logical_lines(doc.body):
        bare = INLINE_CODE_RE.sub("", line)
        hits.update(m.group(0) for m in INLINE_PLACEHOLDER_RE.finditer(bare))
    if hits:
        problems.append(("warning", p, f"contains a placeholder ({', '.join(sorted(hits))}): resolve it, or "
                         "move the open question to the epic or a spike ticket"))
    lines = COMMENT_RE.sub("", doc.body).split("\n")
    empty = set()
    for i, line in enumerate(lines):
        m = EMPTY_SKELETON_LINE_RE.match(line)
        if not m:
            continue
        nxt = next((x for x in lines[i + 1:] if x.strip()), "")
        indent = len(line) - len(line.lstrip())
        if len(nxt) - len(nxt.lstrip()) <= indent:  # no nested bullets under the label
            empty.add(m.group(1))
    empty = sorted(empty)
    if empty:
        problems.append(("warning", p, "template lines left empty: " + ", ".join(f"'- {e}:'" for e in empty)
                         + " (fill them in or delete them)"))


def risk_categories(secs: dict):
    scope = find_section(secs, ("scope",)) or ""
    in_scope = re.split(r"\*\*\s*out of scope\s*\*\*|^#+\s*out of scope|^\s*out of scope\s*:?\s*$",
                        scope, maxsplit=1, flags=re.I | re.M)[0]
    text = in_scope + "\n" + (find_section(secs, ("acceptance criteria",)) or "")
    return [name for name, rx in RISK_CATEGORIES.items() if rx.search(text)]


def check_sizing_and_length(t: Doc, secs: dict, size: str, problems):
    p = str(t.path)
    ac = find_section(secs, ("acceptance criteria",))
    if ac is not None and size in AC_LIMITS:
        count = len(re.findall(r"^\s*[-*]\s+\[[ xX]\]", ac, re.M))
        if count > AC_LIMITS[size]:
            problems.append(("warning", p, f"has {count} acceptance criteria, a lot for size {size} (usually "
                             f"{AC_LIMITS[size]} or fewer): re-check the size, or split it"))
    if size in ("XS", "S"):
        cats = risk_categories(secs)
        if len(cats) >= 2:
            problems.append(("warning", p, f"size {size} may be low: its scope involves {', '.join(cats)}, "
                             "which usually take longer than they look. Re-check the size, or split it"))
    words = len(re.findall(r"\S+", COMMENT_RE.sub(" ", t.body)))
    if words > MAX_BODY_WORDS:
        problems.append(("warning", p, f"is {words} words long; aim for under 600 so it reads in a few minutes "
                         "(move shared background to the epic, and name the pattern to follow instead of "
                         "writing the code)"))
    notes = find_section(secs, TICKET_SECTIONS["Technical notes"])
    if notes:
        fenced, in_code, chars = [], False, 0
        for line in notes.split("\n"):
            if line.strip().startswith(("```", "~~~")):
                in_code = not in_code
                continue
            if in_code:
                chars += len(line.strip())
        chars += sum(len(m.group(2)) for m in INLINE_CODE_RE.finditer(notes) if len(m.group(2)) >= 40)
        if chars > MAX_NOTES_CODE_CHARS:
            problems.append(("warning", p, f"Technical notes spell out about {chars} characters of code. Point to "
                             "the file or pattern to follow instead; keep literal code for contracts other "
                             "tickets rely on (a schema, an API shape)"))


def push_state(doc: Doc) -> str:
    """pending | pushed | changed (edited since its push) | uncertain (create started, no key)."""
    if doc.get("remote_key"):
        recorded = doc.get("remote_hash")
        if recorded and str(recorded) != content_hash(doc):
            return "changed"
        return "pushed"
    if doc.get("push_started"):
        return "uncertain"
    return "pending"


def check_push_state(doc: Doc, problems):
    state = push_state(doc)
    if state == "changed":
        problems.append(("warning", str(doc.path), f"edited after it was pushed as {doc.get('remote_key')}; the "
                         "board copy hasn't changed. Review with `tickets.py diff`, then sync it (`tickets.py push "
                         "--update` on GitHub) or update it on the board"))
    elif state == "uncertain":
        problems.append(("warning", str(doc.path), f"a push of this draft started at {doc.get('push_started')} but "
                         "no key was recorded. Look on the board for an issue with the same title before creating "
                         "it again (`tickets.py push` does this for you)"))


def sensitive_reasons(doc: Doc):
    reasons = [f"label '{label}'" for label in as_list(doc.meta.get("labels"))
               if "security" in label.lower() or "vuln" in label.lower()]
    text = str(doc.meta.get("title", "")) + "\n" + COMMENT_RE.sub("", doc.body)
    found = sorted({m.group(0).lower() for m in SENSITIVE_RE.finditer(text)})
    if found:
        reasons.append("mentions " + ", ".join(f"'{f}'" for f in found[:4]))
    return reasons


# --------------------------------------------------------------------------- validate


UNQUOTED_HASH_TITLE_RE = re.compile(r"^title:[ \t]+[^\"'\s][^\n]*?[ \t]#", re.M)


def _check_aliases(doc: Doc, problems):
    p = str(doc.path)
    fm = FM_RE.match(doc.text)
    if fm and UNQUOTED_HASH_TITLE_RE.search(fm.group(1)):
        problems.append(("warning", p, "the title contains ' #', and everything after it is read as a "
                         "comment; put the title in double quotes"))
    for key in doc.meta:
        target = KEY_ALIASES.get(key.lower())
        if target and target not in doc.meta:
            level = "error" if target == "blocked_by" else "warning"
            problems.append((level, p, f"`{key}` is not read by the tooling; rename it to `{target}`"))


def _check_optional_fields(doc: Doc, problems):
    p = str(doc.path)
    due = doc.meta.get("due")
    if due is not None:
        try:
            _dt.date.fromisoformat(str(due).strip())
        except ValueError:
            problems.append(("error", p, f"due '{due}' must be a date like 2026-12-01"))
    bad = [u for u in url_list(doc.meta.get("links")) if not re.match(r"^https?://\S+$", u)]
    if bad:
        problems.append(("error", p, f"links must be full URLs (https://…): {bad}"))


def check_content(doc: Doc, checks: Checks, problems):
    check_secrets(doc, problems)
    check_personal_data(doc, problems)
    check_placeholders(doc, problems)
    check_paths(doc, checks, problems)
    check_push_state(doc, problems)
    _check_optional_fields(doc, problems)


def check_ticket(t: Doc, problems, *, epic=None, epics_by_slug=None, max_idx=2, checks=None):
    checks = checks or Checks(max_idx=max_idx)
    p = str(t.path)
    for err in t.parse_errors:
        problems.append(("error", p, err))
    _check_aliases(t, problems)
    tid = t.tid
    if not ID_RE.match(tid):
        problems.append(("warning", p, f"id '{tid}' should look like T01, T02, ..."))

    title = t.meta.get("title")
    if isinstance(title, list):
        problems.append(("error", p, "title was read as a list; put quotes around titles that start with ["))
        title = ""
    title = str(title or "").strip()
    if not title:
        problems.append(("error", p, "missing `title`"))
    elif len(title) > 90:
        problems.append(("warning", p, f"title is {len(title)} characters; aim for 90 or fewer"))

    ttype = str(t.get("type", "")).strip().lower()
    if ttype not in TYPES:
        problems.append(("error", p, f"type '{ttype}' must be one of: {', '.join(sorted(TYPES))}"))
    size = str(t.get("size", "")).strip().upper()
    if size not in SIZES:
        problems.append(("error", p, f"size '{size}' must be one of: {', '.join(SIZES)}"))
    elif size == "XL":
        problems.append(("error", p, "size XL is an epic, not a ticket: split it"))
    elif SIZES.index(size) > checks.max_idx:
        problems.append(("warning", p, f"size {size} is over the max ({SIZES[checks.max_idx]}): split it unless "
                         "the user accepted it"))
    prio = str(t.get("priority", "")).strip().lower()
    if prio not in PRIORITIES:
        problems.append(("error", p, f"priority '{prio}' must be one of: {', '.join(sorted(PRIORITIES))}"))
    status = t.meta.get("status")
    if status is not None and str(status).strip().lower() not in STATUSES:
        problems.append(("error", p, f"status '{status}' must be one of: {', '.join(sorted(STATUSES))}"))
    for key in ("labels", "blocked_by"):
        if key not in t.meta:
            problems.append(("warning", p, f"`{key}` missing (use [] for none)"))
    ids = epic.by_id() if epic else {}
    odd_related = [x for x in as_list(t.meta.get("related"))
                   if not EXTERNAL_REF_RE.match(x) and x not in ids
                   and split_ref(x, epics_by_slug or {})[0] is None]
    if odd_related:
        problems.append(("warning", p, f"related {odd_related} match no ticket here and don't look like "
                         "existing ticket keys (\"#12\", ENG-4); quote values that start with #"))
    for dep in as_list(t.meta.get("blocked_by")):
        other_epic, other_id = split_ref(dep, epics_by_slug or {})
        if dep == tid:
            problems.append(("error", p, "ticket is blocked by itself"))
        elif other_epic is not None:
            if other_id not in other_epic.by_id():
                problems.append(("error", p, f"blocked_by '{dep}': no {other_id} in epic {other_epic.slug}"))
        elif EXTERNAL_REF_RE.match(dep):
            pass  # a ticket that already exists on the platform
        elif epic is None:
            problems.append(("error", p, f"blocked_by '{dep}': a standalone ticket can only depend on "
                             "existing tickets (\"#12\", ENG-4)"))
        elif dep not in ids:
            hint = next((i for i in ids if i.lower() == dep.lower()), None)
            problems.append(("error", p, f"blocked_by '{dep}' matches no ticket id in this epic"
                             + (f" (did you mean {hint}?)" if hint else "")))

    secs = sections(t.body)
    for rule in checks.rules:
        content = find_section(secs, rule.aliases)
        optional = "*" in rule.optional_for or ttype in rule.optional_for
        if content is None:
            if not optional:
                problems.append(("error", p, f"missing '## {rule.name}' section"))
            continue
        if not meaningful(content):
            if not optional:
                problems.append(("error", p, f"section '## {rule.name}' is empty"))
            continue
        filler = placeholder_only(content)
        if filler and not optional:
            if filler == "empty":
                problems.append(("error", p, f"section '## {rule.name}' is empty (only template labels)"))
            else:
                problems.append(("error", p, f"section '## {rule.name}' holds only a placeholder ({filler!r})"))
    if checks.rule("Acceptance criteria"):
        ac = find_section(secs, checks.rule("Acceptance criteria").aliases)
        if ac is not None and meaningful(ac) and not re.search(r"^\s*[-*]\s+\[[ xX]\]", ac, re.M):
            problems.append(("warning", p, "acceptance criteria should be a checklist (- [ ] ...)"))
    if checks.rule("Scope"):
        scope = find_section(secs, checks.rule("Scope").aliases)
        if scope is not None and not re.search(r"out of scope", scope, re.I):
            problems.append(("warning", p, "scope has no 'Out of scope' list"))
    if GUIDE_RE.search(t.body):
        problems.append(("warning", p, "template guide comments (<!-- guide: ... -->) are still present"))
    if t.get("remote_key") and not t.get("remote_url"):
        problems.append(("warning", p, "remote_key is set but remote_url is missing"))
    if size in SIZES:
        check_sizing_and_length(t, secs, size, problems)
    check_content(t, checks, problems)


def validate(epics, standalone, problems, max_size="M", checks=None):
    if checks is None:
        checks = Checks(max_idx=SIZES.index(str(max_size).upper()))
    by_slug = {e.slug: e for e in epics}
    global_nodes, global_edges = set(), {}
    if checks.root is not None:
        collect_declared([e.doc for e in epics] + [t for e in epics for t in e.tickets] + list(standalone), checks)

    for epic in epics:
        ed, p = epic.doc, str(epic.doc.path)
        for err in ed.parse_errors:
            problems.append(("error", p, err))
        _check_aliases(ed, problems)
        title = ed.meta.get("title")
        if not title or isinstance(title, list):
            problems.append(("error", p, "epic needs a `title` (quote it if it starts with [)"))
        secs = sections(ed.body)
        for name, aliases in EPIC_SECTIONS.items():
            content = find_section(secs, aliases)
            if content is None:
                problems.append(("error", p, f"epic is missing a '## {name}' section"))
            elif not meaningful(content):
                problems.append(("error", p, f"epic section '## {name}' is empty"))
            elif placeholder_only(content, UNKNOWN_WORDS if name == "Non-goals" else PLACEHOLDER_WORDS) \
                    not in (None, "empty"):
                problems.append(("error", p, f"epic section '## {name}' holds only a placeholder"))
        slicing = ed.meta.get("slicing")
        if slicing is not None and str(slicing).strip() not in SLICINGS:
            problems.append(("warning", p, f"slicing '{slicing}' should be one of: {', '.join(sorted(SLICINGS))}"))
        start = GEN_START_RE.search(ed.text)
        end = ed.text.find(GEN_END, start.end()) if start else -1
        if not start:
            problems.append(("warning", p, "no generated Plan block yet: run `tickets.py render`"))
        elif end == -1:
            problems.append(("error", p, "the Plan block's end marker is missing; delete the start marker "
                             "line and run `tickets.py render`"))
        else:
            expected = render_block(epic, by_slug)
            if ed.text[start.start():end + len(GEN_END)] != expected:
                marker, inner = start.group(0), ed.text[start.end():end]
                version = re.search(r"\bv=(\d+)", marker)
                sig = re.search(r"\bsig=([0-9a-f]+)", marker)
                if not inner.strip():
                    msg = "no Plan yet: run `tickets.py render`"
                elif not version or not sig or int(version.group(1)) != FORMAT_VERSION:
                    msg = "the Plan block was made by another version of tickets.py: run `tickets.py render` to refresh it"
                elif sig.group(1) != plan_signature(epic, by_slug):
                    msg = "the Plan block is out of date (tickets changed since the last render): run `tickets.py render`"
                else:
                    msg = "the Plan block was edited by hand: run `tickets.py render` and edit the ticket files instead"
                problems.append(("warning", p, msg))
        if GUIDE_RE.search(ed.body):
            problems.append(("warning", p, "template guide comments (<!-- guide: ... -->) are still present"))
        for dep in as_list(ed.meta.get("blocked_by")):
            if dep not in by_slug and not EXTERNAL_REF_RE.match(dep):
                problems.append(("error", p, f"epic blocked_by '{dep}' is not an epic folder in this set"))
        if ed.get("existing") is True and not ed.get("remote_key"):
            problems.append(("error", p, "`existing: true` needs remote_key/remote_url of the epic to attach to"))
        if ed.get("remote_key") and not ed.get("remote_url"):
            problems.append(("warning", p, "remote_key is set but remote_url is missing"))
        if not epic.tickets:
            problems.append(("error", str(epic.folder), "epic has no ticket files"))
        check_content(ed, checks, problems)

        seen_ids, seen_titles = {}, {}
        for t in epic.tickets:
            check_ticket(t, problems, epic=epic, epics_by_slug=by_slug, checks=checks)
            if t.tid in seen_ids:
                problems.append(("error", str(t.path), f"duplicate id {t.tid} (also in {seen_ids[t.tid].name})"))
            seen_ids[t.tid] = t.path
            key = str(t.meta.get("title", "")).strip().lower()
            if key and key in seen_titles:
                problems.append(("warning", str(t.path), f"same title as {seen_titles[key].name}"))
            seen_titles[key] = t.path
            node = f"{epic.slug}/{t.tid}"
            global_nodes.add(node)
            edges = []
            for d in as_list(t.meta.get("blocked_by")):
                other_epic, other_id = split_ref(d, by_slug)
                if other_epic is not None:
                    edges.append(f"{other_epic.slug}/{other_id}")
                elif not EXTERNAL_REF_RE.match(d):
                    edges.append(f"{epic.slug}/{d}")
            global_edges[node] = edges

    for t in standalone:
        check_ticket(t, problems, epic=None, epics_by_slug=by_slug, checks=checks)

    cycle = find_cycle(global_nodes, global_edges)
    if cycle:
        problems.append(("error", "dependencies", "cycle: " + " -> ".join(cycle)))
    ecycle = find_cycle(set(by_slug), {e.slug: as_list(e.doc.meta.get("blocked_by")) for e in epics})
    if ecycle:
        problems.append(("error", "epic dependencies", "cycle: " + " -> ".join(ecycle)))
    return problems


# --------------------------------------------------------------------------- render


def display_ref(epic, ref: str, epics_by_slug=None, link=True):
    """T01 -> '[#42](url)' once pushed, else 'T01'. Handles cross-epic 'slug/T01'."""
    other, tid = split_ref(ref, epics_by_slug or {})
    target = other or epic
    t = target.by_id().get(tid) if target else None
    if t is None:
        return ref
    key, url = t.get("remote_key"), t.get("remote_url")
    if key:
        return f"[{key}]({url})" if (link and url) else str(key)
    return tid if target is epic else f"{target.slug}/{tid}"


def mermaid_label(text: str) -> str:
    # Mermaid entity codes keep quotes, '#' and angle brackets from breaking the node syntax.
    return (text.replace("#", "#35;").replace('"', "#quot;")
            .replace("<", "#lt;").replace(">", "#gt;"))


def plan_signature(epic: Epic, epics_by_slug) -> str:
    """Short hash of everything the Plan block shows, so validate can spot a stale block."""
    tickets, waves = sorted_tickets(epic, list(epics_by_slug.values()))
    data = [len(epics_by_slug) > 1] + [
        [t.tid, str(t.get("title", "")), str(t.get("type", "")), str(t.get("size", "")).strip().upper(),
         str(t.get("priority", "")), [display_ref(epic, d, epics_by_slug) for d in as_list(t.meta.get("blocked_by"))],
         waves.get(t.tid), t.get("status"), t.get("remote_key"), t.get("remote_url")]
        for t in tickets]
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def effort_text(hours: float) -> str:
    if hours < 8:
        return f"about {hours:g} hour{'s' if hours != 1 else ''}"
    days = round(hours / 8 * 2) / 2
    return f"about {days:g} dev-day{'s' if days != 1 else ''}"


def size_mix(tickets):
    counts = {}
    for t in tickets:
        size = str(t.get("size", "?")).strip().upper()
        counts[size] = counts.get(size, 0) + 1
    order = SIZES + sorted(k for k in counts if k not in SIZES)
    return ", ".join(f"{counts[s]} {s}" for s in order if s in counts)


def render_block(epic: Epic, epics_by_slug) -> str:
    tickets, waves = sorted_tickets(epic, list(epics_by_slug.values()))
    show_status = any(t.meta.get("status") is not None for t in tickets)
    head = "| ID | Ticket | Type | Size | Priority | Blocked by | Wave |" + (" Status |" if show_status else "")
    start = (f"<!-- ticket-creator:generated:start v={FORMAT_VERSION} sig={plan_signature(epic, epics_by_slug)} "
             "(rewritten by `tickets.py render`; edit the ticket files instead) -->")
    lines = [start, "", "### Tickets", "", head, "|---" * (8 if show_status else 7) + "|"]
    hours = 0
    for t in tickets:
        size = str(t.get("size", "?")).strip().upper()
        hours += EFFORT_HOURS.get(size, 0)
        blockers = ", ".join(display_ref(epic, d, epics_by_slug) for d in as_list(t.meta.get("blocked_by"))) or "-"
        title = str(t.get("title", "")).replace("|", "\\|")
        row = (f"| {display_ref(epic, t.tid)} | {title} | {t.get('type', '?')} | {size} | "
               f"{t.get('priority', '?')} | {blockers} | {waves.get(t.tid) or '?'} |")
        if show_status:
            row += f" {t.get('status', 'todo')} |"
        lines.append(row)
    lines += ["", f"**{len(tickets)} ticket{'s' if len(tickets) != 1 else ''}** · {size_mix(tickets)} · "
                  f"{effort_text(hours)} of work for one developer (XS ≈ 1 h, S ≈ ½ day, M ≈ 1 day)", "",
              "### Build order", ""]
    by_wave = {}
    for t in tickets:
        by_wave.setdefault(waves.get(t.tid) or 0, []).append(t)
    for w in sorted(by_wave):
        refs = ", ".join(display_ref(epic, t.tid, link=False) for t in by_wave[w])
        if w == 0:
            label = "Unordered (dependency cycle - fix blocked_by)"
        elif w == 1:
            label = "Wave 1 - start here" + (", in parallel" if len(by_wave[w]) > 1 else "")
        else:
            label = f"Wave {w}" + (" - in parallel" if len(by_wave[w]) > 1 else "")
        lines.append(f"- **{label}:** {refs}")
    if len(epics_by_slug) > 1:
        lines.append("- Wave numbers count across the whole backlog, including blockers in other epics.")
    external = sorted({d for t in tickets for d in as_list(t.meta.get("blocked_by")) if d not in epic.by_id()})
    if external:
        shown = ", ".join(display_ref(epic, d, epics_by_slug, link=False) for d in external)
        lines.append(f"- **Also waits on (outside this epic):** {shown}")
    lines += ["", "### Dependency graph", "", "```mermaid", "graph TD"]
    ext_nodes = {}
    for d in external:
        ext_nodes[d] = "X_" + re.sub(r"\W", "_", d)
        label = mermaid_label(display_ref(epic, d, epics_by_slug, link=False))
        lines.append(f'  {ext_nodes[d]}["{label}"]:::external')
    for t in tickets:
        label = mermaid_label(str(t.get("title", "")))
        lines.append(f'  {t.tid}["{t.tid}: {label} ({str(t.get("size", "?")).strip().upper()})"]')
    for t in tickets:
        for d in as_list(t.meta.get("blocked_by")):
            if d in epic.by_id():
                lines.append(f"  {d} --> {t.tid}")
            elif d in ext_nodes:
                lines.append(f"  {ext_nodes[d]} -.-> {t.tid}")
    if ext_nodes:
        lines.append("  classDef external stroke-dasharray: 4 3")
    lines += ["```", "", GEN_END]
    return "\n".join(lines)


def write_text(path: Path, original_raw: str, text: str):
    if "\r\n" in original_raw:
        text = text.replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def render(epics):
    by_slug = {e.slug: e for e in epics}
    changed = []
    for epic in epics:
        block = render_block(epic, by_slug)
        text = epic.doc.text
        start = GEN_START_RE.search(text)
        end = text.find(GEN_END, start.end()) if start else -1
        if start and end != -1:
            new = text[:start.start()] + block + text[end + len(GEN_END):]
        else:
            if start or GEN_END in text:
                raise UserError(f"{epic.doc.path}: the generated-block markers are damaged; delete both "
                                "marker lines and run render again")
            new = text.rstrip("\n") + "\n\n## Plan\n\n" + block + "\n"
        if new != text:
            write_text(epic.doc.path, epic.doc.raw, new)
            changed.append(str(epic.doc.path))
    return changed


# --------------------------------------------------------------------------- plan / body / remote state

HASH_KEYS = ("kind", "title", "type", "size", "priority", "labels", "blocked_by", "related", "assignee",
             "milestone", "due", "links", "components", "existing")


def content_hash(doc: Doc) -> str:
    """Fingerprint of what a push sends (fields and body), ignoring remote_* bookkeeping, `status`,
    comments, and the epic's generated Plan block."""
    meta = {}
    for key in HASH_KEYS:
        value = doc.meta.get(key)
        if key in ("labels", "blocked_by", "related", "assignee", "components"):
            value = sorted(as_list(value))
        elif key == "links":
            value = url_list(value)
        elif value is not None:
            value = str(value).strip()
            value = value.upper() if key == "size" else value
        meta[key] = value
    body = strip_generated(doc.body) if doc.is_epic else doc.body
    body = COMMENT_RE.sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in body.split("\n"))).strip()
    return hashlib.sha1(json.dumps([meta, body], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def issue_hash(title, body) -> str:
    """Fingerprint of an issue's title and body as stored on the board (line endings and
    trailing whitespace normalised), to notice edits made on the board."""
    text = str(body or "").replace("\r\n", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    return hashlib.sha1((str(title or "").strip() + "\n\n" + text).encode("utf-8")).hexdigest()[:12]


def plan(epics, standalone=()):
    items = []
    by_slug = {e.slug: e for e in epics}

    def common(d: Doc):
        return {"remote_key": d.get("remote_key"), "remote_id": d.get("remote_id"),
                "remote_url": d.get("remote_url"), "remote_platform": d.get("remote_platform"),
                "push": push_state(d), "sensitive": sensitive_reasons(d),
                "assignee": as_list(d.meta.get("assignee")), "milestone": d.get("milestone"),
                "due": d.get("due"), "links": url_list(d.meta.get("links")),
                "components": as_list(d.meta.get("components"))}

    def changes(d: Doc):
        if push_state(d) != "changed":
            return None
        old, new = as_list(d.meta.get("remote_blocked_by")), as_list(d.meta.get("blocked_by"))
        return {"blocked_by_added": [x for x in new if x not in old],
                "blocked_by_removed": [x for x in old if x not in new]}

    def ticket_item(t: Doc, epic, wave):
        blockers = []
        for d in as_list(t.meta.get("blocked_by")):
            other, tid = split_ref(d, by_slug)
            bt = (other or epic).by_id().get(tid) if (other or epic) else None
            blockers.append({"ref": d,
                             "remote_key": bt.get("remote_key") if bt else (d if EXTERNAL_REF_RE.match(d) else None),
                             "remote_id": bt.get("remote_id") if bt else None})
        item = {"kind": "ticket", "epic": epic.slug if epic else None, "id": t.tid, "file": str(t.path),
                "title": t.get("title"), "type": str(t.get("type", "")).strip().lower(),
                "size": str(t.get("size", "")).strip().upper(),
                "priority": str(t.get("priority", "")).strip().lower(),
                "labels": as_list(t.meta.get("labels")), "blocked_by": blockers,
                "related": as_list(t.meta.get("related")), "wave": wave, **common(t)}
        if changes(t):
            item["changes"] = changes(t)
        return item

    for epic in epic_order(epics):
        ed = epic.doc
        item = {"kind": "epic", "epic": epic.slug, "id": epic.slug, "file": str(ed.path),
                "title": ed.get("title"), "existing": ed.get("existing") is True,
                "priority": str(ed.get("priority", "")).strip().lower() or None,
                "labels": as_list(ed.meta.get("labels")),
                "blocked_by": as_list(ed.meta.get("blocked_by")), **common(ed)}
        if changes(ed):
            item["changes"] = changes(ed)
        items.append(item)
        tickets, waves = sorted_tickets(epic, epics)
        items += [ticket_item(t, epic, waves.get(t.tid)) for t in tickets]
    items += [ticket_item(t, None, None) for t in standalone]
    return items


_BLOCK_START_RE = re.compile(r"^(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||```|~~~|<|(?:-{3,}|\*{3,}|_{3,})$|\*\*[^*]+\*\*:?$)")


def unwrap_paragraphs(text: str) -> str:
    """Join hard-wrapped lines inside paragraphs and list items.

    GitHub (and some other trackers) render every newline in an issue body as a line break, so a
    draft wrapped at 100 columns looks ragged there. Headings, list items, tables, quotes, code
    fences, HTML, bold label lines, and explicit hard breaks (two trailing spaces or a backslash)
    are kept as they are.
    """
    out, in_code = [], False
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith(("```", "~~~")):
            in_code = not in_code
            out.append(line)
            continue
        prev = out[-1] if out else ""
        ps = prev.strip()
        joinable_prev = (ps and not in_code and not prev.endswith(("  ", "\\"))
                         and not re.match(r"^(#{1,6}\s|\||```|~~~|<|(?:-{3,}|\*{3,}|_{3,})$|\*\*[^*]+\*\*:?$)", ps))
        if s and joinable_prev and not _BLOCK_START_RE.match(s):
            out[-1] = prev.rstrip() + " " + s
        else:
            out.append(line)
    return "\n".join(out)


def body_for(file: Path, keep_wraps=False) -> str:
    file = Path(file).resolve()
    if not file.is_file():
        raise UserError(f"{file} is not a file")
    folder = file.parent
    try:  # sibling epics too (greenfield backlogs), so cross-epic refs resolve to their keys
        epics, _, _ = load(folder.parent)
    except UserError:
        epics = []
    if not any(e.folder == folder for e in epics):
        try:
            epics, _, _ = load(folder)
        except UserError:
            epics = []
    by_slug = {e.slug: e for e in epics}
    epic = next((e for e in epics if e.folder == folder), None)
    doc = read_doc(file)
    if doc.parse_errors and not doc.meta:
        raise UserError(f"{file}: {doc.parse_errors[0]}")
    body = COMMENT_RE.sub("", doc.body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip("\n")
    if not keep_wraps:
        body = unwrap_paragraphs(body)

    def swap(m):
        slug, tid = m.group(1), m.group(2)
        if slug:
            return display_ref(epic, f"{slug}/{tid}", by_slug, link=False) if slug in by_slug else m.group(0)
        return display_ref(epic, tid, by_slug, link=False) if epic and tid in epic.by_id() else tid

    def swap_line(line):
        # Inside inline code, only a span that is exactly a ticket id becomes its key; other code
        # (`T02_FLAG`, `run T02 now`) is left as written.
        out, pos = [], 0
        for m in INLINE_CODE_RE.finditer(line):
            out.append(QUALIFIED_TID_RE.sub(swap, line[pos:m.start()]))
            inner = m.group(2).strip()
            if re.fullmatch(r"(?:[a-z0-9][a-z0-9-]*/)?T\d{2,}", inner):
                replaced = QUALIFIED_TID_RE.sub(swap, inner)
                out.append(replaced if replaced != inner else m.group(0))
            else:
                out.append(m.group(0))
            pos = m.end()
        out.append(QUALIFIED_TID_RE.sub(swap, line[pos:]))
        return "".join(out)

    out, in_code = [], False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        out.append(line if in_code else swap_line(line))
    text = "\n".join(out).strip("\n")
    links = url_list(doc.meta.get("links"))
    if links and "## links" not in text.lower():
        text += "\n\n## Links\n\n" + "\n".join(f"- {u}" for u in links)
    if doc.is_epic:
        return text + "\n"

    header = []
    if epic is not None:
        header.append(f"**Epic:** {epic.doc.get('remote_key') or epic.doc.get('title', epic.slug)}")
    blockers = [display_ref(epic, d, by_slug, link=False) if epic else d for d in as_list(doc.meta.get("blocked_by"))]
    if blockers:
        header.append(f"**Blocked by:** {', '.join(blockers)}")
    related = as_list(doc.meta.get("related"))
    if related:
        header.append(f"**Related:** {', '.join(related)}")
    header.append(f"**Size:** {str(doc.get('size', '?')).strip().upper()}")
    if doc.get("due"):
        header.append(f"**Due:** {doc.get('due')}")
    return " · ".join(header) + "\n\n" + text + "\n"


def _fm_line(key, value):
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, (list, tuple)):
        return f"{key}: [" + ", ".join(json.dumps(str(v), ensure_ascii=False) for v in value) + "]"
    if value is None:
        return f"{key}: null"
    return f"{key}: {json.dumps(str(value), ensure_ascii=False)}"


def update_frontmatter(file: Path, updates: dict, remove=()):
    """Set or remove frontmatter keys in place, keeping everything else (and CRLF) as it was."""
    file = Path(file)
    doc = read_doc(file)
    m = FM_RE.match(doc.text)
    if not m:
        raise UserError(f"{file}: no frontmatter block")
    lines = m.group(1).split("\n")

    def span(key):
        for i, line in enumerate(lines):
            if re.match(rf"^{re.escape(key)}:(\s|$)", line):
                j = i + 1
                while j < len(lines) and lines[j][:1] in " \t" and lines[j].strip().startswith("- "):
                    j += 1
                return i, j
        return None

    for key in remove:
        found = span(key)
        if found:
            del lines[found[0]:found[1]]
    for key, value in updates.items():
        found = span(key)
        if found:
            lines[found[0]:found[1]] = [_fm_line(key, value)]
        else:
            lines.append(_fm_line(key, value))
    write_text(file, doc.raw, "---\n" + "\n".join(lines) + "\n---\n" + doc.text[m.end():])


def sent_hash_for(file: Path) -> str:
    doc = read_doc(file)
    return issue_hash(doc.get("title", ""), body_for(file))


def set_remote(file: Path, updates: dict, force=False, sent_hash=None, linked=None):
    """Record a pushed item's identity (`updates`: remote_key, remote_url, remote_id,
    remote_platform) plus fingerprints of what was pushed, and clear `push_started`."""
    file = Path(file)
    if not file.is_file():
        raise UserError(f"{file} is not a file")
    doc = read_doc(file)
    if not FM_RE.match(doc.text):
        raise UserError(f"{file}: no frontmatter block")
    current = doc.get("remote_key")
    if current and str(current) != str(updates.get("remote_key")) and not force:
        raise UserError(f"{file} already records remote_key {current}. Refusing to overwrite it, because "
                        "that would lose the link to a ticket that exists. Pass --force if you are sure.")
    values = {k: v for k, v in updates.items() if v is not None}
    values["remote_hash"] = content_hash(doc)
    values["remote_sent"] = sent_hash or sent_hash_for(file)
    values["remote_blocked_by"] = list(linked) if linked is not None else as_list(doc.meta.get("blocked_by"))
    values["remote_pushed_at"] = now_iso()
    update_frontmatter(file, values, remove=("push_started",))


def mark_pushing(file: Path):
    doc = read_doc(Path(file))
    if doc.get("remote_key"):
        raise UserError(f"{file} was already pushed as {doc.get('remote_key')}")
    update_frontmatter(Path(file), {"push_started": now_iso()})


def diff_items(items):
    out = []
    for it in items:
        if it["push"] not in ("changed", "uncertain"):
            continue
        entry = {"id": it["id"], "kind": it["kind"], "file": it["file"], "title": it["title"],
                 "state": it["push"], "remote_key": it.get("remote_key")}
        if it.get("changes"):
            entry.update(it["changes"])
        out.append(entry)
    return out


# --------------------------------------------------------------------------- push: GitHub via `gh`


class PushStop(Exception):
    """Stop a push with an exit code; the report says what happened so far."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class GHError(Exception):
    def __init__(self, args, message):
        self.cmd_args = list(args)
        self.message = " ".join(str(message).split())[:600]
        m = re.search(r"HTTP (\d{3})", str(message))
        self.status = int(m.group(1)) if m else None
        super().__init__(self.message)

    @property
    def rate_limited(self):
        return self.status == 429 or "rate limit" in self.message.lower()

    @property
    def scope_missing(self):
        return bool(re.search(r"\bscopes?\b", self.message, re.I))

    @property
    def already(self):
        return self.status == 422 and bool(re.search(r"already|duplicate|exists", self.message, re.I))


def gh_command(value, base=None):
    """The command that runs `gh`: config `github.cli`, --gh, or plain `gh` on PATH. A .py path
    runs with this Python (handy for a stand-in CLI in tests and evals)."""
    value = str(value or "gh").strip()
    candidate = Path(value)
    if not candidate.is_absolute() and base is not None and (Path(base) / candidate).exists():
        candidate = Path(base) / candidate
    if candidate.suffix == ".py" and candidate.is_file():
        return [sys.executable, str(candidate)]
    if candidate.is_file() and ("/" in value or "\\" in value):
        return [str(candidate)]
    return [t.strip('"') for t in shlex.split(value, posix=(os.name != "nt"))]


class GH:
    def __init__(self, cmd, dry_run=False):
        self.cmd, self.dry_run, self.actions, self.calls = list(cmd), dry_run, [], 0

    def run(self, args, stdin=None, mutate=False, describe=None):
        if mutate:
            self.actions.append(describe or " ".join(str(a) for a in args))
            if self.dry_run:
                return None
        env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", NO_COLOR="1", CLICOLOR="0")
        try:
            proc = subprocess.run(self.cmd + [str(a) for a in args], input=stdin, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env)
        except FileNotFoundError:
            raise PushStop(4, f"couldn't run `{self.cmd[0]}`: install the GitHub CLI (https://cli.github.com) "
                              "and run `gh auth login`")
        self.calls += 1
        if proc.returncode != 0:
            raise GHError(args, (proc.stderr or "") + " " + (proc.stdout or ""))
        return proc.stdout

    def json(self, args, **kw):
        out = self.run(args, **kw)
        if out is None:
            return None
        try:
            return json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            raise GHError(args, f"unexpected output from gh: {out[:200]}")

    def api(self, method, path, payload=None, describe=None):
        args = ["api", "-X", method, path]
        stdin = None
        if payload is not None:
            args += ["--input", "-"]
            stdin = json.dumps(payload)
        return self.json(args, stdin=stdin, mutate=(method != "GET"), describe=describe or f"{method} {path}")


def _norm_title(title) -> str:
    return " ".join(str(title or "").split()).casefold()


def _gh_key(key, default_repo):
    """'#57' -> (default_repo, 57); 'owner/repo#57' -> ('owner/repo', 57); else None."""
    m = re.fullmatch(r"(?:([\w.-]+/[\w.-]+))?#(\d+)", str(key or "").strip())
    if not m:
        return None
    return (m.group(1) or default_repo), int(m.group(2))


class GitHubPush:
    """One deterministic push of a draft set to GitHub Issues, safe to re-run."""

    def __init__(self, path, cfg, cfg_root, opts, checks):
        self.path, self.cfg, self.opts, self.checks = Path(path), cfg, opts, checks
        self.g = cfg.get("github") or {}
        self.repo = opts.repo or self.g.get("repo")
        if not self.repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", str(self.repo)):
            raise UserError("no GitHub repo: set github.repo in .claude/ticket-creator.yml, or pass --repo OWNER/NAME")
        self.repo = str(self.repo)
        self.owner = self.repo.split("/")[0]
        self.dry = bool(opts.dry_run)
        self.gh = GH(gh_command(opts.gh or self.g.get("cli"), cfg_root), self.dry)
        self.report = {"platform": "github", "repo": self.repo, "dry_run": self.dry, "visibility": None,
                       "created": [], "adopted": [], "updated": [], "placed": [], "labels_created": [],
                       "labels_dropped": [], "conflicts": [], "changed_not_synced": [], "followups": [],
                       "needs_decision": [], "actions": self.gh.actions, "stopped": None}
        self.refs = {}            # draft file -> {"repo", "number", "id", "url"}
        self.owned = set()        # files whose board copy this run created (safe to rewrite)
        self.dropped = set()      # labels to leave off (create_missing_labels: never)
        self.labels_to_create = []
        self.types, self.milestones, self.project = {}, {}, None
        self.deps_supported = True

    # ----------------------------------------------------------------- helpers
    def followup(self, message):
        if message not in self.report["followups"]:
            self.report["followups"].append(message)

    def decide(self, kind, message, options, **extra):
        self.report["needs_decision"].append({"kind": kind, "message": message, "options": options, **extra})

    @staticmethod
    def label(it):
        return it["id"] if it["kind"] == "ticket" else f"epic {it['id']}"

    def labels_for(self, it):
        d = self.cfg.get("defaults") or {}
        type_labels = self.cfg.get("type_labels") or {}
        if it["kind"] == "epic":
            base = [str(self.g.get("epic_label") or "epic")] + it["labels"]
        else:
            base = [str(type_labels.get(it["type"]) or it["type"])]
            if str(self.g.get("size_as") or "label") == "label" and it.get("size"):
                base.append(f"size: {it['size']}")
            prio = it.get("priority")
            if str(self.g.get("priority_as") or "label") == "label" and prio and prio != "none":
                base.append(f"priority: {prio}")
            base += it["labels"]
        base += as_list(d.get("labels"))
        out, seen = [], set()
        for label in base:
            if label and label.lower() not in seen and label.lower() not in self.dropped:
                seen.add(label.lower())
                out.append(label)
        return out

    def managed_label(self, name):
        n = name.lower()
        types = {str(v).lower() for v in (self.cfg.get("type_labels") or {}).values()}
        return n.startswith(("size: ", "priority: ")) or n in types

    def ensure_id(self, ref):
        if ref.get("id") not in (None, ""):
            try:
                return int(ref["id"])
            except (TypeError, ValueError):
                pass
        if self.dry and ref.get("number") is None:
            return None
        issue = self.gh.api("GET", f"repos/{ref['repo']}/issues/{ref['number']}")
        ref["id"] = issue["id"]
        return int(issue["id"])

    def record(self, it, number, issue_id, url, sent_hash, linked):
        ref = {"repo": self.repo, "number": number, "id": issue_id, "url": url}
        self.refs[it["file"]] = ref
        if not self.dry:
            set_remote(Path(it["file"]), {"remote_key": f"#{number}", "remote_url": url, "remote_id": issue_id,
                                          "remote_platform": "github"}, sent_hash=sent_hash, linked=linked)
        return ref

    # ----------------------------------------------------------------- run
    def run(self):
        epics, standalone, problems = load(self.path)
        problems = validate(epics, standalone, problems, checks=self.checks)
        errors = [x for x in problems if x[0] == "error"]
        if errors:
            for _, where, msg in errors:
                print(f"  {_rel(where)}: {msg}", file=sys.stderr)
            raise PushStop(1, "the drafts have validation errors; fix them before pushing")
        self.epics, self.by_slug = epics, {e.slug: e for e in epics}
        items = plan(epics, standalone)
        for it in items:
            parsed = _gh_key(it.get("remote_key"), self.repo) if it.get("remote_key") else None
            if parsed and it.get("remote_platform") in (None, "github"):
                self.refs[it["file"]] = {"repo": parsed[0], "number": parsed[1], "id": it.get("remote_id"),
                                         "url": it.get("remote_url")}
        todo = [it for it in items if it["push"] in ("pending", "uncertain") and not it.get("existing")]
        changed = [it for it in items if it["push"] == "changed" and not it.get("existing")]

        self.preflight(todo, changed, items)
        adopt = self.reconcile(todo)
        if self.report["needs_decision"]:
            raise PushStop(3, "the push needs a decision before anything is created")

        for name in self.labels_to_create:
            try:
                self.gh.run(["label", "create", name, "-R", self.repo, "--color",
                             "3e4b9e" if name == str(self.g.get("epic_label") or "epic") else "ededed"],
                            mutate=True, describe=f"create label '{name}'")
                self.report["labels_created"].append(name)
            except GHError as e:
                if not e.already and "already exists" not in e.message.lower():
                    raise PushStop(4, f"creating label '{name}' failed: {e.message}")
        adopt_by_file = {it["file"]: issue for it, issue in adopt}
        for it in todo:  # plan order, so an epic and blockers always exist before what links to them
            if it["file"] in adopt_by_file:
                self.adopt(it, adopt_by_file[it["file"]])
            else:
                self.create(it)
        for it in items:
            if it["push"] in ("pushed", "changed") and it["file"] in self.refs:
                self.place(it)
        self.refresh_bodies(items)
        if changed:
            if self.opts.update:
                for it in changed:
                    self.update(it)
            else:
                self.report["changed_not_synced"] = [
                    {"id": it["id"], "key": it.get("remote_key"), "file": it["file"]}
                    for it in changed if it["kind"] == "ticket"]
        self.finish_epics()
        return 0

    # ----------------------------------------------------------------- preflight (read-only)
    def preflight(self, todo, changed, items):
        try:
            info = self.gh.json(["repo", "view", self.repo, "--json", "nameWithOwner,visibility,viewerPermission,isArchived"])
        except GHError as e:
            raise PushStop(4, f"can't read {self.repo}: {e.message}. Check github.repo in the config, and that "
                              "`gh auth status` shows a login that can see the repo")
        visibility = str(info.get("visibility") or "").upper()
        self.report["visibility"] = visibility.lower() or None
        if info.get("isArchived"):
            raise PushStop(4, f"{self.repo} is archived; issues can't be created there")
        permission = str(info.get("viewerPermission") or "").upper()
        if permission not in ("ADMIN", "MAINTAIN", "WRITE"):
            raise PushStop(4, f"your gh login has {permission or 'no'} access to {self.repo}; creating issues "
                              "needs write access")
        work = todo + (changed if self.opts.update else [])
        if visibility == "PUBLIC" and not self.opts.allow_public:
            risky = [{"id": it["id"], "file": it["file"], "reasons": it["sensitive"]} for it in work if it["sensitive"]]
            if risky:
                self.decide("public-repo-sensitive",
                            f"{self.repo} is public, and {len(risky)} draft(s) look security-sensitive. A public "
                            "issue discloses the problem to everyone before it's fixed.",
                            "Report it privately instead (a GitHub security advisory, or a private tracker), take "
                            "the details out of the draft, or rerun with --allow-public if it's safe to publish.",
                            items=risky)
        needed, seen = [], set()
        for it in work:
            for name in self.labels_for(it):
                if name.lower() not in seen:
                    seen.add(name.lower())
                    needed.append(name)
        if needed:
            have = {str(x.get("name", "")).lower()
                    for x in self.gh.json(["label", "list", "-R", self.repo, "--limit", "1000", "--json", "name"]) or []}
            missing = [n for n in needed if n.lower() not in have]
            policy = "always" if self.opts.create_labels else str(
                (self.cfg.get("defaults") or {}).get("create_missing_labels") or "ask").lower()
            if missing:
                if policy == "always":
                    self.labels_to_create = missing
                elif policy == "never":
                    self.dropped = {n.lower() for n in missing}
                    self.report["labels_dropped"] = missing
                else:
                    self.decide("missing-labels", f"{len(missing)} label(s) don't exist in {self.repo} yet: "
                                + ", ".join(missing),
                                "Rerun with --create-labels to create them, or set defaults.create_missing_labels: "
                                "never to leave them off.", labels=missing)
        wanted = sorted({str(it["milestone"]) for it in work if it.get("milestone")})
        if wanted:
            found = self.gh.api("GET", f"repos/{self.repo}/milestones?state=all&per_page=100") or []
            self.milestones = {str(m["title"]).lower(): m["number"] for m in found}
            missing = [w for w in wanted if w.lower() not in self.milestones]
            if missing:
                self.decide("missing-milestones", f"milestone(s) not found in {self.repo}: " + ", ".join(missing),
                            "Create them on GitHub, or change or remove `milestone:` in the drafts.",
                            milestones=missing)
        mode = str(self.g.get("use_issue_types", "auto")).lower()
        if work and mode not in ("false", "no", "off"):
            try:
                found = self.gh.api("GET", f"orgs/{self.owner}/issue-types") or []
                names = {str(t["name"]).lower(): t["name"] for t in found if isinstance(t, dict) and t.get("name")}
            except GHError as e:
                names = {}
                if mode == "true":
                    self.followup(f"issue types aren't available for {self.owner} ({e.message}); used labels only")

            def pick(*candidates):
                return next((names[c.lower()] for c in candidates if c.lower() in names), None)

            self.types = {"feature": pick("Feature"), "bug": pick("Bug"), "enabler": pick("Task"),
                          "chore": pick("Task"), "spike": pick("Task"), "task": pick("Task"),
                          "epic": pick("Epic", "Feature")}
        project = self.g.get("project")
        unplaced = any(it["file"] in self.refs and not read_doc(Path(it["file"])).get("remote_board")
                       for it in items if not it.get("existing"))
        if isinstance(project, dict) and project.get("number") and (work or unplaced):
            self.project = self.resolve_project(project)

    def resolve_project(self, pc):
        owner, number = str(pc.get("owner") or self.owner), str(pc.get("number"))
        try:
            view = self.gh.json(["project", "view", number, "--owner", owner, "--format", "json"]) or {}
            fields = self.gh.json(["project", "field-list", number, "--owner", owner, "--format", "json",
                                   "--limit", "100"]) or {}
        except GHError as e:
            if e.scope_missing:
                self.followup("Adding issues to the project board needs the `project` token scope: run "
                              "`gh auth refresh -s project`, then run push again to place them")
            else:
                self.followup(f"couldn't read project {owner}/{number}: {e.message}")
            return None
        flist = fields.get("fields", []) if isinstance(fields, dict) else fields
        by_name = {str(f.get("name", "")).lower(): f for f in flist}

        def option(field_name, value):
            f = by_name.get(str(field_name).lower())
            if not f:
                return None, None
            opt = next((o for o in f.get("options") or [] if str(o.get("name", "")).lower() == str(value).lower()), None)
            return f, opt

        proj = {"owner": owner, "number": number, "id": view.get("id"), "fields": []}
        status = pc.get("status")
        if status:
            f, opt = option(pc.get("status_field") or "Status", status)
            if not f or not opt:
                available = [o.get("name") for o in (f or {}).get("options") or []]
                self.decide("missing-project-status",
                            f"project {owner}/{number} has no {pc.get('status_field') or 'Status'} option '{status}'",
                            "Set github.project.status to one of: " + (", ".join(available) or "(no options found)"))
            else:
                proj["fields"].append(("status", f["id"], {str(status).lower(): opt["id"]}, None))
        for kind, as_key, field_key in (("size", "size_as", "size_field"), ("priority", "priority_as", "priority_field")):
            if str(self.g.get(as_key) or "label") == "project_field":
                f = by_name.get(str(pc.get(field_key) or kind.title()).lower())
                if f:
                    proj["fields"].append((kind, f["id"], {str(o.get("name", "")).lower(): o["id"]
                                                           for o in f.get("options") or []}, kind))
                else:
                    self.followup(f"project {owner}/{number} has no {pc.get(field_key) or kind.title()} field; "
                                  f"{kind} isn't set on the board")
        return proj

    def reconcile(self, todo):
        if not todo:
            return []
        issues = self.gh.json(["issue", "list", "-R", self.repo, "--state", "all", "--limit", "1000",
                               "--json", "number,title,url"]) or []
        known = {ref["number"] for ref in self.refs.values() if ref["repo"] == self.repo}
        by_title = {}
        for issue in issues:
            if issue["number"] not in known:
                by_title.setdefault(_norm_title(issue["title"]), []).append(issue)
        adopt = []
        for it in todo:
            matches = by_title.get(_norm_title(it["title"]), [])
            if not matches:
                continue
            if (it["push"] == "uncertain" or self.opts.adopt) and len(matches) == 1:
                adopt.append((it, matches[0]))
                by_title[_norm_title(it["title"])] = []
            else:
                listed = ", ".join(f"#{m['number']}" for m in matches)
                self.decide("possible-duplicate",
                            f"{self.label(it)} \"{it['title']}\" has the same title as {listed} on the board",
                            "If it's the same work, rerun with --adopt to link the draft to the existing issue. "
                            "If it's different work, change the draft's title.",
                            item=it["id"], file=it["file"], matches=[m.get("url") for m in matches])
        return adopt

    # ----------------------------------------------------------------- writes
    def adopt(self, it, issue):
        full = self.gh.api("GET", f"repos/{self.repo}/issues/{issue['number']}")
        self.record(it, issue["number"], full["id"], full.get("html_url") or issue.get("url"),
                    issue_hash(full.get("title"), full.get("body")), [])
        if it["push"] == "uncertain":
            self.owned.add(it["file"])  # our own interrupted create
        elif not self.dry:
            # Someone else's issue: link it, but its title and body are theirs, never rewritten.
            update_frontmatter(Path(it["file"]), {"remote_adopted": True})
        self.report["adopted"].append({"id": it["id"], "key": f"#{issue['number']}", "url": full.get("html_url"),
                                       "file": it["file"], "reason": "interrupted push" if it["push"] == "uncertain"
                                       else "--adopt"})
        self.link(it)

    def create(self, it):
        file = Path(it["file"])
        title = str(it["title"])
        body = body_for(file)
        payload = {"title": title, "body": body, "labels": self.labels_for(it)}
        issue_type = self.types.get("epic" if it["kind"] == "epic" else it["type"])
        if issue_type:
            payload["type"] = issue_type
        if it.get("milestone"):
            payload["milestone"] = self.milestones.get(str(it["milestone"]).lower())
        if it.get("assignee"):
            payload["assignees"] = it["assignee"]
        if not self.dry:
            mark_pushing(file)
        try:
            resp = self.gh.api("POST", f"repos/{self.repo}/issues", payload,
                               describe=f"create issue for {self.label(it)}: {title}")
        except GHError as e:
            if e.status == 422 and "assignee" in e.message.lower() and "assignees" in payload:
                self.followup(f"{self.label(it)}: couldn't assign {', '.join(payload['assignees'])} "
                              "(not a collaborator?); created it unassigned")
                payload.pop("assignees")
                try:
                    resp = self.gh.api("POST", f"repos/{self.repo}/issues", payload,
                                       describe=f"create issue for {self.label(it)}: {title}")
                except GHError as e2:
                    self.stop_on(it, e2)
            else:
                self.stop_on(it, e)
        if self.dry:
            self.refs[it["file"]] = {"repo": self.repo, "number": None, "id": None, "url": None}
            self.report["created"].append({"id": it["id"], "key": None, "title": title, "file": it["file"]})
        else:
            self.record(it, resp["number"], resp["id"], resp["html_url"], issue_hash(title, body), [])
            self.owned.add(it["file"])
            self.report["created"].append({"id": it["id"], "key": f"#{resp['number']}", "url": resp["html_url"],
                                           "title": title, "file": it["file"]})
        self.link(it)

    def stop_on(self, it, e):
        if not self.dry and e.status is not None and 400 <= e.status < 500 and e.status != 408:
            # GitHub rejected the request outright, so nothing was created: no need to reconcile it later.
            update_frontmatter(Path(it["file"]), {}, remove=("push_started",))
        hint = (" GitHub's rate limit kicked in: wait a minute, then run push again; it continues where it "
                "stopped." if e.rate_limited else " Run push again after fixing it; it continues where it stopped.")
        self.report["stopped"] = {"id": it["id"], "file": it["file"], "error": e.message}
        raise PushStop(4, f"creating {self.label(it)} failed: {e.message}.{hint}")

    def resolve(self, it, ref):
        """The board issue a blocked_by entry points at, as a ref dict, or (None, why)."""
        if it["kind"] == "epic":
            other = self.by_slug.get(ref)
            target = self.refs.get(str(other.doc.path)) if other else None
            return (target, None) if target else (None, f"epic {ref} isn't on the board")
        other, tid = split_ref(ref, self.by_slug)
        epic = other or (self.by_slug.get(it["epic"]) if it.get("epic") else None)
        if epic is not None and tid in epic.by_id():
            target = self.refs.get(str(epic.by_id()[tid].path))
            return (target, None) if target else (None, f"{ref} isn't on the board yet")
        parsed = _gh_key(ref, self.repo)
        if parsed:
            issue = self.gh.api("GET", f"repos/{parsed[0]}/issues/{parsed[1]}")
            return {"repo": parsed[0], "number": parsed[1], "id": issue["id"], "url": issue.get("html_url")}, None
        return None, f"{ref} isn't a GitHub issue"

    def link_dependency(self, it, ref, target, mine):
        if not self.deps_supported:
            return False
        blocker_id = self.ensure_id(target)
        try:
            self.gh.api("POST", f"repos/{self.repo}/issues/{mine['number']}/dependencies/blocked_by",
                        {"issue_id": blocker_id}, describe=f"mark {self.label(it)} blocked by {ref}")
            return True
        except GHError as e:
            if e.already:
                return True
            if e.status == 404:
                self.deps_supported = False
                self.followup("GitHub's issue dependencies API isn't available here (GitHub Enterprise Server or "
                              "an older version), so no \"blocked by\" links were added. Each issue's header line "
                              "lists its blockers.")
            else:
                self.followup(f"{self.label(it)}: couldn't add the dependency on {ref}: {e.message}")
            return False

    def link(self, it):
        mine = self.refs[it["file"]]
        linked = []
        if it["kind"] == "ticket" and it.get("epic"):
            epic = self.by_slug[it["epic"]]
            parent = self.refs.get(str(epic.doc.path))
            if parent and parent["repo"] == self.repo:
                try:
                    if mine.get("id") is not None or self.dry:
                        self.gh.api("POST", f"repos/{self.repo}/issues/{parent['number']}/sub_issues",
                                    {"sub_issue_id": self.ensure_id(mine)},
                                    describe=f"attach {self.label(it)} to the epic as a sub-issue")
                except GHError as e:
                    if not e.already:
                        self.followup(f"{self.label(it)}: couldn't attach it to the epic: {e.message}")
            elif parent:
                self.followup(f"{self.label(it)}: the epic is in {parent['repo']}; attach it by hand")
        refs = it["blocked_by"] if it["kind"] == "epic" else [b["ref"] for b in it["blocked_by"]]
        for ref in refs:
            target, why = self.resolve(it, ref)
            if target is None:
                self.followup(f"{self.label(it)}: dependency on {ref} not linked ({why})")
                continue
            if self.link_dependency(it, ref, target, mine):
                linked.append(ref)
        if not self.dry:
            update_frontmatter(Path(it["file"]), {"remote_blocked_by": linked})
        self.place(it)

    def place(self, it):
        if not self.project:
            return
        doc = read_doc(Path(it["file"]))
        if doc.get("remote_board"):
            return
        mine = self.refs.get(it["file"])
        if not mine:
            return
        p = self.project
        try:
            item = self.gh.json(["project", "item-add", p["number"], "--owner", p["owner"], "--url",
                                 mine.get("url") or "<new issue>", "--format", "json"],
                                mutate=True, describe=f"add {self.label(it)} to project {p['owner']}/{p['number']}")
            item_id = (item or {}).get("id", "<new item>")
            for kind, field_id, options, value_key in p["fields"]:
                if kind == "status":
                    option_id = next(iter(options.values()))
                else:
                    value = str(it.get(value_key) or "").lower()
                    option_id = options.get(value)
                    if option_id is None:
                        self.followup(f"{self.label(it)}: the project's {kind} field has no option '{value}'")
                        continue
                self.gh.run(["project", "item-edit", "--id", item_id, "--project-id", p["id"], "--field-id",
                             field_id, "--single-select-option-id", option_id],
                            mutate=True, describe=f"set {kind} of {self.label(it)} on the board")
            if not self.dry:
                update_frontmatter(Path(it["file"]), {"remote_board": f"{p['owner']}/{p['number']}"})
            self.report["placed"].append(it["id"])
        except GHError as e:
            if e.scope_missing:
                self.followup("Adding issues to the project board needs the `project` token scope: run "
                              "`gh auth refresh -s project`, then run push again to place them")
                self.project = None
            else:
                self.followup(f"{self.label(it)}: couldn't add it to the project board: {e.message}")

    def remote_unchanged(self, doc, ref):
        remote = self.gh.api("GET", f"repos/{ref['repo']}/issues/{ref['number']}")
        return issue_hash(remote.get("title"), remote.get("body")) == str(doc.get("remote_sent")), remote

    def refresh_bodies(self, items):
        """Re-send ticket bodies whose text changed only because keys became known (a ticket that
        mentions a later ticket), if the board copy is still exactly what was pushed."""
        if self.dry:
            return
        for it in items:
            if it["kind"] != "ticket" or it["file"] not in self.refs:
                continue
            file = Path(it["file"])
            doc = read_doc(file)
            if push_state(doc) != "pushed" or not doc.get("remote_sent") or doc.get("remote_adopted") is True:
                continue
            body = body_for(file)
            new_hash = issue_hash(doc.get("title"), body)
            if new_hash == str(doc.get("remote_sent")):
                continue
            ref = self.refs[it["file"]]
            if it["file"] not in self.owned:
                same, _ = self.remote_unchanged(doc, ref)
                if not same:
                    continue  # someone edited it on the board; leave it
            try:
                self.gh.api("PATCH", f"repos/{ref['repo']}/issues/{ref['number']}", {"body": body},
                            describe=f"update {self.label(it)}'s links to other tickets")
                update_frontmatter(file, {"remote_sent": new_hash})
            except GHError as e:
                self.followup(f"{self.label(it)}: couldn't refresh its links to other tickets: {e.message}")

    def update(self, it):
        file = Path(it["file"])
        doc = read_doc(file)
        ref = self.refs.get(it["file"])
        if not ref or ref["repo"] != self.repo:
            self.followup(f"{self.label(it)} ({it.get('remote_key')}) isn't an issue in {self.repo}; update it on its board")
            return
        key = f"#{ref['number']}"
        if doc.get("remote_adopted") is True:
            self.report["conflicts"].append({
                "id": it["id"], "key": key, "file": it["file"],
                "reason": "it's linked to an issue someone else wrote (--adopt), so its text wasn't overwritten",
                "options": "Post the change as a comment on the issue, or edit it by hand."})
            return
        same, remote = self.remote_unchanged(doc, ref)
        if not doc.get("remote_sent"):
            self.report["conflicts"].append({
                "id": it["id"], "key": key, "file": it["file"],
                "reason": "it was pushed without a fingerprint (an older tickets.py), so edits made on the board "
                          "can't be ruled out; it wasn't overwritten",
                "options": "Update the issue by hand, then run `tickets.py set-remote` with the same key to record "
                           "a fingerprint; later pushes can sync it."})
            return
        if str(remote.get("state", "")).lower() == "closed":
            self.report["conflicts"].append({"id": it["id"], "key": key, "file": it["file"],
                                             "reason": "closed on the board, so it wasn't changed"})
            return
        if not same:
            self.report["conflicts"].append({
                "id": it["id"], "key": key, "file": it["file"],
                "reason": "edited on the board since it was pushed, so it wasn't overwritten",
                "options": "Post the change as a comment, or edit the issue by hand and then run "
                           f"`tickets.py set-remote {_rel(it['file'])} --key \"{key}\" --url {ref.get('url') or '<url>'}` "
                           "to mark the draft as synced."})
            return
        title, body = str(it["title"]), body_for(file)
        summary = []
        patch = {}
        if (remote.get("title") or "") != title:
            patch["title"] = title
            summary.append("title")
        if issue_hash("", remote.get("body")) != issue_hash("", body):
            patch["body"] = body
            summary.append("body")
            if self.dry:
                diff = list(difflib.unified_diff(str(remote.get("body") or "").replace("\r\n", "\n").split("\n"),
                                                 body.split("\n"), "board", "draft", lineterm="", n=1))
                summary.append("\n".join(diff[:80]))
        if patch:
            self.gh.api("PATCH", f"repos/{self.repo}/issues/{ref['number']}", patch,
                        describe=f"update {self.label(it)} {key}: {', '.join(k for k in patch)}")
        desired = self.labels_for(it)
        current = [str(x.get("name", "")) for x in remote.get("labels") or []]
        lower_current = {c.lower() for c in current}
        lower_desired = {d.lower() for d in desired}
        add = [d for d in desired if d.lower() not in lower_current]
        stale = [c for c in current if self.managed_label(c) and c.lower() not in lower_desired]
        if add:
            self.gh.api("POST", f"repos/{self.repo}/issues/{ref['number']}/labels", {"labels": add},
                        describe=f"add labels to {key}: {', '.join(add)}")
            summary.append("labels +" + ", ".join(add))
        for name in stale:
            try:
                self.gh.api("DELETE", f"repos/{self.repo}/issues/{ref['number']}/labels/{quote(name, safe='')}",
                            describe=f"remove label '{name}' from {key}")
                summary.append(f"label -{name}")
            except GHError as e:
                if e.status != 404:
                    self.followup(f"{key}: couldn't remove label '{name}': {e.message}")
        others = [c for c in current if not self.managed_label(c) and c.lower() not in lower_desired]
        if others:
            summary.append("kept labels added on the board: " + ", ".join(others))
        new_assignees = [a for a in it.get("assignee") or []
                         if a.lower() not in {str(x.get("login", "")).lower() for x in remote.get("assignees") or []}]
        if new_assignees:
            try:
                self.gh.api("POST", f"repos/{self.repo}/issues/{ref['number']}/assignees", {"assignees": new_assignees},
                            describe=f"assign {key} to {', '.join(new_assignees)}")
                summary.append("assigned " + ", ".join(new_assignees))
            except GHError as e:
                self.followup(f"{key}: couldn't assign {', '.join(new_assignees)}: {e.message}")
        old = as_list(doc.meta.get("remote_blocked_by"))
        new = [b["ref"] for b in it["blocked_by"]] if it["kind"] == "ticket" else it["blocked_by"]
        linked = [r for r in old if r in new]
        for r in [x for x in new if x not in old]:
            target, why = self.resolve(it, r)
            if target is None:
                self.followup(f"{key}: dependency on {r} not linked ({why})")
            elif self.link_dependency(it, r, target, ref):
                linked.append(r)
                summary.append(f"blocked by +{r}")
        for r in [x for x in old if x not in new]:
            target, why = self.resolve(it, r)
            if target is None:
                self.followup(f"{key}: remove its dependency on {r} by hand ({why})")
                continue
            try:
                self.gh.api("DELETE", f"repos/{self.repo}/issues/{ref['number']}/dependencies/blocked_by/"
                                      f"{self.ensure_id(target)}", describe=f"remove {key}'s dependency on {r}")
                summary.append(f"blocked by -{r}")
            except GHError as e:
                if e.status != 404:
                    self.followup(f"{key}: couldn't remove the dependency on {r}: {e.message}")
        if not self.dry:
            set_remote(file, {"remote_key": key, "remote_url": ref.get("url"), "remote_id": ref.get("id"),
                              "remote_platform": "github"}, sent_hash=issue_hash(title, body), linked=linked)
        self.report["updated"].append({"id": it["id"], "key": key, "file": it["file"], "changes": summary})

    def finish_epics(self):
        if not self.dry:
            render(load(self.path)[0])
        for epic in self.epics:
            path = epic.doc.path
            doc = read_doc(path)
            ref = self.refs.get(str(path))
            if doc.get("existing") is True or not ref or ref["repo"] != self.repo:
                continue
            body = body_for(path)
            new_hash = issue_hash(doc.get("title"), body)
            if self.dry:
                created_now = bool(self.report["created"] or self.report["adopted"] or self.report["updated"])
                if ref["number"] is None or created_now or new_hash != str(doc.get("remote_sent")):
                    self.gh.actions.append(f"update the epic body of #{ref['number'] or '<new>'} (Plan block with links)")
                continue
            if new_hash == str(doc.get("remote_sent")):
                continue
            if str(path) not in self.owned:
                same, _ = self.remote_unchanged(doc, ref)
                if not same:
                    self.report["conflicts"].append({
                        "id": epic.slug, "key": f"#{ref['number']}", "file": str(path),
                        "reason": "the epic was edited on the board, so its Plan block wasn't updated",
                        "options": "Copy the Plan section from the draft into the issue by hand."})
                    continue
            try:
                self.gh.api("PATCH", f"repos/{self.repo}/issues/{ref['number']}",
                            {"title": str(doc.get("title")), "body": body},
                            describe=f"update the epic body of #{ref['number']}")
                update_frontmatter(path, {"remote_sent": new_hash, "remote_hash": content_hash(read_doc(path))})
            except GHError as e:
                self.followup(f"couldn't update the epic body of #{ref['number']}: {e.message}")


def print_push_report(r):
    head = f"GitHub {r['repo']}" + (f" ({r['visibility']})" if r.get("visibility") else "")
    print(head + (" - dry run: nothing was changed" if r["dry_run"] else ""))
    for c in r["created"]:
        print(f"  created  {c['key'] or '(new)':>7}  {c['id']:<6} {c.get('title', '')}")
    for c in r["adopted"]:
        print(f"  linked   {c['key']:>7}  {c['id']:<6} already on the board ({c['reason']})")
    for c in r["updated"]:
        print(f"  updated  {c['key']:>7}  {c['id']:<6} " + "; ".join(x for x in c["changes"] if "\n" not in x))
        for x in c["changes"]:
            if "\n" in x:
                print("\n".join("           " + line for line in x.split("\n")))
    if r["labels_created"]:
        print("  labels created: " + ", ".join(r["labels_created"]))
    if r["labels_dropped"]:
        print("  labels left off (create_missing_labels: never): " + ", ".join(r["labels_dropped"]))
    for c in r["changed_not_synced"]:
        print(f"  changed  {c['key']:>7}  {c['id']:<6} edited since its push; not synced (rerun with --update)")
    for c in r["conflicts"]:
        print(f"  CONFLICT {c['key']:>7}  {c['id']:<6} {c['reason']}" + (f". {c['options']}" if c.get("options") else ""))
    for d in r["needs_decision"]:
        print(f"  DECIDE   {d['message']}\n           {d['options']}")
    for f in r["followups"]:
        print(f"  FOLLOW-UP {f}")
    if r["dry_run"] and r["actions"]:
        print("  would:")
        for a in r["actions"]:
            print(f"    - {a}")
    if r.get("stopped"):
        print(f"  STOPPED at {r['stopped']['id']}: {r['stopped']['error']}")


# --------------------------------------------------------------------------- cli


def _rel(where: str) -> str:
    """Relative path when it's short, otherwise the absolute one (no long ../../.. chains)."""
    try:
        rel = os.path.relpath(where)
    except ValueError:  # different drive on Windows
        return str(where)
    return str(Path(where).resolve()) if rel.startswith(os.pardir + os.sep + os.pardir) else rel


def _print_problems(problems):
    errors = [x for x in problems if x[0] == "error"]
    warnings = [x for x in problems if x[0] == "warning"]
    for level, where, msg in errors + warnings:
        print(f"{level.upper():7} {_rel(where)}: {msg}")
    return errors, warnings


def _checks_for(args, path):
    cfg, _, cfg_root = load_config(path, getattr(args, "config", None))
    return cfg, cfg_root, build_checks(path, max_size=getattr(args, "max_size", None),
                                       template=getattr(args, "template", None), root=getattr(args, "root", None),
                                       cfg=cfg, cfg_root=cfg_root)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="tickets.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_check_args(p):
        p.add_argument("--max-size", type=str.upper, choices=SIZES[:-1],
                       help="largest allowed size (default: defaults.max_size in the config, else M)")
        p.add_argument("--template", help="team ticket template; its ## headings become the required sections")
        p.add_argument("--root", help="repo root for checking cited paths (default: the project root)")
        p.add_argument("--config", help="config file (default: the nearest .claude/ticket-creator.yml)")

    v = sub.add_parser("validate", help="check drafts")
    v.add_argument("path")
    v.add_argument("--json", action="store_true")
    add_check_args(v)
    r = sub.add_parser("render", help="rewrite epic Plan blocks")
    r.add_argument("path")
    p = sub.add_parser("plan", help="push order and push state")
    p.add_argument("path")
    p.add_argument("--json", action="store_true")
    p.add_argument("--force", action="store_true", help="plan even if validation fails")
    add_check_args(p)
    d = sub.add_parser("diff", help="what changed in pushed drafts since their push")
    d.add_argument("path")
    d.add_argument("--json", action="store_true")
    b = sub.add_parser("body", help="markdown body to send to the platform")
    b.add_argument("file")
    b.add_argument("--out", help="write to this file (UTF-8) instead of stdout")
    b.add_argument("--keep-wraps", action="store_true",
                   help="keep hard line wraps (by default paragraphs are unwrapped for the platform)")
    mp = sub.add_parser("mark-pushing", help="record that a create is about to be sent")
    mp.add_argument("file")
    s = sub.add_parser("set-remote", help="record a pushed item's remote identity")
    s.add_argument("file")
    s.add_argument("--key", required=True, help="human key, e.g. #42, ENG-123, PROJ-7, or a project name")
    s.add_argument("--url", required=True)
    s.add_argument("--id", help="platform-internal id (GitHub numeric issue id, Linear UUID, Jira id)")
    s.add_argument("--platform")
    s.add_argument("--force", action="store_true", help="overwrite a different existing remote_key")
    pu = sub.add_parser("push", help="push drafts to GitHub Issues with the gh CLI")
    pu.add_argument("path")
    pu.add_argument("--repo", help="OWNER/NAME (default: github.repo in the config)")
    pu.add_argument("--gh", help="command that runs gh (default: github.cli in the config, else gh)")
    pu.add_argument("--dry-run", action="store_true", help="show what would happen; change nothing")
    pu.add_argument("--update", action="store_true",
                    help="also sync drafts edited after their push (never overwrites edits made on the board)")
    pu.add_argument("--create-labels", action="store_true", help="create missing labels without asking")
    pu.add_argument("--adopt", action="store_true",
                    help="link drafts to existing issues with the same title instead of stopping")
    pu.add_argument("--allow-public", action="store_true",
                    help="push security-sensitive drafts to a public repo anyway")
    pu.add_argument("--json", action="store_true")
    add_check_args(pu)
    c = sub.add_parser("config", help="show and check the resolved config")
    c.add_argument("path", nargs="?", default=".")
    c.add_argument("--config", help="config file (default: the nearest .claude/ticket-creator.yml)")
    c.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "set-remote":
            set_remote(Path(args.file), {"remote_key": args.key, "remote_url": args.url,
                                         "remote_id": args.id, "remote_platform": args.platform}, args.force)
            print(f"recorded {args.key} in {_rel(args.file)}")
            return 0

        if args.cmd == "mark-pushing":
            mark_pushing(Path(args.file))
            print(f"marked {_rel(args.file)} as being pushed")
            return 0

        if args.cmd == "body":
            text = body_for(Path(args.file), keep_wraps=args.keep_wraps)
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                print(f"wrote {_rel(args.out)}")
            else:
                sys.stdout.write(text)
            return 0

        if args.cmd == "config":
            cfg, cfg_path, cfg_root = load_config(args.path, args.config)
            issues = check_config(cfg)
            if args.json:
                print(json.dumps({"path": str(cfg_path) if cfg_path else None,
                                  "project_root": str(cfg_root) if cfg_root else None, "config": cfg,
                                  "problems": [{"level": l, "key": k, "message": m} for l, k, m in issues]},
                                 indent=2, ensure_ascii=False))
            else:
                print(f"config: {_rel(str(cfg_path)) if cfg_path else '(none found; using defaults)'}")
                print(json.dumps(cfg, indent=2, ensure_ascii=False))
                for level, key, msg in issues:
                    print(f"{level.upper():7} {key}: {msg}")
            return 1 if any(l == "error" for l, _, _ in issues) else 0

        if args.cmd == "push":
            cfg, cfg_root, checks = _checks_for(args, args.path)
            pusher = GitHubPush(args.path, cfg, cfg_root, args, checks)
            code = 0
            try:
                code = pusher.run()
            except PushStop as stop:
                code = stop.code
                pusher.report["error"] = str(stop)
            if args.json:
                print(json.dumps(pusher.report, indent=2, ensure_ascii=False))
            else:
                print_push_report(pusher.report)
                if pusher.report.get("error"):
                    print(f"\n{'NEEDS A DECISION' if code == 3 else 'ERROR'}: {pusher.report['error']}")
            return code

        if args.cmd == "render":
            epics, _, _ = load(Path(args.path))
            if not epics:
                print("nothing to render: standalone tickets have no Plan block (run validate instead)")
                return 0
            changed = render(epics)
            print("\n".join(f"rendered {_rel(c)}" for c in changed) or "epic Plan blocks already up to date")
            return 0

        epics, standalone, problems = load(Path(args.path))

        if args.cmd == "diff":
            items = diff_items(plan(epics, standalone))
            if args.json:
                print(json.dumps(items, indent=2, ensure_ascii=False))
            else:
                for it in items:
                    if it["state"] == "uncertain":
                        print(f"{it['id']}: a push started but no key was recorded ({_rel(it['file'])})")
                        continue
                    deps = "".join([f" +{x}" for x in it.get("blocked_by_added", [])]
                                   + [f" -{x}" for x in it.get("blocked_by_removed", [])])
                    print(f"{it['remote_key']} {it['id']}: changed since its push"
                          + (f"; blocked_by{deps}" if deps else "")
                          + f". Text changes: git diff -- {_rel(it['file'])}")
                print(f"{len(items)} changed" if items else "no pushed draft has changed since its push")
            return 0

        cfg, cfg_root, checks = _checks_for(args, args.path)
        problems = validate(epics, standalone, problems, checks=checks)
        errors = [x for x in problems if x[0] == "error"]

        if args.cmd == "plan":
            if errors and not args.force:
                print("Drafts have validation errors; fix them before pushing:", file=sys.stderr)
                for _, where, msg in errors:
                    print(f"  {_rel(where)}: {msg}", file=sys.stderr)
                return 1
            items = plan(epics, standalone)
            if args.json:
                print(json.dumps(items, indent=2, ensure_ascii=False))
            else:
                for it in items:
                    state = {"pushed": f"pushed {it['remote_key']}", "changed": f"changed {it['remote_key']}"}.get(
                        it["push"], it["push"])
                    where = f"wave {it['wave']}" if it.get("wave") else it["kind"]
                    flag = "  [security-sensitive]" if it["sensitive"] else ""
                    print(f"[{state:>16}] {where:<8} {it['id']:<6} {it['title']}  ({_rel(it['file'])}){flag}")
                pending = sum(1 for it in items if it["push"] in ("pending", "uncertain"))
                changed = sum(1 for it in items if it["push"] == "changed")
                print(f"\n{len(items)} items, {pending} pending" + (f", {changed} changed since push" if changed else ""))
            return 0

        warnings = [x for x in problems if x[0] == "warning"]
        summary = []
        for e in epics:
            w = waves_for(e, epics)
            hours = sum(EFFORT_HOURS.get(str(t.get("size", "")).strip().upper(), 0) for t in e.tickets)
            summary.append({"epic": e.slug, "title": e.doc.get("title"), "tickets": len(e.tickets),
                            "sizes": size_mix(e.tickets), "effort_hours": hours,
                            "waves": max([x for x in w.values() if x] or [0])})
        if args.json:
            print(json.dumps({"ok": not errors,
                              "errors": [{"where": w, "message": m} for _, w, m in errors],
                              "warnings": [{"where": w, "message": m} for _, w, m in warnings],
                              "epics": summary, "standalone_tickets": len(standalone),
                              "checked_paths_against": str(checks.root) if checks.root else None},
                             indent=2, ensure_ascii=False))
        else:
            for s_ in summary:
                print(f"epic {s_['epic']}: {s_['tickets']} tickets ({s_['sizes']}), {effort_text(s_['effort_hours'])}, "
                      f"{s_['waves']} waves")
            if standalone:
                print(f"standalone tickets: {len(standalone)}")
            _print_problems(problems)
            print(f"\n{'OK' if not errors else 'FAILED'}: {len(errors)} errors, {len(warnings)} warnings")
        return 1 if errors else 0
    except UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
