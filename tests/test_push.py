"""`tickets.py push` against a fake `gh` (tests/fake_gh.py) that simulates GitHub in a JSON file.
Run: python -m unittest discover -s tests"""
import json
import os
import re
import sys
import unittest
from pathlib import Path

from test_tickets import TICKET, DraftCase, run, tickets

FAKE = Path(__file__).resolve().with_name("fake_gh.py")
MUTATING = re.compile(r"^(POST|PATCH|DELETE|PUT)$")


def merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
        else:
            base[k] = v
    return base


class PushCase(DraftCase):
    TICKETS = [("T01", "Session model", "S", "[]"), ("T02", "Login endpoint", "M", "[T01]"),
               ("T03", "Logout", "S", "[T01, T02]")]

    def setUp(self):
        super().setUp()
        self.project = self.tmp / "proj"
        (self.project / ".git").mkdir(parents=True)
        (self.project / "src").mkdir()
        (self.project / "src" / "auth.ts").write_text("export {}\n", encoding="utf-8")
        (self.project / ".claude").mkdir()
        self.state_file = self.tmp / "gh-state.json"
        self._old_env = os.environ.get("FAKE_GH_STATE")
        os.environ["FAKE_GH_STATE"] = str(self.state_file)
        self.config()
        self.set_state()
        self.folder = self.epic(root=self.project / "tickets", tickets_=self.TICKETS)
        run("render", self.folder)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("FAKE_GH_STATE", None)
        else:
            os.environ["FAKE_GH_STATE"] = self._old_env
        super().tearDown()

    # ------------------------------------------------------------------ helpers
    def config(self, labels_policy="ask", github=""):
        (self.project / ".claude" / "ticket-creator.yml").write_text(
            f"platform: github\ndefaults:\n  create_missing_labels: {labels_policy}\n"
            + f"github:\n  repo: acme/app\n  cli: {FAKE.as_posix()}\n" + github, encoding="utf-8")

    def set_state(self, **over):
        state = {"repos": {"acme/app": {"visibility": "PRIVATE", "viewerPermission": "WRITE", "isArchived": False,
                                        "labels": ["bug"], "issues": [], "next_number": 1, "milestones": []}},
                 "orgs": {}, "projects": {}, "auth": {"project_scope": True}, "features": {"dependencies": True},
                 "fail": [], "calls": []}
        self.state_file.write_text(json.dumps(merge(state, over)), encoding="utf-8")

    def state(self):
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def edit_state(self, fn):
        state = self.state()
        fn(state)
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def issues(self):
        return self.state()["repos"]["acme/app"]["issues"]

    def issue(self, title):
        return next(i for i in self.issues() if i["title"] == title)

    def mutations(self):
        out = []
        for call in self.state()["calls"]:
            args = call["args"]
            if args[0] == "api" and "-X" in args and MUTATING.match(args[args.index("-X") + 1]):
                out.append(call)
            elif args[:2] in (["label", "create"], ["project", "item-add"], ["project", "item-edit"]):
                out.append(call)
        return out

    def push(self, *flags, path=None):
        code, out, err = run("push", path or self.folder, "--json", *flags)
        try:
            report = json.loads(out)
        except json.JSONDecodeError:
            self.fail(f"push printed no JSON (exit {code}): {out}\n{err}")
        return code, report

    def meta(self, path):
        meta, _, _ = tickets.parse_frontmatter(Path(path).read_text(encoding="utf-8"))
        return meta

    def file(self, tid):
        return next(self.folder.glob(f"{tid[1:]}-*.md"))

    def edit(self, path, old, new):
        path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    def items(self):
        return {i["id"]: i for i in json.loads(run("plan", self.folder, "--json", "--force")[1])}


