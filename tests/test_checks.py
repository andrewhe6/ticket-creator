"""Content checks, config, templates, change tracking, and rendering details of tickets.py.
Run: python -m unittest discover -s tests"""
import json
import re
import unittest
from pathlib import Path

from test_tickets import EPIC, TICKET, DraftCase, run, tickets


def ticket_text(**over):
    """A valid ticket draft with selected parts replaced."""
    meta = {"id": "T01", "title": "Session model", "type": "feature", "size": "S", "priority": "high",
            "labels": "[backend]", "blocked_by": "[]"}
    meta.update({k: v for k, v in over.items() if k in meta or k in ("due", "links", "assignee", "status")})
    secs = {
        "Context": "Needed for login.",
        "Scope": "**In scope**\n- the thing\n\n**Out of scope**\n- other things",
        "Acceptance criteria": "- [ ] it works",
        "Technical notes": "- Relevant code: `src/auth.ts`",
        "Verification": "- Automated: `npm test`",
    }
    secs.update(over.get("sections", {}))
    for name in over.get("drop", []):
        secs.pop(name)
    fm = "\n".join(f"{k}: {v}" for k, v in meta.items())
    body = "\n\n".join(f"## {k}\n\n{v}" for k, v in secs.items())
    return f"---\n{fm}\n---\n\n{body}\n" + over.get("extra", "")


class CheckCase(DraftCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "auth.ts").write_text("export {}\n", encoding="utf-8")
        (self.repo / "package.json").write_text("{}\n", encoding="utf-8")
        self.folder = self.epic(root=self.repo / "tickets")
        run("render", self.folder)

    def write(self, name="01-session-model.md", **over):
        path = self.folder / name
        path.write_text(ticket_text(**over), encoding="utf-8")
        run("render", self.folder)
        return path

    def check(self, path=None):
        path = path or self.folder
        epics, standalone, problems = tickets.load(path)
        checks = tickets.build_checks(path)
        return tickets.validate(epics, standalone, problems, checks=checks)

    def messages(self, level=None, path=None):
        return [m for lvl, _, m in self.check(path) if level is None or lvl == level]


class SecretAndPrivacyTests(CheckCase):
    def test_clean_drafts_have_no_findings(self):
        self.write()
        self.assertEqual(self.check(), [])

    def test_credential_formats_are_errors_and_masked(self):
        samples = {
            "an AWS access key": "AKIAQWERTYUIOPASDFGH",
            "a GitHub token": "ghp_" + "a1B2" * 9,
            "a Slack token": "xoxb-1234567890-abcdefghij",
            "a private key": "-----BEGIN RSA PRIVATE KEY-----",
            "a Stripe key": "sk_live_" + "x" * 24,
        }
        for what, secret in samples.items():
            with self.subTest(what=what):
                self.write(sections={"Context": f"The key {secret} is in the env."})
                errs = [m for m in self.messages("error") if "looks like it contains" in m]
                self.assertEqual(len(errs), 1, errs)
                self.assertIn(what, errs[0])
                self.assertNotIn(secret, errs[0])  # never echo the secret back

    def test_secret_in_title_blocks_plan(self):
        self.write(title='"Rotate AKIAQWERTYUIOPASDFGH"')
        code, _, err = run("plan", self.folder)
        self.assertEqual(code, 1)
        self.assertIn("AWS access key", err)

    def test_url_credentials(self):
        self.write(sections={"Context": "Connect with `postgres://app:Sup3rS3cret@db.prod.internal/app`."})
        self.assertTrue(any("URL contains a password" in m for m in self.messages("error")))
        self.write(sections={"Context": "Locally: `postgres://postgres:postgres@localhost:5432/dev`."})
        self.assertEqual([m for m in self.messages("error") if "URL" in m], [])
        self.assertTrue(any("local dev default" in m for m in self.messages("warning")))
        self.write(sections={"Context": "Use `smtp://user:<password>@smtp.example.com`."})
        self.assertFalse(any("URL contains" in m for m in self.messages()))

    def test_assignments_warn_but_placeholders_and_env_vars_dont(self):
        self.write(sections={"Context": "Set password=hunter22 in the test env."})
        self.assertTrue(any("`password` seems to be given a real value" in m for m in self.messages("warning")))
        for safe in ("api_key = STRIPE_KEY", "password: <your password>", "secret=${APP_SECRET}",
                     "api_key: process.env.KEY", "password: changeme"):
            with self.subTest(safe=safe):
                self.write(sections={"Context": f"Config: `{safe}`."})
                self.assertFalse(any("real value" in m for m in self.messages()), safe)

    def test_personal_data_warns(self):
        self.write(sections={"Context": "Reported by jane.doe@acme.com, call +1 415 555 0134."})
        warns = [m for m in self.messages("warning") if "personal data" in m]
        self.assertEqual(len(warns), 1)
        self.assertIn("email address", warns[0])
        self.assertIn("phone number", warns[0])
        self.assertNotIn("jane.doe@acme.com", warns[0])

    def test_example_and_role_addresses_are_fine(self):
        self.write(sections={"Context": "Send from noreply@acme.com to user@example.com; dates like 2026-03-29."})
        self.assertFalse(any("personal data" in m for m in self.messages()))


