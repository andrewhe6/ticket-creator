"""Tests for skills/ticket-creator/scripts/tickets.py.  Run: python -m unittest discover -s tests"""
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "ticket-creator" / "scripts" / "tickets.py"
spec = importlib.util.spec_from_file_location("tickets", SCRIPT)
tickets = importlib.util.module_from_spec(spec)
sys.modules["tickets"] = tickets  # dataclasses look the module up while the class is created
spec.loader.exec_module(tickets)

EPIC = """---
kind: epic
title: {title}
priority: high
labels: []
blocked_by: {blocked_by}
source: chat
slicing: vertical+enablers
---

## Goal

Users can sign in.

## Non-goals

- SSO

## Plan

<!-- ticket-creator:generated:start (rewritten by `tickets.py render`; edit the ticket files instead) -->
<!-- ticket-creator:generated:end -->
"""

TICKET = """---
id: {id}
title: {title}
type: feature
size: {size}
priority: high
labels: [backend]
blocked_by: {blocked_by}
---

## Context

Needed for login. Builds on T01.

## Scope

**In scope**
- the thing

**Out of scope**
- other things (T02)

## Acceptance criteria

- [ ] it works

## Technical notes

- Relevant code: `src/auth.ts`

## Verification

- Automated: `npm test`
"""


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tickets.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class DraftCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def epic(self, slug="auth", title="User authentication", blocked_by="[]", tickets_=None, root=None):
        folder = (root or self.tmp) / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "00-epic.md").write_text(EPIC.format(title=title, blocked_by=blocked_by), encoding="utf-8")
        for spec_ in tickets_ or [("T01", "Session model", "S", "[]"), ("T02", "Login endpoint", "M", "[T01]")]:
            tid, ttitle, size, deps = spec_
            slug = "".join(c if c.isalnum() else "-" for c in ttitle.lower()).strip("-")
            (folder / f"{tid[1:]}-{slug}.md").write_text(
                TICKET.format(id=tid, title=ttitle, size=size, blocked_by=deps), encoding="utf-8")
        return folder

    def problems(self, path, max_size="M"):
        epics, standalone, problems = tickets.load(path)
        return tickets.validate(epics, standalone, problems, max_size)

    def errors(self, path):
        return [m for level, _, m in self.problems(path) if level == "error"]


class ParsingTests(unittest.TestCase):
    def test_flat_yaml_subset(self):
        meta, body, errs = tickets.parse_frontmatter(
            "---\nid: T01\ntitle: \"Fix: crash # not a comment\"\ntype: feature   # trailing comment\n"
            "labels:\n  - a\n  - b\nblocked_by: [T02, \"#12\"]\nremote_key: null\nkey_with_colon: a: b\n---\nbody")
        self.assertEqual(errs, [])
        self.assertEqual(meta["title"], "Fix: crash # not a comment")
        self.assertEqual(meta["type"], "feature")
        self.assertEqual(meta["labels"], ["a", "b"])
        self.assertEqual(meta["blocked_by"], ["T02", "#12"])
        self.assertIsNone(meta["remote_key"])
        self.assertEqual(meta["key_with_colon"], "a: b")
        self.assertEqual(body, "body")

    def test_unquoted_hash_is_a_comment(self):
        meta, _, _ = tickets.parse_frontmatter("---\nremote_key: #42\ntitle: Fix export #42 crash\n---\n")
        self.assertIsNone(meta["remote_key"])
        self.assertEqual(meta["title"], "Fix export")  # standard YAML; the validator warns about it

    def test_bad_lines_and_duplicate_keys_are_reported(self):
        _, _, errs = tickets.parse_frontmatter("---\nid: T01\nid: T02\n  nested: x\n---\n")
        self.assertTrue(any("appears twice" in e for e in errs))
        self.assertTrue(any("not understood" in e for e in errs))

    def test_missing_frontmatter(self):
        meta, _, errs = tickets.parse_frontmatter("# just markdown")
        self.assertIsNone(meta)
        self.assertTrue(errs)

    def test_as_list_splits_commas_and_drops_empties(self):
        self.assertEqual(tickets.as_list("T01, T02"), ["T01", "T02"])
        self.assertEqual(tickets.as_list(["T01", None, " T03 "]), ["T01", "T03"])
        self.assertEqual(tickets.as_list(None), [])


