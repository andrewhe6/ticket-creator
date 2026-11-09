# Config and first-time setup

Project settings live in `.claude/ticket-creator.yml` at the project root. The file keeps the user from
answering "which board? which team? which labels?" on every run, and it can be committed so the whole
team drafts tickets the same way. It never holds secrets. Authentication belongs to the MCP server or
CLI login.

## First-time setup

Run this when the file doesn't exist, or when the user asks to change the target.

### 1. Detect what's available (no questions yet)

**MCP servers in this session.** Look at the tool names you have. They usually look like
`mcp__<server>__<tool>`.

| Platform | Signs of the official server | Typical tools |
|---|---|---|
| GitHub | server named `github`, or tools `issue_write` / `create_issue`, `search_issues`, `sub_issue_write` | see `platforms/github.md` |
| Linear | server named `linear`, or tools `save_issue` / `create_issue`, `list_teams`, `list_issue_labels` | see `platforms/linear.md` |
| Jira | server named `atlassian`, or tools `createJiraIssue`, `searchJiraIssuesUsingJql`, `getAccessibleAtlassianResources` | see `platforms/jira.md` |

Some clients load MCP tools lazily. If there's a tool-search tool, search for it ("linear issue",
"jira", "github issue") before you conclude a server is missing. If a server is listed as needing
authentication, tell the user how to authorize it: claude.ai connector settings for claude.ai
connectors, or `/mcp` in Claude Code. Don't ask the user for tokens.

**CLIs**, checked quietly in this order:
- `gh auth status`: the GitHub CLI and whether it's logged in
- `acli --version`: the Atlassian CLI, for Jira
- environment variable `LINEAR_API_KEY`: for Linear's GraphQL API through `curl`

**Repo signals** (first check that `git rev-parse --show-toplevel` is the project root; if the project
sits inside another repo, these signals describe the wrong project, so skip them):
- `git remote -v`: a github.com remote suggests GitHub Issues
- `git log --oneline -50`: keys like `ENG-123` in commits or branch names suggest Linear or Jira, and
  `#123` suggests GitHub
- `.github/ISSUE_TEMPLATE/`: GitHub is in use, and the templates show the team's expected sections and labels

### 2. Ask, in the same batch as the first interview round

Don't run a separate setup conversation. These questions go into the first interview round (SKILL.md
step 3), next to the slicing question, so the user answers everything at once. Use what you detected to
pre-select a recommendation. If the user only wants drafts for now, `platform: markdown` is fine, and
they can switch later and push the same drafts. Ask:

1. **Platform**: GitHub Issues / Linear / Jira / markdown files only. Recommend whichever has a working
   connection and matches the repo signals.
2. **Destination**: repo (`owner/name`), Linear team, or Jira project key. If the MCP server is
   connected, list the real options (`list_teams`, the Jira project list) so the user picks rather
   than types.
3. **Epic representation**, Linear only: a *Project* (recommended for features spanning more than a
   few days) or a *parent issue*. On GitHub, the epic is an issue with the `epic` label and sub-issues.
   On Jira, it's the Epic issue type.
4. **Board placement**: the GitHub Project (v2) number and status column, the Linear workflow state and
   cycle, or the Jira backlog vs. active sprint. "Just the backlog" is a fine answer.

Labels, priority, and estimate mapping can use defaults. Mention that the user can change them in the file.
If the team has its own ticket template or written ticket guidelines, ask where they are (or use
`.github/ISSUE_TEMPLATE/` if it exists) and record them as `template` and `conventions`.

### 3. Write the file

Copy `assets/config.template.yml`, fill in the answers, and **keep only the chosen platform's section**.
If the user named a board you can't reach yet (connector not authorized, CLI missing), still set that
platform and record what they told you (e.g. the Jira project key). Leave unknown fields as `null`;
the first push resolves them and writes them back. Use `platform: markdown` only when the user has no
board or wants files only.
Save it to `.claude/ticket-creator.yml`, then run `tickets.py config` to check it parses and has no
invalid values. Tell the user the path and that it's safe to commit. If you learn something during the
first push, such as a Jira story-points field id or a Linear estimate scale, write it back so the next
run doesn't have to discover it again. The same goes for answers that should stick, such as the slicing
strategy (`defaults.slicing`).

The file uses a plain subset of YAML that `tickets.py` reads without dependencies: nested `key: value`
mappings, `[a, b]` and `- item` lists, `{ a: b }` mappings, and `#` comments. Don't use anchors,
multi-line strings, or lists of mappings.

## Field reference

| Key | Default | Meaning |
|---|---|---|
| `version` | `1` | Config format version |
| `platform` | none | `github`, `linear`, `jira`, or `markdown` |
| `drafts_dir` | `tickets` | Where epic folders are written, relative to the project root |
| `template` | none | Path to the team's ticket template. Its `##` headings become the required sections (see `ticket-format.md`) |
| `conventions` | none | Path to the team's ticket guidelines (title style, definition of ready). Read before drafting |
| `defaults.priority` | `medium` | Used when the interview doesn't settle priority |
| `defaults.max_size` | `M` | Largest allowed ticket. `tickets.py validate` reads it from here |
| `defaults.labels` | `[]` | Added to every ticket |
| `defaults.create_missing_labels` | `ask` | `ask`, `always`, or `never`. `never` drops unknown labels and reports them |
| `defaults.slicing` | none | `vertical+enablers`, `vertical`, or `horizontal`. Set once the team has chosen, so it isn't asked again |
| `type_labels.<type>` | type name | Label applied for each ticket type (feature, enabler, bug, chore, spike, task) |
| `estimates.<size>` | 1/2/3/5/8 | Number sent where the platform stores estimates as numbers (story points) |
| `github.repo` | none | `owner/name` |
| `github.cli` | `gh` | Command that runs the GitHub CLI, if it isn't `gh` on the PATH |
| `github.epic_label` | `epic` | Label marking the epic issue |
| `github.use_issue_types` | `auto` | `auto`, `true`, or `false`. Use the org's issue types (Feature/Task/Bug) when it defines them |
| `github.size_as`, `github.priority_as` | `label` | `label` (`size: M`, `priority: high`) or `project_field`. `priority_as` also accepts `issue_field`, which only the MCP path sets; `tickets.py push` leaves priority unset in that mode |
| `github.project.*` | none | Projects (v2) board: owner, number, status field/value, size/priority field names |
| `linear.team` | none | Team key, e.g. `ENG` |
| `linear.epic_as` | `project` | `project` or `parent_issue` |
| `linear.state` | `Backlog` | Workflow state for new issues |
| `linear.cycle` | `none` | `none`, `current`, or `next` |
| `linear.estimate_scale` | `auto` | Read from the team, or set to `fibonacci`/`exponential`/`linear`/`tshirt` |
| `jira.site` | none | `yourcompany.atlassian.net` |
| `jira.project_key` | none | e.g. `PROJ` |
| `jira.epic_type` / `jira.ticket_type` | `Epic` / `Story` | Issue type names; some projects use `Task` |
| `jira.story_points_field` | discovered | Custom field id for story points |
| `jira.field_defaults` | none | Values for required custom fields, e.g. `{ components: [Web] }`, asked once |
| `jira.link_type` | `Blocks` | Issue link type for dependencies |
| `jira.sprint` | `backlog` | `backlog`, `active`, or `next` |

If the config disagrees with reality (the repo was renamed, a team key changed), tell the user what you
found, fix the file with their OK, and continue.