class PushHappyPathTests(PushCase):
    def test_missing_labels_need_a_decision_and_nothing_is_written(self):
        before = {p: p.read_bytes() for p in self.folder.glob("*.md")}
        code, report = self.push()
        self.assertEqual(code, 3)
        decision = report["needs_decision"][0]
        self.assertEqual(decision["kind"], "missing-labels")
        self.assertEqual(set(decision["labels"]), {"epic", "feature", "size: S", "size: M", "priority: high", "backend"})
        self.assertEqual(self.issues(), [])
        self.assertEqual(self.mutations(), [])
        self.assertEqual(before, {p: p.read_bytes() for p in self.folder.glob("*.md")})

    def test_full_push_creates_links_and_records_everything(self):
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0, report)
        self.assertEqual([i["title"] for i in self.issues()],
                         ["User authentication", "Session model", "Login endpoint", "Logout"])
        epic, t1, t2, t3 = (self.issue(t) for t in ("User authentication", "Session model", "Login endpoint", "Logout"))
        self.assertIn("epic", epic["labels"])
        self.assertEqual(set(t2["labels"]), {"feature", "size: M", "priority: high", "backend"})
        self.assertEqual({t1["parent"], t2["parent"], t3["parent"]}, {epic["number"]})
        self.assertEqual(t2["blocked_by"], [t1["id"]])
        self.assertEqual(sorted(t3["blocked_by"]), sorted([t1["id"], t2["id"]]))
        self.assertTrue(t2["body"].startswith(f"**Epic:** #{epic['number']} · **Blocked by:** #{t1['number']}"))
        self.assertIn(f"[#{t2['number']}](https://github.com/acme/app/issues/{t2['number']})", epic["body"])
        self.assertEqual(set(report["labels_created"]), {"epic", "feature", "size: S", "size: M", "priority: high", "backend"})
        self.assertEqual(self.meta(self.file("T03"))["remote_blocked_by"], ["T01", "T02"])
        self.assertEqual({k: v["push"] for k, v in self.items().items()},
                         {"auth": "pushed", "T01": "pushed", "T02": "pushed", "T03": "pushed"})
        code, out, _ = run("validate", self.folder)
        self.assertEqual(code, 0, out)
        self.assertNotIn("WARNING", out)

    def test_rerun_is_a_no_op(self):
        self.push("--create-labels")
        before = len(self.mutations())
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.mutations()), before)
        self.assertEqual(report["created"], [])
        self.assertEqual(len(self.issues()), 4)

    def test_dry_run_changes_nothing(self):
        before = {p: p.read_bytes() for p in self.folder.glob("*.md")}
        code, report = self.push("--dry-run", "--create-labels")
        self.assertEqual(code, 0, report)
        self.assertEqual(self.issues(), [])
        self.assertEqual(self.mutations(), [])
        self.assertEqual(before, {p: p.read_bytes() for p in self.folder.glob("*.md")})
        self.assertTrue(any(a.startswith("create issue for T02") for a in report["actions"]))
        self.assertTrue(any("blocked by T01" in a for a in report["actions"]))
        self.assertTrue(any("create label 'size: M'" in a for a in report["actions"]))
        code, out, _ = run("push", self.folder, "--dry-run", "--create-labels")
        self.assertIn("dry run: nothing was changed", out)
        self.assertIn("would:", out)

    def test_never_policy_drops_missing_labels(self):
        self.config(labels_policy="never")
        code, report = self.push()
        self.assertEqual(code, 0, report)
        self.assertIn("size: M", report["labels_dropped"])
        self.assertTrue(all(i["labels"] == [] for i in self.issues()))
        self.assertNotIn("auto_created_labels", self.state()["repos"]["acme/app"])

    def test_issue_types_are_used_when_the_org_has_them(self):
        self.set_state(orgs={"acme": {"issue_types": ["Task", "Bug", "Feature", "Epic"]}})
        self.push("--create-labels")
        self.assertEqual(self.issue("User authentication")["type"], "Epic")
        self.assertEqual(self.issue("Session model")["type"], "Feature")

    def test_forward_references_are_filled_in(self):
        self.edit(self.file("T01"), "Needed for login. Builds on T01.", "Needed for login. T03 adds logout later.")
        run("render", self.folder)
        self.push("--create-labels")
        t3 = self.issue("Logout")
        self.assertIn(f"#{t3['number']} adds logout later.", self.issue("Session model")["body"])
        self.assertEqual(self.items()["T01"]["push"], "pushed")

    def test_text_report(self):
        code, out, _ = run("push", self.folder, "--create-labels")
        self.assertEqual(code, 0)
        self.assertIn("GitHub acme/app (private)", out)
        self.assertRegex(out, r"created\s+#2\s+T01\s+Session model")