class ValidateTests(DraftCase):
    def test_valid_epic_passes_after_render(self):
        folder = self.epic()
        self.assertEqual(self.errors(folder), [])
        code, _, _ = run("render", folder)
        self.assertEqual(code, 0)
        self.assertEqual([p for p in self.problems(folder)], [])

    def test_crlf_and_bom_files(self):
        folder = self.epic()
        for p in folder.glob("*.md"):
            p.write_bytes(b"\xef\xbb\xbf" + p.read_text(encoding="utf-8").replace("\n", "\r\n").encode())
        self.assertEqual(self.errors(folder), [])
        run("render", folder)
        self.assertIn(b"\r\n", (folder / "00-epic.md").read_bytes())
        self.assertEqual(self.errors(folder), [])

    def test_common_mistakes_are_caught(self):
        folder = self.epic(tickets_=[("T01", "A", "XL", "[t02]"), ("T02", "B", "S", "[T09]")])
        (folder / "03-c.md").write_text(TICKET.format(id="T03", title="[WIP]", size="S", blocked_by="[]")
                                        .replace("blocked_by: []", "depends_on: [T01]"), encoding="utf-8")
        (folder / "04-d.md").write_text(TICKET.format(id="T04", title="Fix export #42 crash", size="S",
                                                      blocked_by="[]"), encoding="utf-8")
        self.assertTrue(any("put the title in double quotes" in m for _, _, m in self.problems(folder)))
        errs = "\n".join(self.errors(folder))
        self.assertIn("XL is an epic", errs)
        self.assertIn("did you mean T02", errs)
        self.assertIn("'T09' matches no ticket id", errs)
        self.assertIn("rename it to `blocked_by`", errs)
        self.assertIn("title was read as a list", errs)

    def test_missing_sections_and_bad_enums(self):
        folder = self.epic()
        t = folder / "01-session-model.md"
        t.write_text(t.read_text(encoding="utf-8").replace("## Verification", "## Notes")
                     .replace("priority: high", "priority: P1").replace("type: feature", "type: story")
                     + "status: blocked\n", encoding="utf-8")
        t.write_text(t.read_text(encoding="utf-8").replace("labels: [backend]", "labels: [backend]\nstatus: blocked"),
                     encoding="utf-8")
        errs = "\n".join(self.errors(folder))
        self.assertIn("missing '## Verification'", errs)
        self.assertIn("priority 'p1'", errs)
        self.assertIn("type 'story'", errs)
        self.assertIn("status 'blocked'", errs)

    def test_large_ticket_is_a_warning_and_cycles_are_errors(self):
        folder = self.epic(tickets_=[("T01", "A", "L", "[T02]"), ("T02", "B", "S", "[T01]")])
        problems = self.problems(folder)
        self.assertTrue(any(l == "warning" and "over the max" in m for l, _, m in problems))
        self.assertTrue(any(l == "error" and m.startswith("cycle:") for l, _, m in problems))
        self.assertFalse(any("over the max" in m for _, _, m in self.problems(folder, max_size="L")))

    def test_existing_epic_needs_remote(self):
        folder = self.epic()
        e = folder / "00-epic.md"
        e.write_text(e.read_text(encoding="utf-8").replace("slicing:", "existing: true\nslicing:"), encoding="utf-8")
        self.assertTrue(any("existing: true" in m for m in self.errors(folder)))

    def test_multi_epic_backlog_with_cross_epic_refs(self):
        root = self.tmp / "proj"
        self.epic("skeleton", root=root)
        self.epic("feature", blocked_by="[skeleton]", root=root,
                  tickets_=[("T01", "Uses skeleton", "S", "[skeleton/T02]"), ("T02", "X", "S", "[skeleton/T07]")])
        errs = self.errors(root)
        self.assertEqual(len(errs), 1)
        self.assertIn("no T07 in epic skeleton", errs[0])

    def test_epic_dependency_cycle(self):
        root = self.tmp / "proj"
        self.epic("a", blocked_by="[b]", root=root)
        self.epic("b", blocked_by="[a]", root=root)
        self.assertTrue(any(m.startswith("cycle:") for m in self.errors(root)))

    def test_standalone_tickets(self):
        single = self.tmp / "_single"
        single.mkdir()
        (single / "export-timeout.md").write_text(
            TICKET.format(id="T01", title="Export works for 10k rows", size="S", blocked_by='["#12"]'), encoding="utf-8")
        self.assertEqual(self.errors(single), [])
        self.assertEqual(self.errors(single / "export-timeout.md"), [])
        (single / "other.md").write_text(TICKET.format(id="T02", title="Other", size="S", blocked_by="[T01]"),
                                         encoding="utf-8")
        self.assertTrue(any("standalone ticket" in m for m in self.errors(single)))

    def test_unreadable_and_missing_inputs(self):
        folder = self.epic()
        (folder / "09-binary.md").write_bytes(b"\xff\xfe\x00garbage")
        code, out, _ = run("validate", folder)
        self.assertEqual(code, 0)  # ignored with a warning, not a crash
        self.assertIn("not valid UTF-8", out)
        code, _, err = run("validate", self.tmp / "nope")
        self.assertEqual(code, 2)
        self.assertIn("does not exist", err)
        empty = self.tmp / "empty"
        empty.mkdir()
        code, _, err = run("validate", empty)
        self.assertEqual(code, 2)

    def test_json_output(self):
        folder = self.epic()
        code, out, _ = run("validate", folder, "--json")
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertEqual(data["epics"][0]["tickets"], 2)
        self.assertEqual(data["epics"][0]["sizes"], "1 S, 1 M")
        self.assertEqual(data["epics"][0]["effort_hours"], 12)


