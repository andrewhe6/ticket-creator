# Jira (Cloud)

The epic is an issue of type **Epic**. Tickets are **Stories** (or Tasks, per `jira.ticket_type`) whose
`parent` is the epic. Dependencies are **Blocks** issue links. Size goes into the story points field.
Priority is the native priority.

## Connect: MCP, then CLI/API, then markdown

1. **Atlassian Rovo MCP server** (official; also the claude.ai Atlassian connector). Most calls need a
   `cloudId`. Get it first from `getAccessibleAtlassianResources`, picking the site that matches
   `jira.site`. Tool names have shifted between versions, so match by meaning:

   | Need | Names you may see |
   |---|---|
   | Site / cloud id | `getAccessibleAtlassianResources` |
   | Projects | `getVisibleJiraProjects`, `listJiraProjects` |
   | Issue types and fields | `getJiraProjectIssueTypesMetadata` / `listJiraProjectIssueTypesMetadata`, `getJiraIssueTypeMetaWithFields` |
   | Create / edit | `createJiraIssue`, `editJiraIssue` |
   | Search | `searchJiraIssuesUsingJql` |
   | Links | `createJiraIssueLink` (older: `createIssueLink`), `listJiraIssueLinkTypes` |
   | Boards and sprints | `listJiraBoards`, `listJiraBoardSprints`, `getJiraBoardSprintData` |
   | Read one issue | `getJiraIssue` |

2. **Atlassian CLI** (`acli jira workitem create ...`), if installed and logged in. Confirm its flags
   with `acli jira workitem create --help` before the first use. The REST API also works when
   `JIRA_EMAIL` and `JIRA_API_TOKEN` are set (`POST https://<site>/rest/api/3/issue`). REST v3 needs
   descriptions in Atlassian Document Format, so prefer MCP or the CLI.
3. **Neither available.** Tell the user (authorize the Atlassian connector, or `/mcp`), and keep the
   drafts.

## Preflight (once per push)

- **cloudId and project.** Confirm `jira.project_key` exists and is visible.
- **Issue types.** Confirm `jira.epic_type` and `jira.ticket_type` exist in the project. Map `bug`
  tickets to `Bug` if it exists. Map spike, enabler, and chore to `jira.ticket_type` (or `Task`) and
  add their type label.
- **Create-field metadata** for the ticket type (`getJiraIssueTypeMetaWithFields`):
  - Find the story points field: "Story point estimate" on team-managed projects, "Story Points" on
    company-managed ones. Save its id in config `jira.story_points_field`.
  - Read the allowed **priority** names. The usual mapping is urgent → Highest, high → High,
    medium → Medium, low → Low, none → leave unset. Use the names that actually exist.
  - Check for **required fields** without defaults (Components, Team, …). Ask the user for values
    once, and save them in config as `jira.field_defaults`.
  - Confirm the `parent` field is available. It's the modern way to put a story in an epic on Jira
    Cloud.
- **Link type.** Find `jira.link_type` (usually "Blocks", with inward "is blocked by" and outward
  "blocks") via `listJiraIssueLinkTypes`.
- **Sprint**, if `jira.sprint` is `active` or `next`. Find the project's board, then the active or
  first future sprint. Note the Sprint field id from the field metadata.

## Duplicate search

`searchJiraIssuesUsingJql` with queries like:
```
project = KEY AND statusCategory != Done AND text ~ "<term>" ORDER BY updated DESC
project = KEY AND issuetype = Epic AND statusCategory != Done ORDER BY updated DESC
project = KEY AND statusCategory = Done AND resolved >= -90d AND text ~ "<term>"
```
Use 2–4 key terms, one `text ~` clause each, or several queries.

## Field mapping

| Draft | Jira |
|---|---|
| `title` | summary |
| body (`tickets.py body <file>`) | description: send markdown if the tool says it accepts markdown (current Rovo MCP versions convert it). Otherwise follow the tool's stated format |
| `type` | issue type (Bug, or `ticket_type`) + label |
| `size` | story points field ← `estimates[size]` |
| `priority` | priority (by name) |
| `labels` | labels. **Jira labels can't contain spaces**, so use `size-M`-style hyphens if you add any |
| epic | `parent: {key: <epic key>}` |
| `blocked_by` | issue link of type Blocks |
| `related` | issue link of type "Relates", if it exists |
| `assignee` | `assignee: {accountId}`. Look the person up first (the user search tool, or the REST user search); Jira Cloud doesn't accept names |
| `due` | `duedate` (`YYYY-MM-DD`) |
| `milestone` | `fixVersions: [{name}]`, if that version exists in the project |
| `components` | `components: [{name}]` (only names that exist) |
| `links` | already in the body as a `## Links` list; add them as remote links too if the tool supports it |

## Push

1. **Epic.** `createJiraIssue` with the project, `epic_type`, summary, and description (the epic body),
   plus labels and priority. Some projects also require an "Epic Name" field; fill it with the title
   if the metadata lists it as required. Then
   `set-remote 00-epic.md --key PROJ-100 --url https://<site>/browse/PROJ-100 --id <id> --platform jira`.
2. **Each ticket, in plan order**, skipping `pushed` and `changed` items. If an item is `uncertain` (a
   create started but no key was recorded), search for its exact summary first
   (`project = KEY AND summary ~ "\"<title>\""`); if it's there, `set-remote` it instead of creating it.
   Otherwise run `tickets.py mark-pushing <file>`, then `createJiraIssue` with summary, description,
   issue type, priority, labels, story points, `parent` = epic key, the optional fields the draft has
   (assignee, due date, fix version, components), and any `field_defaults`. Record it right away with
   `set-remote --key PROJ-101 ... --platform jira`.
3. **Dependencies.** For each blocker, call `createJiraIssueLink` with type Blocks and
   **inwardIssue = the blocker** (must finish first), **outwardIssue = the blocked ticket**. After the
   first link, read the blocked ticket (`getJiraIssue`) and check that it shows "is blocked by
   <blocker>". If the direction is reversed, swap the arguments for the remaining links, and tell the
   user which link is backwards so they can flip it in Jira. Don't delete links yourself.
4. **Sprint.** If configured, set the Sprint field to the sprint id with `editJiraIssue`, or use a
   sprint tool if one exists.
5. **Epic body.** Run `tickets.py render`, then `editJiraIssue` the epic's description with the new
   Plan block, and run `set-remote` on `00-epic.md` with its same key.

## Syncing drafts edited after the push

`tickets.py diff` lists drafts changed since their push (`plan` marks them `changed`). With the user's
OK, for each one: read the issue (`getJiraIssue`) and compare its description with the new draft body. If
it was edited in Jira in a way the draft would lose, show both and let the user choose (often a comment
is better). Otherwise `editJiraIssue` the summary, description, priority, story points, and labels (add
new ones; leave labels added in Jira alone), and add or remove Blocks links for the dependencies that
changed (the `diff` output lists them). Then run `set-remote` with the same key to record the sync. Never
edit an issue this skill didn't create.

Security-sensitive tickets (`plan` flags them) don't belong in a Jira project that customers or the
public can see, such as a public service desk. Ask before pushing them there.

## Known problems

- **No Mermaid rendering.** The diagram shows as a code block. The ticket table and build-order list
  carry the same information.
- **Markdown checkboxes** may become plain bullets. That's acceptable; don't fight the conversion.
- **Team-managed vs. company-managed projects** use different field names and ids for the same
  concepts. Always read the field metadata. Never hardcode `customfield_*` ids from memory.
- **Permissions.** Creating links or setting sprints can need permissions the user lacks. Report what
  couldn't be set rather than failing the whole push.