class PushSafetyTests(PushCase):
    def test_public_repo_with_security_sensitive_ticket(self):
        self.set_state(repos={"acme/app": {"visibility": "PUBLIC"}})
        self.edit(self.file("T03"), "Needed for login.", "Fixes the XSS in the logout redirect.")
        run("render", self.folder)
        code, report = self.push("--create-labels")
        self.assertEqual(code, 3)
        decision = report["needs_decision"][0]
        self.assertEqual(decision["kind"], "public-repo-sensitive")
        self.assertEqual(decision["items"][0]["id"], "T03")
        self.assertEqual(self.issues(), [])
        code, report = self.push("--create-labels", "--allow-public")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.issues()), 4)

    def test_interrupted_create_is_adopted_not_duplicated(self):
        # The 3rd create (T02) reaches GitHub, but the connection drops before the reply.
        self.set_state(fail=[{"call": "POST repos/acme/app/issues", "after": 2, "status": 502, "partial": True}])
        code, report = self.push("--create-labels")
        self.assertEqual(code, 4)
        self.assertEqual(report["stopped"]["id"], "T02")
        self.assertEqual(len(self.issues()), 3)
        t2 = self.meta(self.file("T02"))
        self.assertIsNone(t2.get("remote_key"))
        self.assertTrue(t2.get("push_started"))
        self.assertEqual(self.items()["T02"]["push"], "uncertain")
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0, report)
        self.assertEqual([a["id"] for a in report["adopted"]], ["T02"])
        titles = [i["title"] for i in self.issues()]
        self.assertEqual(len(titles), len(set(titles)), "no duplicate issues")
        self.assertEqual(len(titles), 4)
        epic, t1, t2i = self.issue("User authentication"), self.issue("Session model"), self.issue("Login endpoint")
        self.assertEqual(t2i["parent"], epic["number"])
        self.assertEqual(t2i["blocked_by"], [t1["id"]])
        self.assertNotIn("push_started", self.file("T02").read_text(encoding="utf-8"))

    def test_rejected_create_clears_the_marker(self):
        self.set_state(fail=[{"call": "POST repos/acme/app/issues", "after": 1, "status": 422,
                              "message": "Validation Failed"}])
        code, report = self.push("--create-labels")
        self.assertEqual(code, 4)
        t1 = self.meta(self.file("T01"))
        self.assertIsNone(t1.get("push_started"))
        self.assertIsNone(t1.get("remote_key"))
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["adopted"], [])
        self.assertEqual(len(self.issues()), 4)

    def test_rate_limit_hint(self):
        self.set_state(fail=[{"call": "POST repos/acme/app/issues", "after": 1, "status": 403,
                              "message": "You have exceeded a secondary rate limit"}])
        code, report = self.push("--create-labels")
        self.assertEqual(code, 4)
        self.assertIn("wait a minute", report["error"])

    def test_same_title_on_the_board_needs_a_decision(self):
        self.edit_state(lambda s: s["repos"]["acme/app"].update(
            issues=[{"number": 1, "id": 1001, "title": "Login endpoint", "body": "teammate's", "state": "open",
                     "labels": [], "assignees": [], "blocked_by": [], "sub_issues": [], "parent": None}],
            next_number=2))
        code, report = self.push("--create-labels")
        self.assertEqual(code, 3)
        self.assertEqual(report["needs_decision"][0]["kind"], "possible-duplicate")
        self.assertEqual(len(self.issues()), 1)
        code, report = self.push("--create-labels", "--adopt")
        self.assertEqual(code, 0, report)
        self.assertEqual(self.meta(self.file("T02"))["remote_key"], "#1")
        self.assertEqual(len(self.issues()), 4)
        teammate = self.issue("Login endpoint")
        self.assertEqual(teammate["body"], "teammate's")  # adopted, never rewritten
        self.assertEqual(teammate["parent"], self.issue("User authentication")["number"])
        self.assertEqual(teammate["blocked_by"], [self.issue("Session model")["id"]])
        self.assertIs(self.meta(self.file("T02"))["remote_adopted"], True)
        # Later runs, and --update after a draft edit, leave its text alone too.
        self.edit(self.file("T02"), "it works", "it works fast")
        code, report = self.push("--update", "--create-labels")
        self.assertEqual(code, 0, report)
        self.assertIn("someone else wrote", report["conflicts"][0]["reason"])
        self.assertEqual(self.issue("Login endpoint")["body"], "teammate's")

    def test_validation_errors_block_the_push(self):
        self.edit(self.file("T01"), "Needed for login.", "Key AKIAQWERTYUIOPASDFGH.")
        code, report = self.push("--create-labels")
        self.assertEqual(code, 1)
        self.assertEqual(self.issues(), [])

    def test_access_problems(self):
        self.set_state(repos={"acme/app": {"viewerPermission": "READ"}})
        code, report = self.push("--create-labels")
        self.assertEqual(code, 4)
        self.assertIn("write access", report["error"])
        self.set_state(repos={"acme/app": {"isArchived": True}})
        self.assertEqual(self.push("--create-labels")[0], 4)
        code, report = self.push("--create-labels", "--repo", "acme/nope")
        self.assertEqual(code, 4)
        self.assertIn("can't read acme/nope", report["error"])

    def test_missing_gh(self):
        code, report = self.push("--gh", "definitely-not-a-real-gh-binary")
        self.assertEqual(code, 4)
        self.assertIn("install the GitHub CLI", report["error"])


