# GitHub Issues

The epic is an issue labelled `epic` (or with an "Epic" issue type, if the org defines one). Tickets
are its **sub-issues**. Dependencies use GitHub's native **"blocked by"** relationship. Size and
priority become labels, or fields on a GitHub Projects (v2) board.

## Connect: `gh` first, then MCP, then markdown

1. **GitHub CLI, through `tickets.py push`**, if `gh auth status` shows a login. One command does the
   whole push deterministically (below). This is the preferred path: it's one tool call instead of
   dozens, it reconciles with the board before creating anything, and it records every key as it goes.
2. **GitHub MCP server** (the official `github/github-mcp-server`, local or remote), when there's no
   `gh`. You make the calls yourself, following "Push with MCP".
3. **Neither available.** Tell the user, keep the drafts as they are, and offer to push later
   (`gh auth login`, or install the GitHub MCP server).

## Duplicate search

MCP `search_issues` with queries like:
```
repo:OWNER/REPO is:issue is:open <2-4 key terms>
repo:OWNER/REPO is:issue is:closed closed:>2026-01-01 <terms>   # recently finished work
repo:OWNER/REPO is:issue label:epic is:open                      # epics to attach to
```
CLI: `gh issue list -R OWNER/REPO --search "<terms>" --state all --limit 20 --json number,title,state,url,labels`

A closed hit means the work may already exist. Mention it in "Existing work" rather than drafting it again.
Look at a few recent issues too: their titles and labels show the team's conventions.

## Push with `tickets.py push` (recommended)

```bash
python3 <skill-dir>/scripts/tickets.py push tickets/<epic> --dry-run   # what would happen
python3 <skill-dir>/scripts/tickets.py push tickets/<epic>             # do it
```

It reads `github.repo` and the other `github.*` settings from the config. In order, it:

1. **Validates** the drafts. Errors (including credentials in a draft) stop it with exit 1.
2. **Preflights**, read-only: repo access (write needed; archived repos refused), visibility, labels,
   milestones, org issue types, and the project board's fields.
3. **Reconciles** with the board: lists recent issues and matches pending drafts by exact title. A draft
   whose create was interrupted (`push_started`, no key) is linked to its issue instead of being created
   twice. Any other match stops the push so the user can decide.
4. **Creates** the epic, then the tickets in dependency order: body from `tickets.py body`, labels
   (`type_labels`, `size: M` / `priority: high` in label mode, area labels, `defaults.labels`), issue type,
   milestone, and assignees. It attaches each ticket as a sub-issue, adds its "blocked by" links, and
   places it on the project board, recording the key, a fingerprint, and the linked dependencies right
   after each create.
5. **Finishes**: refreshes ticket bodies that mention later tickets (so `T05` becomes `#61`), re-renders
   the epic, and updates its body with the Plan block that now links every ticket.

**Exit 3: a decision is needed.** Nothing has been created. Ask the user, then rerun with their choice:

| `needs_decision` kind | Ask | Rerun with |
|---|---|---|
| `missing-labels` | Create these labels? | `--create-labels` (or set `create_missing_labels: never` to skip them) |
| `possible-duplicate` | Is this the same work as the existing issue? | `--adopt` to link it (its text is never rewritten), or retitle the draft |
| `public-repo-sensitive` | These describe security problems and the repo is public. Report privately instead? | Remove or rework the tickets; `--allow-public` only if the user confirms they're safe to publish |
| `missing-milestones` | Create the milestone on GitHub, or drop `milestone:`? | (after fixing) |
| `missing-project-status` | Which board column? | (after fixing `github.project.status`) |

**Exit 4: stopped on an error.** Report what was created (`created`, `adopted`) and the error. Rerunning
continues where it stopped. Follow its hint for rate limits (wait a minute) and auth problems.

**Follow-ups** are things it couldn't do but didn't stop for: a dependency on a non-GitHub key, the
dependencies API missing (GitHub Enterprise Server), a missing `project` token scope (`gh auth refresh -s
project`, then rerun to place the tickets), an assignee who isn't a collaborator. List them in the
hand-off.

**Syncing edited drafts:** `push --update --dry-run` shows the diff against the board for every draft
edited since its push, and `push --update` applies it, after the user agrees. It updates title and body,
adds new labels, swaps the size/priority/type labels it manages, adds or removes the dependencies it
created, and assigns new assignees. It never overwrites an issue edited on the board since its push, a
closed issue, or one it only linked with `--adopt`; those come back as `conflicts`. Labels someone added
on the board are kept.