class RenderTests(DraftCase):
    def test_render_is_idempotent_and_complete(self):
        folder = self.epic(tickets_=[("T01", "A", "S", "[]"), ("T02", "B", "S", "[]"),
                                     ("T03", '"C \\"q\\" #1"', "M", "[T01, T02]")])
        run("render", folder)
        first = (folder / "00-epic.md").read_text(encoding="utf-8")
        code, out, _ = run("render", folder)
        self.assertIn("already up to date", out)
        self.assertEqual(first, (folder / "00-epic.md").read_text(encoding="utf-8"))
        self.assertIn("Wave 1 - start here, in parallel:** T01, T02", first)
        self.assertIn("T01 --> T03", first)
        self.assertIn("#quot;q#quot; #35;1", first)
        self.assertIn("**3 tickets** · 2 S, 1 M · about 2 dev-days of work", first)

    def test_status_column_appears_only_when_used(self):
        folder = self.epic()
        run("render", folder)
        self.assertNotIn("Status |", (folder / "00-epic.md").read_text(encoding="utf-8"))
        t = folder / "01-session-model.md"
        t.write_text(t.read_text(encoding="utf-8").replace("labels:", "status: done\nlabels:"), encoding="utf-8")
        run("render", folder)
        self.assertIn("| done |", (folder / "00-epic.md").read_text(encoding="utf-8"))

    def test_missing_markers_append_plan_and_damaged_markers_fail(self):
        folder = self.epic()
        e = folder / "00-epic.md"
        e.write_text(e.read_text(encoding="utf-8").split("## Plan")[0], encoding="utf-8")
        run("render", folder)
        self.assertIn("## Plan", e.read_text(encoding="utf-8"))
        e.write_text(e.read_text(encoding="utf-8").replace(tickets.GEN_END, ""), encoding="utf-8")
        code, _, err = run("render", folder)
        self.assertEqual(code, 2)
        self.assertIn("markers are damaged", err)