class PushPlatformFeatureTests(PushCase):
    def test_dependencies_api_missing(self):
        self.set_state(features={"dependencies": False})
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0)
        self.assertTrue(any("dependencies API isn't available" in f for f in report["followups"]))
        self.assertEqual(self.meta(self.file("T03"))["remote_blocked_by"], [])
        self.assertEqual(self.issue("Logout")["blocked_by"], [])

    PROJECT = {"acme/7": {"id": "PVT_7", "items": [], "fields": [
        {"id": "F_status", "name": "Status", "options": [{"id": "O_todo", "name": "Todo"}, {"id": "O_done", "name": "Done"}]},
        {"id": "F_size", "name": "Size", "options": [{"id": "O_s", "name": "S"}, {"id": "O_m", "name": "M"}]}]}}
    BOARD = "  project:\n    owner: acme\n    number: 7\n    status: Todo\n"

    def test_project_board_placement(self):
        self.set_state(projects=self.PROJECT)
        self.config(github="  size_as: project_field\n" + self.BOARD)
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0, report)
        items = self.state()["projects"]["acme/7"]["items"]
        self.assertEqual(len(items), 4)
        self.assertTrue(all(i["values"].get("F_status") == "O_todo" for i in items))
        sized = [i["values"].get("F_size") for i in items if "F_size" in i["values"]]
        self.assertEqual(sorted(sized), ["O_m", "O_s", "O_s"])
        self.assertNotIn("size: M", self.issue("Login endpoint")["labels"])  # size lives on the board
        self.assertEqual(self.meta(self.file("T01"))["remote_board"], "acme/7")

    def test_project_scope_missing_then_fixed(self):
        self.set_state(projects=self.PROJECT, auth={"project_scope": False})
        self.config(github=self.BOARD)
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0)
        self.assertTrue(any("gh auth refresh -s project" in f for f in report["followups"]))
        self.assertIsNone(self.meta(self.file("T01")).get("remote_board"))
        self.edit_state(lambda s: s["auth"].update(project_scope=True))
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0, report)
        self.assertEqual(len(self.issues()), 4)
        self.assertEqual(len(self.state()["projects"]["acme/7"]["items"]), 4)

    def test_unknown_status_option(self):
        self.set_state(projects=self.PROJECT)
        self.config(github=self.BOARD.replace("Todo", "Doing"))
        code, report = self.push("--create-labels")
        self.assertEqual(code, 3)
        self.assertIn("Todo, Done", report["needs_decision"][0]["options"])

    def test_milestones_and_assignees(self):
        self.set_state(repos={"acme/app": {"milestones": [{"number": 4, "title": "v1"}], "collaborators": ["octocat"]}})
        self.edit(self.file("T01"), "labels:", "milestone: v1\nassignee: octocat\nlabels:")
        self.edit(self.file("T02"), "labels:", "assignee: ghost\nlabels:")
        run("render", self.folder)
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0, report)
        t1, t2 = self.issue("Session model"), self.issue("Login endpoint")
        self.assertEqual((t1["milestone"], t1["assignees"]), (4, ["octocat"]))
        self.assertEqual(t2["assignees"], [])
        self.assertTrue(any("couldn't assign ghost" in f for f in report["followups"]))

    def test_missing_milestone(self):
        self.edit(self.file("T01"), "labels:", "milestone: v9\nlabels:")
        run("render", self.folder)
        code, report = self.push("--create-labels")
        self.assertEqual(code, 3)
        self.assertEqual(report["needs_decision"][0]["kind"], "missing-milestones")

    def test_existing_epic_is_attached_to_not_edited(self):
        self.edit_state(lambda s: s["repos"]["acme/app"].update(
            issues=[{"number": 50, "id": 1050, "title": "Auth epic", "body": "theirs", "state": "open", "labels": [],
                     "assignees": [], "blocked_by": [], "sub_issues": [], "parent": None}], next_number=51))
        self.edit(self.folder / "00-epic.md", "slicing:", 'existing: true\nremote_key: "#50"\n'
                  'remote_url: https://github.com/acme/app/issues/50\nslicing:')
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0, report)
        epic = self.issue("Auth epic")
        self.assertEqual(epic["body"], "theirs")
        self.assertEqual(sorted(epic["sub_issues"]), [51, 52, 53])
        self.assertTrue(self.issue("Session model")["body"].startswith("**Epic:** #50"))

    def test_standalone_ticket_blocked_by_existing_issue(self):
        self.edit_state(lambda s: s["repos"]["acme/app"].update(
            issues=[{"number": 9, "id": 1009, "title": "Old bug", "body": "", "state": "open", "labels": [],
                     "assignees": [], "blocked_by": [], "sub_issues": [], "parent": None}], next_number=10))
        single = self.project / "tickets" / "_single"
        single.mkdir()
        (single / "export.md").write_text(TICKET.format(id="T01", title="Export works for 10k rows", size="S",
                                                        blocked_by='["#9"]'), encoding="utf-8")
        code, report = self.push("--create-labels", path=single)
        self.assertEqual(code, 0, report)
        new = self.issue("Export works for 10k rows")
        self.assertEqual(new["blocked_by"], [1009])
        self.assertIsNone(new["parent"])

    def test_multi_epic_backlog(self):
        root = self.project / "tickets"
        for p in list(root.iterdir()):
            for f in p.glob("*.md"):
                f.unlink()
            p.rmdir()
        self.epic("skeleton", root=root, tickets_=[("T01", "Scaffold", "S", "[]"), ("T02", "CI", "S", "[T01]")])
        self.epic("feature", root=root, blocked_by="[skeleton]", title="Feature epic",
                  tickets_=[("T01", "Uses CI", "S", "[skeleton/T02]")])
        run("render", root)
        code, report = self.push("--create-labels", path=root)
        self.assertEqual(code, 0, report)
        titles = [i["title"] for i in self.issues()]
        self.assertEqual(titles, ["User authentication", "Scaffold", "CI", "Feature epic", "Uses CI"])
        skeleton, feature = self.issue("User authentication"), self.issue("Feature epic")
        self.assertEqual(feature["blocked_by"], [skeleton["id"]])
        self.assertEqual(self.issue("Uses CI")["blocked_by"], [self.issue("CI")["id"]])
        self.assertEqual(self.issue("Uses CI")["parent"], feature["number"])


