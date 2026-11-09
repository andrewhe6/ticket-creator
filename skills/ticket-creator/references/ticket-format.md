# Draft format

The drafts serve three purposes. They are the review surface (the user edits them in their editor), the
input for `tickets.py` (validate, render, plan, push), and, after a push, the record of what was
created. Keep them in this format so the tooling works.

## Layout

```
<drafts_dir>/<epic-slug>/            # kebab-case, e.g. tickets/profile-avatars/
├── 00-epic.md                       # kind: epic
├── 01-avatar-storage.md             # id: T01
├── 02-upload-avatar.md              # id: T02
└── ...
```

- The file prefix is the ticket number (`01-` ↔ `T01`). Assign ids in dependency order (wave 1 first)
  before writing bodies, so files sort roughly by wave and references never need renumbering.
- Greenfield, with several epics: `<drafts_dir>/<project>/<epic-slug>/...`. Point `tickets.py` at the
  project folder to validate all epics together.
- Single-ticket mode: `<drafts_dir>/_single/<slug>.md`. Same frontmatter and sections, keep `id: T01`,
  and no epic.

## Frontmatter

Keep frontmatter **flat**: `key: value`, inline lists `[a, b]`, or `- item` lists. No nested mappings.
Use exactly the key names below. The validator rejects `depends_on`/`dependencies` and similar habits,
because an ignored dependency means a wrong build order. The trailing `# ...` comments in the templates
are hints; delete them or keep them. Put **double quotes** around:
- any value that starts with `#` (`"#12"`), which would otherwise be a comment
- any title containing ` #` (`"Fix export #42 crash"`), because everything after ` #` is a comment
- any title that starts with something other than a letter or digit (`"[UI] Fix button"`,
  `"![[embed]] support"`). Inside double quotes, escape a `"` as `\"`.

### Epic (`00-epic.md`)

| Field | Required | Notes |
|---|---|---|
| `kind` | yes | Always `epic` |
| `title` | yes | Feature or milestone name, e.g. "Profile avatars" |
| `priority` | no | What the user said, otherwise the highest priority among its tickets |
| `labels` | no | Extra labels for the epic itself |
| `blocked_by` | no | Other epic folder slugs (greenfield ordering) |
| `source` | yes | Where the requirements came from (doc path, "chat <date>", "codebase scan of src/x"). Not checked by `validate`, but later runs rely on it |
| `slicing` | yes | `vertical+enablers` / `vertical` / `horizontal`, whichever the interview chose. `validate` warns on any other value |
| `existing` | no | `true` = attach to an epic already on the board (fill `remote_*`); its body is never edited |
| `due`, `milestone`, `links` | no | Same as on tickets (below) |
| `remote_*` | no | Written by `tickets.py set-remote` / `push` |

### Ticket

| Field | Required | Notes |
|---|---|---|
| `id` | yes | `T01`, `T02`, … unique within the epic, never reused |
| `title` | yes | Outcome-focused, ≤ 90 characters (see below) |
| `type` | yes | `feature` / `enabler` / `bug` / `chore` / `spike` / `task` (non-code work: design, ops, content, process) |
| `size` | yes | `XS` / `S` / `M` (L warns, XL fails) |
| `priority` | yes | `urgent` / `high` / `medium` / `low` / `none` |
| `labels` | yes | Area labels such as `backend`, `auth`. Type and size labels are added at push time, so don't repeat them here |
| `blocked_by` | yes | `[]`, or ids in this epic (`T02`), another epic (`other-slug/T03`), or existing tickets (`"#12"`, `ENG-45`) |
| `related` | no | Overlapping tickets that don't block: ids in this set, or existing keys |
| `assignee` | no | Who it's for, only when the user said so: a GitHub login, a Linear user, a Jira account. A list is fine |
| `milestone` | no | GitHub milestone title, Jira fix version, or Linear project milestone. Must already exist |
| `due` | no | A date, `2026-12-01`. Shown in the body header and set where the platform has due dates |
| `links` | no | Full URLs the implementer needs: designs, a Slack thread, the PRD section. Added as a `## Links` list |
| `components` | no | Jira components |
| `status` | no | `todo` / `in-progress` / `done`. Only for markdown-only backlogs; once pushed, the board is the truth |
| `remote_*`, `push_started` | no | Written by `tickets.py` during a push (key, URL, id, a fingerprint of what was sent, the dependencies linked, board placement). Never invent or edit these |