class PushHelperTests(DraftCase):
    def test_plan_refuses_invalid_drafts(self):
        folder = self.epic(tickets_=[("T01", "A", "XL", "[]")])
        code, _, err = run("plan", folder)
        self.assertEqual(code, 1)
        self.assertIn("validation errors", err)
        code, _, _ = run("plan", folder, "--force")
        self.assertEqual(code, 0)

    def test_plan_order_and_resume(self):
        folder = self.epic(tickets_=[("T02", "Second", "S", "[T01]"), ("T01", "First", "S", "[]")])
        run("render", folder)
        code, out, _ = run("plan", folder, "--json")
        items = json.loads(out)
        self.assertEqual([i["id"] for i in items], ["auth", "T01", "T02"])
        self.assertTrue(all(i["push"] == "pending" for i in items))
        t01 = next(folder.glob("01-*.md"))
        run("set-remote", t01, "--key", "#41", "--url", "https://github.com/o/r/issues/41", "--id", "9001",
            "--platform", "github")
        items = json.loads(run("plan", folder, "--json")[1])
        self.assertEqual(items[1]["push"], "pushed")
        self.assertEqual(items[2]["blocked_by"][0], {"ref": "T01", "remote_key": "#41", "remote_id": "9001"})

    def test_set_remote_protects_existing_links(self):
        folder = self.epic()
        t = next(folder.glob("01-*.md"))
        self.assertEqual(run("set-remote", t, "--key", "#41", "--url", "u1")[0], 0)
        self.assertEqual(run("set-remote", t, "--key", "#41", "--url", "u1b")[0], 0)  # same key: fine
        code, _, err = run("set-remote", t, "--key", "#99", "--url", "u2")
        self.assertEqual(code, 2)
        self.assertIn("Refusing to overwrite", err)
        self.assertEqual(run("set-remote", t, "--key", "#99", "--url", "u2", "--force")[0], 0)
        meta, _, _ = tickets.parse_frontmatter(t.read_text(encoding="utf-8"))
        self.assertEqual(meta["remote_key"], "#99")

    def test_body_rewrites_ids_and_strips_comments(self):
        folder = self.epic()
        run("render", folder)
        run("set-remote", folder / "00-epic.md", "--key", "#40", "--url", "https://x/40")
        run("set-remote", next(folder.glob("01-*.md")), "--key", "#41", "--url", "https://x/41")
        t2 = next(folder.glob("02-*.md"))
        t2.write_text(t2.read_text(encoding="utf-8") + "\n<!-- guide: leftover -->\n```\nT01 stays in code\n```\n",
                      encoding="utf-8")
        code, out, _ = run("body", t2)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("**Epic:** #40 · **Blocked by:** #41 · **Size:** M"))
        self.assertIn("Builds on #41.", out)
        self.assertIn("T01 stays in code", out)
        self.assertNotIn("<!--", out)
        self.assertNotIn("id: T02", out)
        run("render", folder)  # the push flow re-renders before updating the epic body
        epic_body = run("body", folder / "00-epic.md")[1]
        self.assertNotIn("<!--", epic_body)
        self.assertIn("[#41](https://x/41)", epic_body)

    def test_body_cross_epic_and_out_file(self):
        root = self.tmp / "proj"
        sk = self.epic("skeleton", root=root)
        ft = self.epic("feature", root=root, blocked_by="[skeleton]",
                       tickets_=[("T01", "Uses skeleton", "S", "[skeleton/T02]")])
        run("set-remote", next(sk.glob("02-*.md")), "--key", "NS-5", "--url", "https://j/NS-5")
        t = next(ft.glob("01-*.md"))
        t.write_text(t.read_text(encoding="utf-8") + "\nSee skeleton/T02, skeleton/T01, src/T05 and T01.\n",
                     encoding="utf-8")
        out_file = self.tmp / "out" / "body.md"
        code, _, _ = run("body", t, "--out", out_file)
        self.assertEqual(code, 0)
        text = out_file.read_text(encoding="utf-8")
        self.assertIn("**Blocked by:** NS-5", text)
        self.assertIn("See NS-5, skeleton/T01, src/T05 and T01.", text)

    def test_unwrap_paragraphs(self):
        src = ("A wrapped\nparagraph here.\n\n**In scope**\n- item one\n  continues here\n- item two\n\n"
               "| a | b |\n|---|---|\n| 1 | 2 |\n\n```\nkeep\nlines\n```\nHard break  \nnext line\n## Heading\ntext")
        out = tickets.unwrap_paragraphs(src)
        self.assertIn("A wrapped paragraph here.", out)
        self.assertIn("**In scope**\n- item one continues here\n- item two", out)
        self.assertIn("| a | b |\n|---|---|\n| 1 | 2 |", out)
        self.assertIn("```\nkeep\nlines\n```", out)
        self.assertIn("Hard break  \nnext line", out)
        self.assertIn("## Heading\ntext", out)

    def test_body_unwraps_by_default(self):
        folder = self.epic()
        t = next(folder.glob("01-*.md"))
        t.write_text(t.read_text(encoding="utf-8").replace("Needed for login. Builds on T01.",
                                                           "Needed for\nlogin."), encoding="utf-8")
        self.assertIn("Needed for login.", run("body", t)[1])
        self.assertIn("Needed for\nlogin.", run("body", t, "--keep-wraps")[1])

    def test_stale_plan_block_is_flagged(self):
        folder = self.epic()
        run("render", folder)
        self.assertEqual(self.problems(folder), [])
        t = next(folder.glob("02-*.md"))
        t.write_text(t.read_text(encoding="utf-8").replace("size: M", "size: S"), encoding="utf-8")
        self.assertTrue(any("out of date" in m for _, _, m in self.problems(folder)))
        run("render", folder)
        self.assertEqual(self.problems(folder), [])

    def test_plan_block_hand_edits_and_old_versions_are_told_apart(self):
        folder = self.epic()
        run("render", folder)
        e = folder / "00-epic.md"
        rendered = e.read_text(encoding="utf-8")
        e.write_text(rendered.replace("### Build order", "### Build order (my notes)"), encoding="utf-8")
        self.assertTrue(any("edited by hand" in m for _, _, m in self.problems(folder)))
        old = re.sub(r" v=\d+ sig=[0-9a-f]+", "", rendered).replace("in parallel", "these can run in parallel")
        e.write_text(old, encoding="utf-8")
        self.assertTrue(any("another version" in m for _, _, m in self.problems(folder)))
        fresh = re.sub(r"(generated:start[^\n]*-->).*?(<!-- ticket-creator:generated:end)", r"\1\n\2",
                       rendered, flags=re.S)
        e.write_text(fresh, encoding="utf-8")
        self.assertTrue(any("no Plan yet" in m for _, _, m in self.problems(folder)))

    def test_waves_count_across_epics(self):
        root = self.tmp / "proj"
        self.epic("skeleton", root=root)  # T01 (wave 1) -> T02 (wave 2)
        self.epic("feature", root=root, blocked_by="[skeleton]",
                  tickets_=[("T01", "Needs skeleton", "S", "[skeleton/T02]"), ("T02", "Independent", "S", "[]")])
        epics, _, _ = tickets.load(root)
        feature = next(e for e in epics if e.slug == "feature")
        self.assertEqual(tickets.waves_for(feature, epics), {"T01": 3, "T02": 1})
        run("render", root)
        text = (root / "feature" / "00-epic.md").read_text(encoding="utf-8")
        self.assertIn("**Wave 3:** T01", text)
        self.assertIn("count across the whole backlog", text)

    def test_render_standalone_is_a_no_op(self):
        single = self.tmp / "_single"
        single.mkdir()
        (single / "bug.md").write_text(TICKET.format(id="T01", title="Bug", size="XS", blocked_by="[]"),
                                       encoding="utf-8")
        code, out, _ = run("render", single)
        self.assertEqual(code, 0)
        self.assertIn("nothing to render", out)

    def test_related_may_name_tickets_in_the_epic(self):
        folder = self.epic()
        t = next(folder.glob("02-*.md"))
        t.write_text(t.read_text(encoding="utf-8").replace("labels:", 'related: [T01, "#9", banana]\nlabels:'),
                     encoding="utf-8")
        warns = [m for l, _, m in self.problems(folder) if "related" in m]
        self.assertEqual(len(warns), 1)
        self.assertIn("banana", warns[0])
        self.assertNotIn("T01", warns[0])

    def test_body_for_standalone_ticket(self):
        single = self.tmp / "_single"
        single.mkdir()
        f = single / "bug.md"
        f.write_text(TICKET.format(id="T01", title="Bug", size="XS", blocked_by='["#12"]'), encoding="utf-8")
        out = run("body", f)[1]
        self.assertTrue(out.startswith("**Blocked by:** #12 · **Size:** XS"))


if __name__ == "__main__":
    unittest.main()