class PushUpdateTests(PushCase):
    def setUp(self):
        super().setUp()
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0, report)
        t2 = self.file("T02")
        text = t2.read_text(encoding="utf-8")
        text = text.replace("title: Login endpoint", "title: Login endpoint with rate limit")
        text = text.replace("size: M", "size: S").replace("- [ ] it works", "- [ ] it works\n- [ ] 429 after 5 tries")
        text = re.sub(r"^blocked_by: \[T01\]", "blocked_by: []", text, flags=re.M)
        t2.write_text(text, encoding="utf-8")
        run("render", self.folder)

    def test_changed_drafts_are_reported_not_synced(self):
        before = len(self.mutations())
        code, report = self.push()
        self.assertEqual(code, 0, report)
        self.assertEqual([c["id"] for c in report["changed_not_synced"]], ["T02"])
        self.assertEqual(self.issue("Login endpoint")["title"], "Login endpoint")
        # Only the epic's Plan block may be refreshed (its table shows the new title).
        new = self.mutations()[before:]
        self.assertTrue(all(c["args"][3].endswith("/issues/1") for c in new), new)

    def test_update_dry_run_shows_the_diff(self):
        before = len(self.mutations())
        code, report = self.push("--update", "--dry-run")
        self.assertEqual(code, 0, report)
        self.assertEqual(len(self.mutations()), before)
        changes = report["updated"][0]["changes"]
        self.assertIn("title", changes)
        self.assertIn("body", changes)
        self.assertTrue(any("+- [ ] 429 after 5 tries" in c for c in changes))
        self.assertTrue(any(c == "label -size: M" for c in changes))
        self.assertTrue(any(c == "blocked by -T01" for c in changes))

    def test_update_syncs_the_board(self):
        self.edit_state(lambda s: s["repos"]["acme/app"]["issues"][2]["labels"].append("customer"))
        code, report = self.push("--update")  # every label it needs already exists
        self.assertEqual(code, 0, report)
        t2 = self.issue("Login endpoint with rate limit")
        self.assertIn("429 after 5 tries", t2["body"])
        self.assertIn("size: S", t2["labels"])
        self.assertNotIn("size: M", t2["labels"])
        self.assertIn("customer", t2["labels"])  # added on the board: kept
        self.assertEqual(t2["blocked_by"], [])
        self.assertEqual(self.items()["T02"]["push"], "pushed")
        self.assertEqual(self.meta(self.file("T02"))["remote_blocked_by"], [])
        self.assertTrue(any("kept labels added on the board: customer" in c for c in report["updated"][0]["changes"]))

    def test_board_edits_are_never_overwritten(self):
        self.edit_state(lambda s: s["repos"]["acme/app"]["issues"][2].update(body="edited on GitHub"))
        code, report = self.push("--update", "--create-labels")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["conflicts"][0]["id"], "T02")
        self.assertIn("edited on the board", report["conflicts"][0]["reason"])
        self.assertEqual(self.issue("Login endpoint")["body"], "edited on GitHub")
        self.assertEqual(self.items()["T02"]["push"], "changed")

    def test_closed_issues_are_left_alone(self):
        self.edit_state(lambda s: s["repos"]["acme/app"]["issues"][2].update(state="closed"))
        code, report = self.push("--update", "--create-labels")
        self.assertEqual(report["conflicts"][0]["reason"], "closed on the board, so it wasn't changed")
        self.assertEqual(self.issue("Login endpoint")["title"], "Login endpoint")

    def test_epic_edited_on_the_board_is_not_overwritten(self):
        self.edit_state(lambda s: s["repos"]["acme/app"]["issues"][0].update(body="curated by the PM"))
        (self.folder / "04-audit.md").write_text(
            TICKET.format(id="T04", title="Audit log", size="S", blocked_by="[]"), encoding="utf-8")
        run("render", self.folder)
        code, report = self.push("--create-labels")
        self.assertEqual(code, 0, report)
        self.assertEqual(self.issue("Audit log")["parent"], 1)
        self.assertEqual(self.issue("User authentication")["body"], "curated by the PM")
        self.assertTrue(any(c["id"] == "auth" for c in report["conflicts"]))


class GhCommandTests(unittest.TestCase):
    def test_gh_command(self):
        self.assertEqual(tickets.gh_command(None), ["gh"])
        self.assertEqual(tickets.gh_command(str(FAKE)), [sys.executable, str(FAKE)])
        self.assertEqual(tickets.gh_command("fake_gh.py", FAKE.parent), [sys.executable, str(FAKE.parent / "fake_gh.py")])
        self.assertEqual(tickets.gh_command("gh --hostname example.com")[:2], ["gh", "--hostname"])

    def test_gh_key(self):
        self.assertEqual(tickets._gh_key("#57", "o/r"), ("o/r", 57))
        self.assertEqual(tickets._gh_key("x/y#3", "o/r"), ("x/y", 3))
        self.assertIsNone(tickets._gh_key("ENG-12", "o/r"))


if __name__ == "__main__":
    unittest.main()
