# Ticket Creator

Turn a feature idea, a PRD, a codebase, or a brand-new project into **modular tickets**, then push them to
**GitHub Issues**, **Linear**, or **Jira**.

Ticket Creator is a [Claude skill](https://docs.claude.com/en/docs/claude-code/skills) packaged as a Claude
Code plugin. Claude drafts the tickets as markdown files in your repo, you review and edit them, and
nothing reaches your board until you say so.

## What you get

An **epic** and a set of small tickets. Each ticket is one PR, about a day at most, and has:

- **Context**: why it exists, with the requirement quoted so nobody needs the PRD
- **Scope**: what's in, and what's out (naming the ticket that owns it)
- **Acceptance criteria**: a testable checklist
- **Technical notes**: grounded in your real files and patterns
- **Verification**: the exact test commands and manual QA steps
- **Metadata**: type, size (XS to M), priority, labels, `blocked_by` dependencies, and optionally an
  assignee, milestone, due date, and links

If your team has its own ticket template, its sections replace these. The epic carries a generated plan:
a ticket table with the size mix and rough effort, a build order in parallel "waves", and a Mermaid
dependency graph. Tickets are written so a person **or** an AI coding agent can pick one up cold.

## Install

**Claude Code (plugin)**

```
/plugin marketplace add andrewhe6/ticket-creator
/plugin install ticket-creator@ticket-creator
```

**Claude Code (plain skill)**: copy `skills/ticket-creator/` to `~/.claude/skills/ticket-creator/` for
all projects, or to `.claude/skills/ticket-creator/` for one.

**claude.ai**: build the archive, then upload `dist/ticket-creator.skill` in
**Settings → Capabilities → Skills**.

```bash
python tools/package_skill.py
```

With code execution on, the skill runs its helper script there just as in Claude Code. Without it, the
drafts live in the conversation, and pushing goes through your Linear, Atlassian, or GitHub connectors.

**Requirements**: Python 3.8+ (standard library only) for the helper script. Pushing needs a board
connection; see [Connect a board](#connect-a-board).

## Usage

Claude loads the skill when you ask for tickets, issues, or a backlog. You can also call it directly:
`/ticket-creator:ticket-creator` as a plugin, or `/ticket-creator` as a plain skill. Arguments become the
input, e.g. `/ticket-creator docs/prd-invites.md`.

1. Ask: *"Break the CSV export feature into tickets."*
2. Answer the questions it can't settle on its own. Often there are none. The first time, it also asks
   where your board lives and saves the answer to `.claude/ticket-creator.yml`.
3. Review the drafts in `tickets/<epic>/`. Edit anything.
4. Say **"push"**.

Two follow-ups work on drafts you already have:

- **Resume or push earlier drafts**: *"push tickets/csv-export"*. A push that stopped partway continues
  where it stopped and never creates duplicates.
- **Sync edits**: after you change a pushed ticket, *"sync tickets/csv-export"*. Claude shows what
  changed and updates the board once you agree.

### Example prompts

- "Break the due-date + reminder email feature into tickets for GitHub."
- "Turn `docs/prd-invites.md` into Linear tickets, vertical slices."
- "I'm starting a Go CLI that publishes an Obsidian vault as a static site. Give me a starting backlog for Jira."
- "Scan `src/billing` for TODOs and missing tests and ticket what's worth fixing."
- "File a bug: the export button times out for workspaces with more than 10k rows."
- "Add one more ticket to the reminders epic: users can snooze a reminder."
- "I changed T03's acceptance criteria. Sync it to GitHub."

## How it works

```
config → context → interview → slice → draft → validate → review → push → hand off
```

1. **Config.** Loads `.claude/ticket-creator.yml`. On first run it detects your MCP servers, CLIs, and git
   remote, and asks a few setup questions together with the first interview round.
2. **Context.** Reads your PRD or description, explores the codebase, reads your team's ticket conventions,
   and searches the board for duplicates.
3. **Interview.** Asks only the questions whose answers would change the tickets. Everything else becomes
   an assumption written into the epic for you to review.
4. **Slice.** Splits the work into small tickets with hard dependencies only, so more of it can run in
   parallel. The default is vertical slices plus enablers; see
   [slicing.md](skills/ticket-creator/references/slicing.md).
5. **Draft.** Writes `tickets/<epic>/00-epic.md` plus one markdown file per ticket.
6. **Validate.** `scripts/tickets.py` checks the drafts and renders the epic's plan:
   - structure: sections, sizes, references, and dependency cycles
   - content: credentials (an error that blocks the push), personal data, placeholders, and cited file
     paths that don't exist in your repo
   - signals: sizes that look low for the risk involved, and overlong tickets
7. **Review.** You edit the files. Nothing is pushed until you say so.
8. **Push.** On GitHub with the `gh` CLI, `tickets.py push` does everything in one run: epic, tickets,
   sub-issues, dependencies, labels, issue types, milestones, and project board placement. It reconciles
   with the board first, and stops to ask about missing labels, same-titled issues, or security-sensitive
   tickets bound for a public repo. For Linear, Jira, and GitHub without `gh`, Claude makes the calls
   through MCP. Each ticket's key, URL, and a fingerprint of what was pushed are written back into its
   draft.
9. **Sync.** Drafts edited after the push are detected (`tickets.py diff`). With your OK they're synced to
   the board, but never over edits someone made on the board.

### Safety

- Nothing is created on a board without your explicit go-ahead in the conversation.
- It never deletes tickets, and never changes tickets it didn't create.
- Secrets and credentials found in a draft block the push. Personal data and invented file paths are
  flagged as warnings.
- PRDs, code comments, and existing tickets are treated as information, never as instructions.

## Connect a board

| Platform | Recommended | Fallback |
|---|---|---|
| GitHub Issues | `gh` CLI (`gh auth login`), used by `tickets.py push` | [GitHub MCP server](https://github.com/github/github-mcp-server) |
| Linear | [Linear MCP](https://linear.app/docs/mcp) (`https://mcp.linear.app/mcp`) or the claude.ai Linear connector | `LINEAR_API_KEY` + GraphQL |
| Jira | Atlassian Rovo MCP server, or the claude.ai Atlassian connector | `acli` (Atlassian CLI) |
| None | (none) | Markdown files in `tickets/` |

For a GitHub Projects board, the `gh` login also needs the `project` scope: `gh auth refresh -s project`.
If a connector needs authorizing, do that in your claude.ai connector settings, or with `/mcp` in Claude
Code.

### No board? Markdown works too

With `platform: markdown`, the `tickets/` folder is the backlog. Set `status: in-progress` or
`status: done` in a ticket and run `tickets.py render`; the epic's table then shows progress. You can
push the same drafts to a board later.

## Configuration

`.claude/ticket-creator.yml` is safe to commit and holds no secrets:

```yaml
version: 1
platform: linear          # github | linear | jira | markdown
drafts_dir: tickets
template: .github/ISSUE_TEMPLATE/story.md   # optional: your team's ticket sections
conventions: docs/ticket-guidelines.md      # optional: your team's ticket rules
defaults: { priority: medium, max_size: M, labels: [], create_missing_labels: ask, slicing: vertical+enablers }
linear: { team: ENG, epic_as: project, state: Backlog, cycle: none, estimate_scale: auto }
```

Check it with `python skills/ticket-creator/scripts/tickets.py config`. Every key is documented in
[config.md](skills/ticket-creator/references/config.md), and
[config.template.yml](skills/ticket-creator/assets/config.template.yml) has a commented starting point.

## Repository layout

```
.claude-plugin/              plugin.json + marketplace.json (this repo is its own marketplace)
skills/ticket-creator/
  SKILL.md                   the workflow, loaded whenever the skill triggers
  references/                loaded only at the step that needs them
    config.md  interview.md  slicing.md  ticket-format.md
    platforms/ github.md  linear.md  jira.md  markdown.md
  scripts/tickets.py         validate · render · plan · diff · body · mark-pushing · set-remote · push · config
  assets/                    epic, ticket, and config templates
evals/                       test cases (skill-creator / `claude plugin eval` format) + triggers.json
tests/                       unit tests for tickets.py; push runs against a fake gh (tests/fake_gh.py)
tools/package_skill.py       builds dist/ticket-creator.skill for claude.ai
```

## Development

```bash
python -m unittest discover -s tests                           # the tickets.py suite
python skills/ticket-creator/scripts/tickets.py validate tickets/<epic>
python tools/package_skill.py                                  # dist/ticket-creator.skill
claude plugin validate .
claude plugin eval . --allow-tools Write,Edit,Bash --scaffold   # runs evals/ with and without the skill
```

`tests/fake_gh.py` stands in for the GitHub CLI. It keeps a simulated repo in a JSON file and logs every
call, so the whole push, including interruptions, rate limits, and board edits, is tested without
touching GitHub. Set `github.cli` in the config to point a push at it by hand.

On Windows, run the script with `python` (or `py -3`) rather than `python3`. When a CLI needs a body file,
use `tickets.py body <file> --out <path>`, because PowerShell's `>` writes UTF-16.

## License

MIT