class PlaceholderTests(CheckCase):
    def test_placeholder_only_sections_are_errors(self):
        self.write(sections={"Verification": "- Automated: TBD\n- Manual: none"})
        self.assertTrue(any("'## Verification' holds only a placeholder" in m for m in self.messages("error")))
        self.write(sections={"Technical notes": "n/a"})
        self.assertTrue(any("'## Technical notes' holds only a placeholder" in m for m in self.messages("error")))

    def test_only_template_labels_counts_as_empty(self):
        self.write(sections={"Technical notes": "- Relevant code:\n- Approach:\n- Watch out for:"})
        self.assertTrue(any("is empty (only template labels)" in m for m in self.messages("error")))

    def test_inline_tbd_warns_but_not_inside_code(self):
        self.write(sections={"Context": "The retry limit is TBD."})
        self.assertTrue(any("contains a placeholder (TBD)" in m for m in self.messages("warning")))
        self.write(sections={"Context": "Rename the `TBD_FLAG` constant."})
        self.assertFalse(any("placeholder" in m for m in self.messages()))

    def test_empty_template_lines_warn_unless_they_have_sub_bullets(self):
        self.write(sections={"Technical notes": "- Relevant code: `src/auth.ts`\n- Approach:\n- Watch out for: x"})
        self.assertTrue(any("'- Approach:'" in m for m in self.messages("warning")))
        self.write(sections={"Technical notes": "- Relevant code: `src/auth.ts`\n- Approach:\n  - reuse the session store"})
        self.assertFalse(any("template lines" in m for m in self.messages()))

    def test_template_values_left_in_frontmatter(self):
        self.write(title="<Outcome-focused title>")
        self.assertTrue(any("`title` still holds the template placeholder" in m for m in self.messages("error")))
        epic = self.folder / "00-epic.md"
        epic.write_text(epic.read_text(encoding="utf-8").replace("source: chat", "source: <where this came from>"),
                        encoding="utf-8")
        self.assertTrue(any("`source` still holds" in m for m in self.messages("error")))

    def test_epic_goal_placeholder(self):
        epic = self.folder / "00-epic.md"
        epic.write_text(epic.read_text(encoding="utf-8").replace("Users can sign in.", "TBD"), encoding="utf-8")
        self.assertTrue(any("'## Goal' holds only a placeholder" in m for m in self.messages("error")))

    def test_no_non_goals_is_a_valid_answer_but_tbd_is_not(self):
        epic = self.folder / "00-epic.md"
        epic.write_text(epic.read_text(encoding="utf-8").replace("- SSO", "- None"), encoding="utf-8")
        self.assertFalse(any("Non-goals" in m for m in self.messages()))
        epic.write_text(epic.read_text(encoding="utf-8").replace("- None", "- TBD"), encoding="utf-8")
        self.assertTrue(any("'## Non-goals' holds only a placeholder" in m for m in self.messages("error")))