## Push with MCP (no `gh`)

Current versions of the official server group tools by method; older versions have one tool per action.
Match tools by what they do, and read each tool's schema before the first call.

| Need | Current name | Older name |
|---|---|---|
| Create / update an issue | `issue_write` (`method: create` / `update`) | `create_issue`, `update_issue` |
| Read an issue / sub-issues | `issue_read` (`method: get`, `get_sub_issues`) | `get_issue` |
| Search | `search_issues` | `search_issues` |
| Attach a sub-issue | `sub_issue_write` (`method: add`) | `add_sub_issue` |
| Dependencies | `issue_dependency_write` (if present) | none |
| Labels | `list_label`, `get_label`, `label_write` (`method: create`) | none |
| Issue types / fields | `list_issue_types`, `list_issue_fields` | none |
| Repo details | `get_repository` / `search_repositories` (visibility) | |
| Projects (v2) | `projects_list`, `projects_get`, `projects_write` (`add_project_item`, `update_project_item`) | `add_project_item`, … |

The server enables only some toolsets by default (issues, yes; labels and projects, often not). If a
label or project tool is missing, tell the user they can enable the toolset (`--toolsets` /
`GITHUB_TOOLSETS` for the local server, or the toolset settings of the remote server).

**Preflight.** Check the repo is reachable and whether it's public; if it is and `plan` flags a ticket as
`sensitive`, stop and ask (see the table above). Collect the labels the push needs, list the repo's
labels, and ask before creating missing ones (`create_missing_labels`). If `use_issue_types` is
`auto`/`true` and `list_issue_types` returns types, map feature→`Feature`, bug→`Bug`,
enabler/chore/spike/task→`Task`, epic→`Epic` (or `Feature` plus the epic label); use only names that
exist. Resolve the project's Status option if `github.project` is set.

**Push**, in `tickets.py plan` order, skipping `pushed` and `changed` items:

1. For an `uncertain` item, search for its exact title first (`search_issues` with
   `repo:OWNER/REPO in:title "<title>"`). If it exists, `set-remote` it and move on.
2. `tickets.py mark-pushing <file>`, then `issue_write` create with the title, body
   (`tickets.py body <file>`), labels, type, and `parent_issue_number: <epic number>` when supported.
   Keep `number` and the numeric `id` (not `node_id`) from the response, and run
   `tickets.py set-remote <file> --key "#<number>" --url <html_url> --id <id> --platform github`.
   If the parent wasn't set at create, call `sub_issue_write` (`method: add`, `issue_number: <epic
   number>`, `sub_issue_id: <numeric id>`).
3. Dependencies: `issue_dependency_write` per `blocked_by` entry if the server has it. Otherwise say in
   the hand-off that they're listed in each body's header line only.
4. Board: `projects_write` `add_project_item`, then `update_project_item` for Status (and Size/Priority in
   `project_field` mode).
5. Epic: `tickets.py render <folder>`, `issue_write` update its body with `tickets.py body 00-epic.md`,
   then `set-remote` the epic with its same key.

## By hand (single ticket, "I'll push it myself")

```bash
python3 <skill-dir>/scripts/tickets.py body tickets/_single/bug.md --out "<tmp>/body.md"
gh issue create -R OWNER/REPO --title "<title>" --body-file "<tmp>/body.md" --label bug --label "size: S"
python3 <skill-dir>/scripts/tickets.py set-remote tickets/_single/bug.md --key "#57" \
  --url https://github.com/OWNER/REPO/issues/57 --platform github
```

Use `--out` rather than `>`: PowerShell's `>` writes UTF-16, which `gh` can't read.

## Known problems

- **Projects need an extra token scope.** `gh project ...` fails with a scope error until the user runs
  `gh auth refresh -s project`. Ask them to run it; don't change their auth yourself. The push reports
  it as a follow-up and places the tickets on the next run.
- **Ids vs. numbers.** `sub_issue_id` and the dependency `issue_id` are the issue's numeric **id**, not
  its `#number`. Passing the number is the most common failure (404 or 422) in the MCP path.
- **Sub-issues must share an owner** with the parent (same org or user).
- **Secondary rate limits.** Create issues one at a time, not in parallel. On a 403/429 "secondary rate
  limit", wait about a minute, then push again; it resumes.
- **Labels with spaces or colons** (`size: M`) are fine, but quote them in shell commands.
- **The dependencies API needs a recent GitHub.** On GitHub Enterprise Server it may return 404; the push
  reports it once, and the body header lines still show each ticket's blockers.
