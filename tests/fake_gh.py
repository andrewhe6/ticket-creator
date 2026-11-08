#!/usr/bin/env python3
"""A stand-in for the GitHub CLI, for testing `tickets.py push` without touching GitHub.

It implements the subset of `gh` the push uses, against a simulated GitHub kept in a JSON file:
FAKE_GH_STATE (env), else fake_gh_state.json next to this script. Every call is appended to
state["calls"], so tests can assert exactly what was sent.

State shape (all keys optional except repos):
  repos:    {"o/r": {"visibility": "PRIVATE", "viewerPermission": "WRITE", "isArchived": false,
                     "labels": ["bug"], "milestones": [{"number": 1, "title": "v1"}],
                     "issues": [...], "next_number": 1}}
  orgs:     {"o": {"issue_types": ["Feature", "Bug", "Task"]}}
  projects: {"o/1": {"id": "PVT_1", "fields": [{"id": "F1", "name": "Status", "options": [{"id": "O1", "name": "Todo"}]}],
                     "items": []}}
  auth:     {"project_scope": true}
  features: {"dependencies": true}
  fail:     [{"call": "POST repos/o/r/issues", "after": 2, "status": 502, "times": 1}]
            -> the 3rd matching call (after 2 successes) fails with that status, `times` times.
            "partial": true makes the call succeed on the fake GitHub, then report a failure
            (as when a connection drops after the server acted).
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

STATE = Path(os.environ.get("FAKE_GH_STATE") or Path(__file__).with_name("fake_gh_state.json"))


def load():
    return json.loads(STATE.read_text(encoding="utf-8"))


def save(state):
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


class Fail(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def out(obj):
    sys.stdout.write(json.dumps(obj))


def issue_json(repo, issue):
    return {"number": issue["number"], "id": issue["id"], "node_id": f"I_{issue['id']}",
            "title": issue["title"], "body": issue.get("body", ""), "state": issue.get("state", "open"),
            "html_url": f"https://github.com/{repo}/issues/{issue['number']}",
            "labels": [{"name": n} for n in issue.get("labels", [])],
            "assignees": [{"login": a} for a in issue.get("assignees", [])],
            "milestone": issue.get("milestone"), "type": issue.get("type")}


def find_issue(r, number):
    for issue in r["issues"]:
        if issue["number"] == int(number):
            return issue
    raise Fail(404, "Not Found")


def by_id(state, issue_id):
    for name, r in state["repos"].items():
        for issue in r["issues"]:
            if issue["id"] == int(issue_id):
                return name, issue
    raise Fail(422, "Validation Failed: issue not found")


def ensure_labels(r, names):
    for n in names:
        if n.lower() not in {x.lower() for x in r["labels"]}:
            r["labels"].append(n)
            r.setdefault("auto_created_labels", []).append(n)


def check_fail(state, key):
    for rule in state.get("fail", []):
        if rule.get("call") != key or rule.get("times", 1) <= 0:
            continue
        seen = rule.setdefault("_seen", 0)
        if seen >= rule.get("after", 0):
            rule["times"] = rule.get("times", 1) - 1
            return rule
        rule["_seen"] = seen + 1
    return None


def api(state, args, stdin):
    method, path, i = "GET", None, 0
    payload = {}
    while i < len(args):
        a = args[i]
        if a == "-X":
            method = args[i + 1]
            i += 2
        elif a == "--input":
            payload = json.loads(stdin or "{}")
            i += 2
        elif a in ("-F", "-f"):
            k, v = args[i + 1].split("=", 1)
            payload[k] = int(v) if a == "-F" and v.isdigit() else v
            i += 2
        else:
            path = a
            i += 1
    parsed = urlparse(path)
    parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
    key = f"{method} {parsed.path}"
    rule = check_fail(state, key)
    if rule and not rule.get("partial"):
        raise Fail(rule.get("status", 500), rule.get("message", "Server Error"))

    result = route(state, method, parts, payload, parse_qs(parsed.query))
    if rule:  # the server acted, but the client saw an error
        raise Fail(rule.get("status", 502), rule.get("message", "Bad Gateway"))
    return result


def route(state, method, parts, payload, query):
    if parts[0] == "orgs" and parts[2:] == ["issue-types"]:
        org = state.get("orgs", {}).get(parts[1])
        if not org:
            raise Fail(404, "Not Found")
        return [{"id": i + 1, "name": n} for i, n in enumerate(org.get("issue_types", []))]
    if parts[0] != "repos":
        raise Fail(404, "Not Found")
    repo = f"{parts[1]}/{parts[2]}"
    r = state["repos"].get(repo)
    if r is None:
        raise Fail(404, "Not Found")
    rest = parts[3:]
    if rest == ["milestones"]:
        return r.get("milestones", [])
    if rest == ["issues"] and method == "POST":
        if not payload.get("title"):
            raise Fail(422, "Validation Failed: title missing")
        for a in payload.get("assignees", []):
            if a not in r.get("collaborators", [a]):
                raise Fail(422, f"Validation Failed: invalid assignee {a}")
        n = r.get("next_number", 1)
        r["next_number"] = n + 1
        issue = {"number": n, "id": 1000 + n + 100000 * (list(state["repos"]).index(repo)),
                 "title": payload["title"], "body": payload.get("body", ""), "state": "open",
                 "labels": list(payload.get("labels", [])), "assignees": list(payload.get("assignees", [])),
                 "milestone": payload.get("milestone"), "type": payload.get("type"), "parent": None,
                 "blocked_by": [], "sub_issues": []}
        ensure_labels(r, issue["labels"])
        r["issues"].append(issue)
        return issue_json(repo, issue)
    if len(rest) >= 2 and rest[0] == "issues":
        issue = find_issue(r, rest[1])
        sub = rest[2:]
        if not sub:
            if method == "GET":
                return issue_json(repo, issue)
            if method == "PATCH":
                for k in ("title", "body", "state", "milestone", "type"):
                    if k in payload:
                        issue[k] = payload[k]
                return issue_json(repo, issue)
        if sub == ["sub_issues"] and method == "POST":
            _, child = by_id(state, payload["sub_issue_id"])
            if child.get("parent"):
                raise Fail(422, "Validation Failed: sub-issue already has a parent")
            child["parent"] = issue["number"]
            issue.setdefault("sub_issues", []).append(child["number"])
            return issue_json(repo, child)
        if sub == ["sub_issues"] and method == "GET":
            return [issue_json(repo, find_issue(r, n)) for n in issue.get("sub_issues", [])]
        if sub[:2] == ["dependencies", "blocked_by"]:
            if not state.get("features", {}).get("dependencies", True):
                raise Fail(404, "Not Found")
            if method == "POST":
                blocker = int(payload["issue_id"])
                by_id(state, blocker)
                if blocker in issue["blocked_by"]:
                    raise Fail(422, "Validation Failed: dependency already exists")
                issue["blocked_by"].append(blocker)
                return issue_json(repo, issue)
            if method == "DELETE":
                blocker = int(sub[2])
                if blocker not in issue["blocked_by"]:
                    raise Fail(404, "Not Found")
                issue["blocked_by"].remove(blocker)
                return issue_json(repo, issue)
            if method == "GET":
                return [issue_json(*by_id(state, b)) for b in issue["blocked_by"]]
        if sub == ["labels"] and method == "POST":
            ensure_labels(r, payload["labels"])
            for n in payload["labels"]:
                if n not in issue["labels"]:
                    issue["labels"].append(n)
            return [{"name": n} for n in issue["labels"]]
        if len(sub) == 2 and sub[0] == "labels" and method == "DELETE":
            if sub[1] not in issue["labels"]:
                raise Fail(404, "Label does not exist")
            issue["labels"].remove(sub[1])
            return [{"name": n} for n in issue["labels"]]
        if sub == ["assignees"] and method == "POST":
            for a in payload["assignees"]:
                if a not in issue["assignees"]:
                    issue["assignees"].append(a)
            return issue_json(repo, issue)
    raise Fail(404, f"Not Found ({method} {'/'.join(parts)})")


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def project_cmd(state, args):
    if not state.get("auth", {}).get("project_scope", True):
        raise Fail(None, "error: your authentication token is missing required scopes [read:project]\n"
                         "To request it, run:  gh auth refresh -s read:project")
    sub = args[0]
    if sub in ("view", "field-list", "item-add"):
        key = f"{opt(args, '--owner')}/{args[1]}"
        project = state.get("projects", {}).get(key)
        if project is None:
            raise Fail(None, f"Could not resolve to a ProjectV2 with the number {args[1]}.")
        if sub == "view":
            return {"id": project["id"], "number": int(args[1]), "title": project.get("title", "Board")}
        if sub == "field-list":
            return {"fields": project["fields"], "totalCount": len(project["fields"])}
        item = {"id": f"PVTI_{len(project['items']) + 1}", "url": opt(args, "--url"), "values": {}}
        project["items"].append(item)
        return item
    if sub == "item-edit":
        for project in state.get("projects", {}).values():
            if project["id"] == opt(args, "--project-id"):
                for item in project["items"]:
                    if item["id"] == opt(args, "--id"):
                        item["values"][opt(args, "--field-id")] = opt(args, "--single-select-option-id")
                        return {"id": item["id"]}
        raise Fail(None, "item not found")
    raise Fail(None, f"unsupported: project {sub}")


def main(argv):
    state = load()
    stdin = sys.stdin.read() if "--input" in argv else None
    state.setdefault("calls", []).append({"args": argv, "stdin": json.loads(stdin) if stdin else None})
    try:
        cmd = argv[0]
        if cmd == "api":
            out(api(state, argv[1:], stdin))
        elif cmd == "auth" and argv[1:2] == ["status"]:
            sys.stdout.write("Logged in to github.com as fake\n")
        elif cmd == "repo" and argv[1] == "view":
            r = state["repos"].get(argv[2])
            if r is None:
                raise Fail(None, f"GraphQL: Could not resolve to a Repository with the name '{argv[2]}'.")
            out({"nameWithOwner": argv[2], "visibility": r.get("visibility", "PRIVATE"),
                 "viewerPermission": r.get("viewerPermission", "WRITE"), "isArchived": r.get("isArchived", False)})
        elif cmd == "label" and argv[1] == "list":
            out([{"name": n} for n in state["repos"][opt(argv, "-R")]["labels"]])
        elif cmd == "label" and argv[1] == "create":
            r = state["repos"][opt(argv, "-R")]
            if argv[2].lower() in {x.lower() for x in r["labels"]}:
                raise Fail(None, f"label with name \"{argv[2]}\" already exists; use `--force` to update its color")
            r["labels"].append(argv[2])
            r.setdefault("created_labels", []).append(argv[2])
        elif cmd == "issue" and argv[1] == "create":
            repo = opt(argv, "-R") or opt(argv, "--repo")
            body = opt(argv, "--body", "")
            if opt(argv, "--body-file"):
                body = Path(opt(argv, "--body-file")).read_text(encoding="utf-8")
            labels = [argv[i + 1] for i, a in enumerate(argv) if a in ("--label", "-l")]
            issue = api(state, ["-X", "POST", f"repos/{repo}/issues", "--input", "-"],
                        json.dumps({"title": opt(argv, "--title") or opt(argv, "-t"), "body": body,
                                    "labels": [x for l in labels for x in l.split(",")]}))
            sys.stdout.write(issue["html_url"] + "\n")
        elif cmd == "issue" and argv[1] == "view":
            repo = opt(argv, "-R") or opt(argv, "--repo")
            out(api(state, [f"repos/{repo}/issues/{argv[2].lstrip('#')}"], None))
        elif cmd == "issue" and argv[1] == "list":
            r = state["repos"][opt(argv, "-R")]
            limit = int(opt(argv, "--limit", "30"))
            issues = sorted(r["issues"], key=lambda i: -i["number"])[:limit]
            out([{"number": i["number"], "title": i["title"],
                  "url": f"https://github.com/{opt(argv, '-R')}/issues/{i['number']}"} for i in issues])
        elif cmd == "project":
            out(project_cmd(state, argv[1:]))
        else:
            raise Fail(None, f"fake gh: unsupported command {argv}")
    except Fail as e:
        save(state)
        status = f" (HTTP {e.status})" if e.status else ""
        sys.stderr.write(f"gh: {e.message}{status}\n")
        return 1
    save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