class PathTests(CheckCase):
    def test_missing_path_in_technical_notes_warns(self):
        self.write(sections={"Technical notes": "- Relevant code: `src/auth.ts`, `src/made/up.ts: fakeFn()`"})
        warns = [m for m in self.messages("warning") if "cites paths" in m]
        self.assertEqual(len(warns), 1)
        self.assertIn("`src/made/up.ts`", warns[0])
        self.assertNotIn("src/auth.ts", warns[0])

    def test_line_refs_and_dirs(self):
        self.write(sections={"Technical notes": "- See `src/auth.ts:12-20`, `src/auth.ts#L4`, `src/` and `lib/`"})
        warns = [m for m in self.messages("warning") if "cites paths" in m]
        self.assertEqual(len(warns), 1)
        self.assertIn("`lib/`", warns[0])
        self.assertNotIn("src/", warns[0].replace("lib/", ""))

    def test_new_files_and_guesses_are_not_flagged(self):
        for line in ("- Add `src/jobs/reminders.ts` (new file)", "- Likely in `src/jobs/`",
                     "- Create `src/jobs/reminders.ts`", "- e.g. `src/x/y.ts`"):
            with self.subTest(line=line):
                self.write(sections={"Technical notes": line})
                self.assertFalse(any("cites paths" in m for m in self.messages()), line)

    def test_paths_another_ticket_creates_are_not_flagged(self):
        self.write("01-session-model.md", sections={"Technical notes": "- New file `src/mailer/send.ts`"})
        self.write("02-login-endpoint.md", id="T02", title="Login endpoint", blocked_by="[T01]",
                   sections={"Technical notes": "- Relevant code: `src/mailer/send.ts`, `src/mailer/`"})
        self.assertFalse(any("cites paths" in m for m in self.messages()))

    def test_non_paths_are_ignored(self):
        self.write(sections={"Technical notes": "- `POST /api/me`, `/api/todos/:id`, `Europe/Berlin`, "
                                                "`application/json`, `github.com/spf13/cobra`, `@types/node`, "
                                                "`owner/repo`, `dist/app.js`, `node_modules/x/y.js`, "
                                                "`https://x.io/a.md`, `src/*.ts`"})
        self.assertFalse(any("cites paths" in m for m in self.messages()))

    def test_example_paths_in_acceptance_criteria_are_ignored(self):
        self.write(sections={"Acceptance criteria": "- [ ] Given `notes/private/a.md`, it isn't published"})
        self.assertFalse(any("cites paths" in m for m in self.messages()))

    def test_greenfield_and_no_codebase_skip_the_check(self):
        self.write(sections={"Technical notes": "- Relevant code: not verified, drafted without the codebase. "
                                                "`src/nope.ts`"})
        self.assertFalse(any("cites paths" in m for m in self.messages()))
        green = self.tmp / "green"
        (green / ".git").mkdir(parents=True)
        (green / "README.md").write_text("# idea\n", encoding="utf-8")
        folder = self.epic(root=green / "tickets")
        t = next(folder.glob("01-*.md"))
        t.write_text(t.read_text(encoding="utf-8").replace("`src/auth.ts`", "`cmd/app/main.go`"), encoding="utf-8")
        self.assertIsNone(tickets.build_checks(folder).root)
        self.assertFalse(any("cites paths" in m for m in self.messages(path=folder)))

    def test_root_override_and_no_repo(self):
        self.write(sections={"Technical notes": "- `src/made/up.ts`"})
        code, out, _ = run("validate", self.folder, "--root", self.repo)
        self.assertIn("cites paths", out)
        loose = self.tmp / "loose"
        folder = self.epic(root=loose)  # no .git, no config: nothing to check against
        self.assertIsNone(tickets.build_checks(folder).root)


