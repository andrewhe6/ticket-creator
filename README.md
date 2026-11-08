# Ticket Creator

A Claude skill, packaged as a Claude Code plugin. It turns a feature idea, a PRD, an existing codebase,
or a brand-new project into **modular tickets**, then puts them on your board: **GitHub Issues**,
**Linear**, or **Jira**.

Each ticket is a small, independently shippable unit (one PR, about a day at most). It has:

- **Context**: why it exists, with the requirement quoted so nobody needs the PRD
- **Scope**: what's in, and what's explicitly out (pointing to the ticket that owns it)
- **Acceptance criteria**: a testable checklist
- **Technical notes**: grounded in your real files and patterns
- **Verification**: the exact test commands and manual QA steps
- **Metadata**: type, T-shirt size, priority, labels, `blocked_by` dependencies, and optionally an
  assignee, milestone, due date, and links

Your team's own ticket template can replace those sections. Tickets are grouped under an **epic**. The
epic includes a generated ticket table with the size mix and rough effort, a build order in parallel
"waves", and a Mermaid dependency graph. The tickets are written to be picked up by people **or** AI coding
agents (e.g. `/implement`).

## Quick start

1. Install the plugin (see [Install](#install)). It needs Python 3.8+ and nothing else.
2. In your repo, ask: *"Break the CSV export feature into tickets"* (or point it at a PRD).
3. Answer the questions it can't settle on its own (often none; the first time, it also asks where your
   board lives).
4. Review the drafts in `tickets/<epic>/`, edit anything, then say **"push"**.

Later runs reuse `.claude/ticket-creator.yml`. To push drafts you made earlier, or to resume a push
that stopped partway, just ask: *"push tickets/csv-export"*. If you edit a ticket after pushing it, ask
to *"sync tickets/csv-export"*: it shows the changes and updates the board once you agree.

## How it works

```
config → gather context → interview → slice → draft → validate → you review → push → hand-off
```

1. **Config.** On first run, it detects your MCP servers, CLIs, and git remote, asks a few questions,
   and saves `.claude/ticket-creator.yml`.
2. **Context.** It reads your PRD or description, explores the codebase, reads your team's ticket
   conventions, and searches the board for duplicates.
3. **Interview.** It asks only the questions whose answers would change the tickets, and writes down
   everything else as assumptions for you to review. The slicing strategy is remembered once chosen.
4. **Draft.** It writes `tickets/<epic>/00-epic.md` plus one markdown file per ticket.
5. **Validate.** A bundled script (`scripts/tickets.py`, stdlib Python) checks:
   - structure: sections, sizes, references, and dependency cycles
   - content: credentials (blocks the push), personal data, placeholders, and cited file paths that
     don't exist in your repo
   - sizes that look low for the risk involved, and overlong tickets

   It also renders the epic's plan.
6. **Review.** You edit the files in your editor. Nothing is pushed until you say so.
7. **Push.** On GitHub, `tickets.py push` does the whole push in one run through the `gh` CLI. It
   first reconciles with the board, so an interrupted push never creates duplicates, and it stops to ask
   about missing labels, same-titled issues, or security tickets bound for a public repo. Then it creates
   the epic, tickets, sub-issues, dependencies, labels, and board placement. For Linear and Jira (and
   GitHub without `gh`) it uses MCP. Each ticket's key, URL, and a fingerprint of what was pushed are
   written back into its draft.
8. **Sync.** Drafts edited after the push are detected (`tickets.py diff`). With your OK they're synced
   to the board, but never over edits someone made on the board.

## Install

### Claude Code (plugin)

```
/plugin marketplace add andrewhe6/ticket-creator
/plugin install ticket-creator@ticket-creator
```

Claude uses the skill automatically when you ask for tickets, issues, or a backlog. You can also invoke
it directly as `/ticket-creator:ticket-creator`.

### Claude Code (plain skill)

Copy `skills/ticket-creator/` to `~/.claude/skills/ticket-creator/` (personal) or
`.claude/skills/ticket-creator/` (one project). It's then available as `/ticket-creator`.

### claude.ai

Build the archive, then upload `dist/ticket-creator.skill` in **Settings → Capabilities → Skills**:

```bash
python tools/package_skill.py
```

With code execution on, the skill runs `tickets.py` there just as in Claude Code. Without it, the drafts
live in the conversation. Pushing uses your Linear, Atlassian, or GitHub connectors.

## Connect a board

| Platform | Recommended | Fallback |
|---|---|---|
| GitHub Issues | `gh` CLI (`gh auth login`), used by `tickets.py push` | [GitHub MCP server](https://github.com/github/github-mcp-server) |
| Linear | [Linear MCP](https://linear.app/docs/mcp) (`https://mcp.linear.app/mcp`) or the claude.ai Linear connector | `LINEAR_API_KEY` + GraphQL |
| Jira | Atlassian Rovo MCP server, or the claude.ai Atlassian connector | `acli` (Atlassian CLI) |
| None | (none) | Markdown files in `tickets/` |

For a GitHub Projects board, the `gh` login also needs the `project` scope: `gh auth refresh -s project`.

## Example prompts

- "Break the due-date + reminder email feature into tickets for GitHub."
- "Turn `docs/prd-invites.md` into Linear tickets, vertical slices."
- "I'm starting a Go CLI that publishes an Obsidian vault as a static site. Give me a starting backlog for Jira."
- "Scan `src/billing` for TODOs and missing tests and ticket what's worth fixing."
- "File a bug: the export button times out for workspaces with more than 10k rows."
- "I changed T03's acceptance criteria. Sync it to GitHub."

## Configuration

`.claude/ticket-creator.yml` (safe to commit, holds no secrets):

```yaml
version: 1
platform: linear          # github | linear | jira | markdown
drafts_dir: tickets
template: .github/ISSUE_TEMPLATE/story.md   # optional: your team's ticket sections
conventions: docs/ticket-guidelines.md      # optional: your team's ticket rules
defaults: { priority: medium, max_size: M, labels: [], create_missing_labels: ask, slicing: vertical+enablers }
linear: { team: ENG, epic_as: project, state: Backlog, cycle: none, estimate_scale: auto }
```

Run `python skills/ticket-creator/scripts/tickets.py config` to check it. See
[skills/ticket-creator/references/config.md](skills/ticket-creator/references/config.md) for every key.

## Repository layout

```
.claude-plugin/              plugin.json + marketplace.json (this repo is its own marketplace)
skills/ticket-creator/
  SKILL.md                   the workflow (always loaded when the skill triggers)
  references/                loaded only at the step that needs it
    config.md  interview.md  slicing.md  ticket-format.md
    platforms/ github.md  linear.md  jira.md  markdown.md
  scripts/tickets.py         validate · render · plan · diff · body · mark-pushing · set-remote · push · config
  assets/                    epic/ticket/config templates
evals/                       test cases (skill-creator / `claude plugin eval` format) + triggers.json
tests/                       unit tests for tickets.py, including push against a fake gh (tests/fake_gh.py)
tools/package_skill.py       builds dist/ticket-creator.skill for claude.ai
```

## No board? Markdown works too

With `platform: markdown`, the `tickets/` folder is the backlog. Set `status: in-progress` or
`status: done` in a ticket and run `tickets.py render`. The epic's table then shows progress, and you
can push the same drafts to a board later.

## Development

```bash
python -m unittest discover -s tests                           # tickets.py suite (push runs against tests/fake_gh.py)
python skills/ticket-creator/scripts/tickets.py validate tickets/<epic>
python tools/package_skill.py                                  # dist/ticket-creator.skill
claude plugin validate .
claude plugin eval . --allow-tools Write,Edit,Bash --scaffold   # runs evals/ with and without the skill
```

`tests/fake_gh.py` stands in for the GitHub CLI: it simulates a repo in a JSON file and logs every call,
so the whole push (including interruptions, rate limits, and board edits) is tested without touching
GitHub. Point `github.cli` at it to try a push by hand.

On Windows, use `python` (or `py -3`) rather than `python3`. When a CLI needs a body file, use
`tickets.py body <file> --out <path>`; PowerShell's `>` writes UTF-16.

## License

MIT