**References in prose.** Write `T03` for a ticket in the same epic, and **always** `other-epic/T03` for
one in another epic, even in a sentence ("reuses the resolver from walking-skeleton/T04"). At push time
`tickets.py body` swaps each reference for its real key (`#57`, `ENG-123`). A bare `T03` always means
*this* epic's T03, so a bare id meant for another epic would point at the wrong ticket. Text inside
code fences is left alone, and so is inline code, except a span that is exactly an id (`` `T03` ``).

## Ticket body sections

All five are required, in this order, as `##` headings. A `task` ticket (non-code work) may leave out
Technical notes. Each section exists for a reason. Knowing the reason is what lets you write it well. You
may add an optional section after them, e.g. `## Assumptions` for a single ticket drafted without the
user's answers.

**A team template replaces these.** If the config sets `template:` to a team's own ticket template, its
`##` headings are the required sections instead (validate reads the same file). Mark a heading
`<!-- optional -->`, or `<!-- optional-for: task, spike -->` for some types only. Fill those sections in
the same spirit as below.

**`## Context`.** Why the ticket exists and what it unlocks, in 1–3 sentences. Quote or paraphrase the
specific requirement, so the ticket makes sense to someone who never read the PRD. Mention soft
ordering here ("Builds on the upload form from T02"). **For a bug**, Context also holds a short list:
steps to reproduce, actual behavior, expected behavior, and the cause if you found it.

**`## Scope`.** Two lists: **In scope** and **Out of scope**. The out-of-scope list is what stops an
implementer (especially an agent) from "helpfully" doing the neighbouring ticket's work. Name the
ticket that owns each excluded item: "Image resizing (T04)".

**`## Acceptance criteria`.** A checklist (`- [ ]`) of observable, testable outcomes. Write them from the
user's or caller's point of view. Given/When/Then works well for behavior. Cover the main path, the
important edge or error cases, and any non-functional requirement that applies (permissions,
performance limits, accessibility). For bugs, include a regression criterion ("valid requests still
succeed"). Avoid implementation instructions here; those go in Technical notes.

**`## Technical notes`.** Ground the ticket in the real code:
- Relevant code: verified paths and symbols (`src/users/service.ts: updateProfile()`), plus the
  existing pattern to copy ("follow `src/uploads/` for the storage adapter").
- Approach: a suggested design in a few bullets. It's a suggestion, not an order.
- Data: schema changes, migrations, backfills.
- Watch out for: gotchas, and files that same-wave tickets also edit.

Mark anything unverified: "(new file)", "likely in `src/jobs/`". `validate` checks the paths cited here
and in Verification against the repo and warns about any that don't exist, unless the line says it's new
or a guess, or another ticket creates it. When the code exists but you can't see it (a PRD for another
repo), start with one standard line, "Relevant code: not verified, drafted without the codebase", and
describe the approach by role ("the existing mailer"), not invented paths. For a greenfield project,
describe the intended structure decided in the interview.