class SizingTests(CheckCase):
    def test_many_acceptance_criteria_for_the_size(self):
        acs = "\n".join(f"- [ ] case {i}" for i in range(9))
        self.write(size="S", sections={"Acceptance criteria": acs})
        self.assertTrue(any("9 acceptance criteria, a lot for size S" in m for m in self.messages("warning")))
        self.write(size="M", sections={"Acceptance criteria": acs})
        self.assertFalse(any("acceptance criteria, a lot" in m for m in self.messages()))

    def test_risky_scope_on_a_small_ticket(self):
        scope = ("**In scope**\n- Run the reminder scheduler daily at 08:00 in the configured time zone\n"
                 "- Never overlap two runs\n\n**Out of scope**\n- retries (T02)")
        self.write(size="S", sections={"Scope": scope})
        warns = [m for m in self.messages("warning") if "may be low" in m]
        self.assertEqual(len(warns), 1)
        self.assertIn("time zones/DST", warns[0])
        self.assertIn("scheduling", warns[0])
        self.write(size="M", sections={"Scope": scope})
        self.assertFalse(any("may be low" in m for m in self.messages()))

    def test_risk_terms_only_in_out_of_scope_dont_count(self):
        scope = "**In scope**\n- a label\n\n**Out of scope**\n- time zones, cron scheduler, retries (T02)"
        self.write(size="XS", sections={"Scope": scope})
        self.assertFalse(any("may be low" in m for m in self.messages()))

    def test_long_ticket_and_code_heavy_notes(self):
        self.write(sections={"Context": "word " * 820})
        self.assertTrue(any("words long" in m for m in self.messages("warning")))
        code = "```sql\n" + "\n".join("SELECT a, b, c FROM some_table WHERE x = 1;" for _ in range(12)) + "\n```"
        self.write(sections={"Technical notes": "- Relevant code: `src/auth.ts`\n\n" + code})
        self.assertTrue(any("spell out about" in m for m in self.messages("warning")))


class TypeTemplateAndFieldTests(CheckCase):
    def test_task_type_needs_no_technical_notes(self):
        self.write(type="task", drop=["Technical notes"])
        self.assertEqual(self.messages("error"), [])
        self.write(type="feature", drop=["Technical notes"])
        self.assertTrue(any("missing '## Technical notes'" in m for m in self.messages("error")))

    def test_custom_template_sets_required_sections(self):
        tpl = self.tmp / "team-template.md"
        tpl.write_text("---\nid: T01\n---\n\n## Problem\n\n## Scope\n\n## Acceptance criteria\n\n"
                       "## Design notes <!-- optional-for: task, chore -->\n\n## Rollout <!-- optional -->\n",
                       encoding="utf-8")
        rules = tickets.load_template_rules(tpl)
        self.assertEqual([r.name for r in rules], ["Problem", "Scope", "Acceptance criteria", "Design notes", "Rollout"])
        self.assertEqual(rules[3].optional_for, frozenset({"task", "chore"}))
        self.assertEqual(rules[4].optional_for, frozenset({"*"}))
        self.write(sections={"Problem": "Users can't sign in."})
        code, out, _ = run("validate", self.folder, "--template", tpl)
        self.assertEqual(code, 1)
        self.assertIn("missing '## Design notes'", out)
        self.assertNotIn("Rollout", out)
        self.assertNotIn("Verification", out)  # not in the team's template
        chore = self.write(type="chore", sections={"Problem": "x"})
        code, out, _ = run("validate", chore, "--template", tpl)
        self.assertNotIn("missing '## Design notes'", out)
        self.assertEqual(code, 0)

    def test_template_without_headings_is_a_usage_error(self):
        tpl = self.tmp / "empty.md"
        tpl.write_text("just text\n", encoding="utf-8")
        code, _, err = run("validate", self.folder, "--template", tpl)
        self.assertEqual(code, 2)
        self.assertIn("no `## ` section headings", err)

    def test_optional_fields(self):
        self.write(due="2026-12-01", links="[\"https://figma.com/file/abc\"]", assignee="octocat")
        self.assertEqual(self.messages("error"), [])
        self.write(due="next week", links="[figma.com/x]")
        errs = "\n".join(self.messages("error"))
        self.assertIn("due 'next week' must be a date", errs)
        self.assertIn("links must be full URLs", errs)

    def test_field_aliases_warn(self):
        path = self.write()
        path.write_text(path.read_text(encoding="utf-8").replace("labels:", "due_date: 2026-12-01\nlabels:"),
                        encoding="utf-8")
        self.assertTrue(any("rename it to `due`" in m for m in self.messages("warning")))


