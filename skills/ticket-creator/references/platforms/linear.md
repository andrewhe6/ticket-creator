# Linear

The epic is a **Project** (`linear.epic_as: project`, the default) or a **parent issue**
(`parent_issue`). Tickets are issues in the configured team, linked to the project or set as
sub-issues of the parent. Dependencies use Linear's native **blocks / blocked by** relations. Priority
and estimate are native fields.

## Connect: MCP, then API, then markdown

1. **Linear MCP server**: the official remote server (`https://mcp.linear.app/mcp`) or the claude.ai
   Linear connector. Tool names have changed between versions, so read the schemas you actually have:

   | Need | Names you may see |
   |---|---|
   | Create / update an issue | `save_issue` (no id = create, id = update), or `create_issue` + `update_issue` |
   | Read / search issues | `get_issue`, `list_issues` (supports a `query` text filter) |
   | Teams | `list_teams`, `get_team` |
   | Projects | `save_project`, or `create_project` / `update_project`; `list_projects`, `get_project` |
   | Labels | `list_issue_labels`, `create_issue_label` |
   | States / cycles | `list_issue_statuses`, `list_cycles` |

   Typical issue fields: `title`, `team`, `description` (markdown), `priority` (number), `estimate`
   (number), `labels`, `parentId` / `parent`, `project`, `state`, `cycle`, `assignee`. Relation fields
   (`blockedBy`, `blocks`, `relatedTo`) exist on newer versions. If a field isn't in the schema, don't
   guess it.

2. **GraphQL API**, if the environment has `LINEAR_API_KEY`. Post to `https://api.linear.app/graphql`
   with the header `Authorization: <key>`. Write the JSON payload to a temp file and use
   `curl -s ... -d @payload.json`. Useful mutations: `issueCreate(input: {teamId, title, description,
   priority, estimate, labelIds, parentId, projectId, stateId, cycleId}) { issue { id identifier url } }`,
   `projectCreate(input: {name, teamIds, description, content})`, and
   `issueRelationCreate(input: {issueId: <blocker>, relatedIssueId: <blocked>, type: blocks})`.
3. **Neither available.** Tell the user (authorize the Linear connector, or `/mcp` in Claude Code),
   and keep the drafts.

## Preflight (once per push)

- **Team.** Resolve `linear.team` to its id with `list_teams`. Read the team's **estimate scale**
  (`issueEstimationType`: notUsed / exponential / fibonacci / linear / tShirt) unless
  `linear.estimate_scale` is set.
- **Estimate mapping** by scale (XS / S / M / L / XL):
  fibonacci 1/2/3/5/8 · exponential 1/2/4/8/16 · linear 1/2/3/4/5. For tShirt, Linear displays sizes
  but stores numbers. Check the schema description or one existing estimated issue for the mapping.
  If it's still unclear, ask once and save it in config `estimates`. For notUsed, send no estimate.
  The size stays in the body header.
- **Workflow state.** Find the id of `linear.state` (e.g. Backlog) in `list_issue_statuses` for the team.
- **Labels.** Team labels plus workspace labels. Diff against what the push needs (type labels, ticket
  labels, defaults). Create missing ones only per `create_missing_labels`.
- **Cycle.** For `current`/`next`, pick the matching cycle from `list_cycles`. For `none`, skip it.
- **Priority mapping**: urgent → 1, high → 2, medium → 3, low → 4, none → 0.

## Duplicate search

Use `list_issues` with `query: "<terms>"` (and the team filter), 2–4 times with different key terms.
Include completed issues from recent months as context. Also check `list_projects` for a project that
already covers the feature; that's a candidate for `existing: true`. GraphQL alternative:
`searchIssues(term: "<terms>", first: 20) { nodes { identifier title state { name } url } }`.

## Field mapping

| Draft | Linear |
|---|---|
| `title` | title |
| body (`tickets.py body <file>`) | description (markdown) |
| `type` | label from `type_labels` |
| `size` | `estimate` via the team's scale |
| `priority` | `priority` (number) |
| `labels` | labels (by name or id, per the schema) |
| epic | project (`project`) or parent issue (`parentId`) |
| `blocked_by` | `blockedBy` relations (the body header line shows them too) |
| `related` | `relatedTo` relations if supported, else the body header line |
| `assignee` | `assignee` (a user name, email, or id; resolve it with the users tool if the schema wants an id) |
| `due` | `dueDate` (`YYYY-MM-DD`) |
| `milestone` | the project milestone, if the epic is a project and the tool supports it; else the body |
| `links` | already in the body as a `## Links` list; Linear also shows them as attachments if you add them |

## Push

1. **Epic.**
   - *Project*: create it with name = epic title, the team, a one-line `description` (the Goal), and
     the full epic body as the project's long-form content if the tool supports it (`content`). If it
     only takes a short description, add the full body as a project document or update if a tool
     exists. Otherwise mention it in the hand-off. Record it with
     `set-remote 00-epic.md --key "<project name>" --url <project url> --id <project id> --platform linear`.
   - *Parent issue*: create an issue with the epic title and body, and record its identifier
     (`ENG-120`).
2. **Each ticket, in plan order**, skipping `pushed` and `changed` items. If an item is `uncertain` (a
   create started but no key was recorded), search `list_issues` for its exact title in the team first;
   if it's there, `set-remote` it instead of creating it. Otherwise run `tickets.py mark-pushing <file>`,
   then create it with team, title, description, priority, estimate, labels, state, cycle, assignee,
   due date, and `project` or `parentId`. Include `blockedBy` at create time if supported. The blockers
   already exist, because the plan is in topological order. Record it right away with
   `set-remote --key ENG-123 --url <url> --id <uuid> --platform linear`.
3. **Relations not set at create.** Add them with an update, or with `issueRelationCreate` through the
   API. Some versions **replace** the whole relation list on update, so always send a ticket's full
   `blockedBy` list, never just the new one.
4. **Epic body.** Run `tickets.py render`, then update the project content or the parent issue
   description with the new Plan block, and run `set-remote` on `00-epic.md` with its same key.

## Syncing drafts edited after the push

`tickets.py diff` lists drafts changed since their push (`plan` marks them `changed`). With the user's
OK, for each one: fetch the issue (`get_issue`), and compare its description with the new draft body. If
someone edited it on Linear in a way the draft would lose, show both and let the user choose (often a
comment is better). Otherwise update the title, description, priority, estimate, and labels (add the new
ones, swap the size or type label; leave labels added on Linear alone), and send the full `blockedBy`
list. Then run `set-remote` with the same key to record the sync. Never update an issue this skill
didn't create.

## Known problems

- **Identifiers vs. ids.** Humans and the body header use `ENG-123`. Some tools want the UUID `id` for
  `parentId` or relations. Keep both: `--key` holds the identifier and `--id` holds the UUID.
- **Sub-issues and projects.** A sub-issue created under a parent doesn't always inherit the parent's
  project. If the epic is a project, set `project` on every ticket explicitly.
- **Mermaid** may show as a code block rather than a diagram. The build-order list carries the same
  information.
- **Label scope.** A label created on the team isn't visible to other teams. Create it where the
  existing labels of the same kind live.