**Point, don't prescribe.** The implementer reads the code when they start, which may be weeks later and
after other tickets landed. Name the file and pattern to follow and the constraint to respect ("one row
per user per day, enforced by a unique key"), not the code itself. Write literal code only for a
contract other tickets rely on: a schema, an API shape, an event name. Full SQL or function bodies go
stale, pre-empt the team's design discussion, and make the ticket long; `validate` warns when Technical
notes hold a lot of code.

**`## Verification`.** How a reviewer or agent proves the ticket is done:
- Automated: which tests to add (by behavior), and the exact commands from the repo
  (`npm test -- avatars`, `pytest tests/test_avatars.py`, `go test ./...`).
- Manual: short QA steps if there's UI or an integration ("upload a 5 MB PNG in Settings, reload, see
  the 256 px avatar beside your comments").

## Writing well

- **Titles** describe the outcome, not the activity: "User can upload an avatar in profile settings",
  not "Avatar work" or "Implement upload". Enablers name the capability they add: "File storage
  adapter with local and object-storage backends".
- **Be specific and concise.** Each ticket should take a few minutes to read: aim for 300–600 words,
  and a bug for under 300. `validate` warns above 800. Use bullets, not essays. Put background that
  several tickets share in the epic once, not in every ticket. Hard-wrapping lines is fine:
  `tickets.py body` unwraps paragraphs, because trackers like GitHub would otherwise show each newline
  as a line break.
- **No placeholders** in the final draft: no `TBD`, no `n/a` sections, no empty `- Approach:` lines, no
  leftover `<!-- guide: -->` comments or `<template values>`. An unknown is either an open question in
  the epic or a spike ticket. `validate` flags these.
- **No secrets or personal data.** `validate` treats credential formats (cloud keys, tokens, private
  keys, passwords in URLs) as errors that block pushing, and warns about email addresses and phone
  numbers. Use placeholders (`<password>`), env var names, roles, or `example.com` addresses.
- **Write for an agent as well as a human.** An agent will follow the out-of-scope list and the
  verification commands literally, so make them correct.

## Epic body sections

`## Goal`, `## Background`, `## Non-goals`, `## Assumptions & open questions`, `## Existing work`,
`## Definition of done`, `## Plan`. `validate` requires Goal and Non-goals (Non-goals may say "None");
fill in the rest as the templates describe. The Plan section holds the generated block between the
`ticket-creator:generated` markers. Leave the markers in place, never edit between them, and run
`tickets.py render` after any ticket change. `validate` warns when the block is out of date.

## Example ticket

```markdown
---
id: T02
title: User can upload and crop an avatar in profile settings
type: feature
size: M
priority: high
labels: [backend, frontend]
blocked_by: [T01]
related: []
remote_key: null
remote_url: null
remote_id: null
---

## Context

Users asked to put a face to their comments (PRD §3.1: "A user can upload a JPG or PNG up to 5 MB from
Settings → Profile and crop it to a square"). This is the first user-visible avatar slice. T04 (resize
and EXIF) and T05 (moderation) build on the stored image.

## Scope

**In scope**
- `POST /api/me/avatar` accepts JPG/PNG up to 5 MB, stores it through the T01 adapter, sets `avatar_url`
- Settings → Profile: file picker, square crop preview, save and remove actions

**Out of scope**
- Showing avatars next to comments (T03)
- Resizing and stripping EXIF data (T04): store the cropped original for now
- Animated GIFs

## Acceptance criteria

- [ ] Given I'm on Settings → Profile, when I pick a PNG, crop it, and save, then reloading shows my avatar
- [ ] A 6 MB file or a PDF is rejected with an inline error, and nothing is stored
- [ ] "Remove" clears `avatar_url`, and the profile shows the initials placeholder again
- [ ] `GET /api/me` includes `avatar_url` (`null` when unset)

## Technical notes

- Relevant code: `src/routes/me.ts` (profile routes), `src/services/userService.ts: updateProfile()`,
  `web/src/pages/settings/ProfileForm.tsx`
- Approach: multipart upload via the existing `upload` middleware in `src/middleware/upload.ts`;
  cropping happens client-side, so the server receives a square image (contract: `avatar_url` is a
  string or `null` on `GET /api/me`)
- Watch out for: T03 also edits `web/src/api/users.ts` (types). Keep the type change minimal, or land
  T02 first.

## Verification

- Automated: API tests for upload, oversized file, wrong type, and remove in `tests/me.test.ts`;
  run `npm test`
- Manual: `npm run dev`, upload, crop, save, reload, then remove
```