class ConfigTests(DraftCase):
    YML = """# comment
version: 1
platform: github   # trailing comment
drafts_dir: tickets
template: .claude/ticket-template.md
defaults:
  priority: medium
  max_size: S
  labels: [team-a, "needs: triage"]
  create_missing_labels: never
type_labels: { feature: feature, bug: "type: bug" }
estimates:
  XS: 1
  S: 2
github:
  repo: acme/app
  project:
    owner: acme
    number: 3
    status: "In progress"
list_key:
- a
- b
"""

    def test_yaml_subset(self):
        data = tickets.parse_yaml_subset(self.YML)
        self.assertEqual(data["platform"], "github")
        self.assertEqual(data["defaults"]["labels"], ["team-a", "needs: triage"])
        self.assertEqual(data["type_labels"], {"feature": "feature", "bug": "type: bug"})
        self.assertEqual(data["estimates"], {"XS": 1, "S": 2})
        self.assertEqual(data["github"]["project"], {"owner": "acme", "number": 3, "status": "In progress"})
        self.assertEqual(data["list_key"], ["a", "b"])

    def test_bundled_config_template_parses(self):
        template = Path(tickets.__file__).resolve().parents[1] / "assets" / "config.template.yml"
        data = tickets.parse_yaml_subset(template.read_text(encoding="utf-8"))
        self.assertEqual(data["github"]["project"]["number"], 1)
        self.assertEqual(tickets.check_config(tickets._deep_merge(tickets.DEFAULT_CONFIG, data)), [])

    def test_yaml_errors(self):
        for text, expect in (("a: 1\n\tb: 2\n", "tabs"), ("a: 1\na: 2\n", "appears twice"),
                             ("a:\n  - x: 1\n", "lists of `key: value`"), ("a: [1, 2\n", "isn't closed"),
                             ("a: 1\n    b: 2\n", "unexpected indentation"), ("just words\n", "expected `key: value`")):
            with self.subTest(text=text):
                with self.assertRaises(tickets.UserError) as ctx:
                    tickets.parse_yaml_subset(text)
                self.assertIn(expect, str(ctx.exception))

    def test_config_is_found_and_used_by_validate(self):
        project = self.tmp / "proj"
        (project / ".git").mkdir(parents=True)
        (project / ".claude").mkdir()
        (project / ".claude" / "ticket-creator.yml").write_text(self.YML.replace("template: .claude/ticket-template.md\n", ""),
                                                                encoding="utf-8")
        folder = self.epic(root=project / "tickets")  # T02 is size M; the config's max is S
        run("render", folder)
        cfg, path, root = tickets.load_config(folder)
        self.assertEqual(path, (project / ".claude" / "ticket-creator.yml").resolve())
        self.assertEqual(root, project.resolve())
        code, out, _ = run("validate", folder)
        self.assertIn("over the max (S)", out)
        code, out, _ = run("validate", folder, "--max-size", "M")
        self.assertNotIn("over the max", out)
        code, out, _ = run("config", folder, "--json")
        data = json.loads(out)
        self.assertEqual(data["config"]["github"]["repo"], "acme/app")
        self.assertEqual(data["config"]["defaults"]["priority"], "medium")
        self.assertEqual(code, 0)

    def test_config_search_stops_at_the_git_root(self):
        outer = self.tmp / "outer"
        (outer / ".claude").mkdir(parents=True)
        (outer / ".claude" / "ticket-creator.yml").write_text("platform: jira\n", encoding="utf-8")
        inner = outer / "inner"
        (inner / ".git").mkdir(parents=True)
        self.assertIsNone(tickets.find_config(inner))

    def test_check_config_flags_bad_values(self):
        cfg = tickets._deep_merge(tickets.DEFAULT_CONFIG, {"platform": "trello", "defaults": {"max_size": "XL"},
                                                           "github": {"size_as": "field"}, "colour": "red"})
        problems = {k: (lvl, m) for lvl, k, m in tickets.check_config(cfg)}
        self.assertEqual(problems["platform"][0], "error")
        self.assertEqual(problems["defaults.max_size"][0], "error")
        self.assertEqual(problems["github.size_as"][0], "error")
        self.assertEqual(problems["colour"][0], "warning")


class ChangeTrackingTests(DraftCase):
    def setUp(self):
        super().setUp()
        self.folder = self.epic()
        run("render", self.folder)
        self.t1 = next(self.folder.glob("01-*.md"))
        run("set-remote", self.folder / "00-epic.md", "--key", "#40", "--url", "https://x/40")
        run("set-remote", self.t1, "--key", "#41", "--url", "https://x/41", "--id", "9041", "--platform", "github")

    def state(self, path=None):
        items = json.loads(run("plan", self.folder, "--json", "--force")[1])
        return {i["id"]: i["push"] for i in items}

    def test_set_remote_records_fingerprints(self):
        meta, _, _ = tickets.parse_frontmatter(self.t1.read_text(encoding="utf-8"))
        for key in ("remote_hash", "remote_sent", "remote_blocked_by", "remote_pushed_at"):
            self.assertIn(key, meta)
        self.assertEqual(meta["remote_blocked_by"], [])
        self.assertEqual(self.state(), {"auth": "pushed", "T01": "pushed", "T02": "pending"})

    def test_edits_after_push_are_detected(self):
        self.t1.write_text(self.t1.read_text(encoding="utf-8").replace("it works", "it works on mobile"),
                           encoding="utf-8")
        self.assertEqual(self.state()["T01"], "changed")
        code, out, _ = run("validate", self.folder)
        self.assertIn("edited after it was pushed as #41", out)
        code, out, _ = run("diff", self.folder)
        self.assertIn("#41 T01: changed since its push", out)

    def test_dependency_changes_are_listed(self):
        self.t1.write_text(re.sub(r"^blocked_by: \[\]", 'blocked_by: ["#7"]', self.t1.read_text(encoding="utf-8"),
                                  flags=re.M), encoding="utf-8")
        data = json.loads(run("diff", self.folder, "--json")[1])
        self.assertEqual(data[0]["blocked_by_added"], ["#7"])

    def test_bookkeeping_edits_do_not_count(self):
        text = self.t1.read_text(encoding="utf-8")
        text = text.replace("labels:", "status: in-progress\nlabels:") + "\n<!-- a note to self -->\n"
        self.t1.write_text(text.replace("labels: [backend]", "labels: [ backend ]"), encoding="utf-8")
        run("render", self.folder)  # the epic's Plan block changes; that isn't an edit to the epic
        self.assertEqual(self.state()["T01"], "pushed")
        self.assertEqual(self.state()["auth"], "pushed")

    def test_mark_pushing_and_uncertain_state(self):
        t2 = next(self.folder.glob("02-*.md"))
        self.assertEqual(run("mark-pushing", t2)[0], 0)
        self.assertEqual(self.state()["T02"], "uncertain")
        self.assertIn("no key was recorded", run("validate", self.folder)[1])
        run("set-remote", t2, "--key", "#42", "--url", "https://x/42")
        self.assertNotIn("push_started", t2.read_text(encoding="utf-8"))
        self.assertEqual(self.state()["T02"], "pushed")
        code, _, err = run("mark-pushing", t2)
        self.assertEqual(code, 2)
        self.assertIn("already pushed", err)

    def test_set_remote_same_key_resyncs(self):
        self.t1.write_text(self.t1.read_text(encoding="utf-8").replace("it works", "it really works"),
                           encoding="utf-8")
        self.assertEqual(self.state()["T01"], "changed")
        run("set-remote", self.t1, "--key", "#41", "--url", "https://x/41")
        self.assertEqual(self.state()["T01"], "pushed")

    def test_crlf_preserved_by_frontmatter_updates(self):
        self.t1.write_bytes(self.t1.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        tickets.update_frontmatter(self.t1, {"remote_board": "acme/1"})
        raw = self.t1.read_bytes()
        self.assertIn(b'remote_board: "acme/1"\r\n', raw)
        self.assertNotIn(b"\r\r", raw)


class RenderAndBodyTests(DraftCase):
    def test_external_blockers_in_the_graph(self):
        root = self.tmp / "proj"
        self.epic("skeleton", root=root)
        feature = self.epic("feature", root=root, blocked_by="[skeleton]",
                            tickets_=[("T01", "Uses skeleton", "S", '[skeleton/T02, "#12"]')])
        run("render", root)
        text = (feature / "00-epic.md").read_text(encoding="utf-8")
        self.assertIn('X_skeleton_T02["skeleton/T02"]:::external', text)
        self.assertIn('X__12["#35;12"]:::external', text)
        self.assertIn("X_skeleton_T02 -.-> T01", text)
        self.assertIn("classDef external", text)

    def test_effort_text(self):
        self.assertEqual(tickets.effort_text(1), "about 1 hour")
        self.assertEqual(tickets.effort_text(5), "about 5 hours")
        self.assertEqual(tickets.effort_text(8), "about 1 dev-day")
        self.assertEqual(tickets.effort_text(28), "about 3.5 dev-days")

    def test_inline_code_ids(self):
        folder = self.epic()
        run("set-remote", next(folder.glob("01-*.md")), "--key", "#41", "--url", "https://x/41")
        t2 = next(folder.glob("02-*.md"))
        t2.write_text(t2.read_text(encoding="utf-8").replace(
            "Needed for login. Builds on T01.", "Uses `T01`, keeps `T01_FLAG` and `run T01 now`, then T01."),
            encoding="utf-8")
        out = run("body", t2)[1]
        self.assertIn("Uses #41, keeps `T01_FLAG` and `run T01 now`, then #41.", out)

    def test_links_and_due_in_body(self):
        folder = self.epic()
        t1 = next(folder.glob("01-*.md"))
        t1.write_text(t1.read_text(encoding="utf-8").replace(
            "labels:", 'due: 2026-12-01\nlinks: ["https://figma.com/file/a?b=1,2"]\nlabels:'), encoding="utf-8")
        out = run("body", t1)[1]
        self.assertIn("**Size:** S · **Due:** 2026-12-01", out)
        self.assertTrue(out.rstrip().endswith("## Links\n\n- https://figma.com/file/a?b=1,2"))

    def test_plan_flags_security_sensitive_tickets(self):
        folder = self.epic()
        t1 = next(folder.glob("01-*.md"))
        t1.write_text(t1.read_text(encoding="utf-8").replace("Needed for login.", "Fixes a SQL injection in login."),
                      encoding="utf-8")
        run("render", folder)
        items = {i["id"]: i for i in json.loads(run("plan", folder, "--json")[1])}
        self.assertEqual(items["T01"]["sensitive"], ["mentions 'sql injection'"])
        self.assertEqual(items["T02"]["sensitive"], [])
        self.assertIn("[security-sensitive]", run("plan", folder)[1])
        self.assertEqual(tickets.sensitive_reasons(tickets.read_doc(t1)), ["mentions 'sql injection'"])


if __name__ == "__main__":
    unittest.main()
