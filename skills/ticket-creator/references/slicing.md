# Slicing guide

## What a good modular ticket is

Each ticket should be:

- **Independent.** It can be built and merged without waiting on anything except its listed
  `blocked_by` tickets.
- **Shippable.** Main stays green after it merges. Unfinished UX hides behind a flag or isn't wired
  into navigation yet.
- **Small.** One PR, about a day at most (size ≤ `max_size`, default M). Small tickets get reviewed
  faster, conflict less, and are much more reliable for AI agents to complete in one session.
- **Verifiable.** It has its own acceptance criteria and a concrete way to prove them.
- **Coherent.** It touches one logical area. If the files it changes are scattered across the
  codebase, it's probably two tickets.
- **Valuable or enabling.** Each ticket either moves a user-visible behavior forward or is an
  explicit enabler with a stated reason.

## Sizes

| Size | Effort | Typical content |
|---|---|---|
| XS | ≤ 1 hour | Config change, copy change, a flag, a one-function fix |
| S | ≤ half a day | One endpoint or one component with tests |
| M | ~ 1 day | A thin end-to-end slice across 2–3 layers with tests |
| L | 2–3 days | **Split it** (the validator warns when a size is over `max_size`), unless the user explicitly accepts it |
| XL | a week or more | That's an epic, not a ticket. The validator rejects it |

Estimate for one competent developer who is new to this code area. Include tests in the estimate.

**Check the size against the risk.** First drafts tend to be optimistic. Before settling on XS or S, look at
what the in-scope list and acceptance criteria actually involve. These take longer than they look:

- time zones, daylight saving, calendars
- concurrency: overlapping runs, locks, idempotency, exactly-once delivery
- data migrations with a backfill
- retries and partial failure
- schedulers, cron, background jobs
- cryptography: hashing, signing, encryption
- a new external service (email, payments, webhooks, a third-party API)

Two or more of these in an S ticket usually means M, or a split. Many acceptance criteria are another
signal: more than about 5 for XS, 8 for S, or 11 for M suggests the ticket does more than its size says.
`validate` warns about both. When it does, re-size or split, or keep the size and know why.

The epic's Plan block shows the size mix and a rough effort total (XS ≈ 1 hour, S ≈ half a day,
M ≈ 1 day). It's a sanity check for the reviewer, not a commitment. Teams that estimate in story points
map sizes to points in the config (`estimates`), which is what gets sent to the board.

## The three strategies

The same feature, "Users can upload a profile photo that appears next to their comments; moderators can
remove inappropriate photos", sliced three ways:

**Vertical slices + enablers** (default)
- T01 *enabler*: file storage adapter (local disk in dev, object storage in prod) plus a nullable
  `avatar_url` column (S). It's an enabler because upload, display, and moderation all need it.
- T02: user can upload and crop an avatar in profile settings (M), blocked by T01
- T03: comments show the author's avatar, with an initials fallback (S), blocked by T01
- T04: uploads are resized to 256 px and stripped of EXIF location data (S), blocked by T02
- T05: moderators can remove an avatar from the comment menu (S), blocked by T02, T03

**Pure vertical slices**: no standalone enablers.
- T01: user can upload an avatar (includes storage and the column) (M)
- T02: comments show avatars with a fallback (S), blocked by T01
- T03: uploads are resized and stripped of EXIF data (S), blocked by T01
- T04: moderators can remove an avatar (S), blocked by T02

**Horizontal layers**
- T01: schema and storage for avatars (S)
- T02: API endpoints to upload, fetch, and remove avatars (M), blocked by T01
- T03: image processing pipeline (S), blocked by T01
- T04: UI for the settings upload, comment avatars, and the moderation button (M), blocked by T02

Vertical slicing gives early working software and wide parallelism (in the first example, T02 and T03
run in parallel as soon as T01 lands). Horizontal slicing delays anything demoable until the end and
makes every ticket depend on the previous layer. Use it only when the interview gave a reason to.

**When the user just says "vertical slices"**, use vertical slicing with a high bar for enablers. Fold
groundwork into the first slice that needs it, and extract an enabler only when two or more slices need
it in parallel. Record which reading you used (`slicing:` in the epic, plus a line in Assumptions).

## Process

1. **List the outcomes.** Extract every requirement and behavior from the input as a flat list. This
   becomes your coverage checklist.
2. **Find the thinnest end-to-end path** (the tracer bullet): the smallest version of the core flow
   that works for real. That's usually the first slice.
3. **Add variations as their own slices**: additional fields, edge cases, error handling, secondary
   flows, settings, permissions.
4. **Extract enablers only when it pays off**, meaning the groundwork is needed by two or more slices,
   is risky enough to land and verify alone (data migrations, framework upgrades), or is inherently
   standalone (CI pipeline, environment setup). Under *pure vertical*, fold groundwork into the first
   slice that needs it.
5. **Size everything and split anything over `max_size`** using the patterns below.
6. **Wire dependencies.** Add `blocked_by` only for hard dependencies: the ticket literally needs code,
   schema, or an API from the other one. Soft preferences ("nicer to do after") go in the Context
   section. Fewer edges means wider waves and more parallel work.
7. **Check coverage.** Every outcome from step 1 maps to a ticket. Every ticket maps to an outcome or
   an enabler reason. Non-goals have no tickets.
8. **Check for collisions.** Tickets in the same wave that edit the same file will conflict when built
   in parallel. Either sequence them, or extract the shared change into an enabler, and mention the
   shared file in both tickets' Technical notes.
9. **Number the tickets.** Assign T-ids in dependency order (wave 1 first) *before* writing any ticket
   body, so the file order, the ids, and the cross-references all agree and never need renumbering.

## Splitting patterns

When a ticket is too big, try these in order:

- **Workflow steps.** Create → view → edit → delete. Or: submit → review → approve.
- **Happy path first.** The core path in one ticket. Validation, error states, and edge cases in the next.
- **Business-rule variations.** One rule per ticket (e.g. discount types, notification channels).
- **Data variations.** One input format or data source per ticket.
- **Interface variations.** API first, then UI. Or web before mobile. Each needs its own verification.
- **Defer performance.** Make it work, then make it fast (caching, pagination, batching) in a follow-up.
- **Spike.** When the size is unknown because the approach is unknown, create a time-boxed spike
  (XS–S). Its acceptance criteria are the questions it must answer and the decision it records (an ADR
  or a comment). Then draft follow-up tickets from the result.

## Anti-patterns

- **"Write tests for X" as its own ticket.** Tests belong to the ticket that changes the behavior.
  Exception: a deliberate coverage backfill for existing untested code.
- **"Frontend" and "Backend" mega-tickets** under a vertical strategy.
- **"Refactor X"** with no observable outcome. State what the refactor enables, and give it acceptance
  criteria a reviewer can check.
- **"Research Y"** with no time box and no defined output.
- **Hidden dependencies.** If T05 silently relies on T03's helper, write it in `blocked_by`.
- **Chains.** T01 → T02 → T03 → … → T09 with no parallelism. Look for slices that could start from an
  earlier point.
- **Placeholder tickets** ("misc fixes", "polish"). Name the specific fixes, or leave them out.

## Greenfield backlogs

A new project doesn't fit in one epic. Structure it as a few epics, each in its own folder, with
epic-level `blocked_by`:

1. **Walking skeleton** (first epic, always). Repo scaffold and tooling, CI that runs lint and tests,
   environment config, a deploy of a trivial build, and one thin real feature end-to-end through every
   layer. After this epic, every later ticket has a place to land and a way to ship.
2. **Feature epics**, one per core user flow from the MVP, each sliced as above.
3. **Hardening** (if needed): observability, error handling, backups, security review, docs.

Include only the enablers this project actually needs. A CLI tool doesn't need a deploy pipeline or a
database. A hosted web app probably needs both. Ask during the interview rather than adding a generic
checklist. Ask, too, whether hardening, docs, and release work belong in this backlog or can wait.

Put project-wide decisions (stack, hosting, conventions) in the first epic's Assumptions section, and
point to it from the other epics rather than repeating them. Epic-level `blocked_by` gives the coarse
order. When a ticket really waits on one specific ticket in another epic, add it to that ticket's
`blocked_by` as `other-epic/T03`. Waves are computed across the whole backlog, so precise links let
work start earlier than "after the whole skeleton epic".

## Sizing without the code

When the code lives somewhere you can't see (a PRD for another repo), sizes are educated guesses. Say so
once in the epic's Assumptions ("estimates are provisional until checked against the codebase"), and
suggest a re-grounding pass, with the repo available, before pushing.

## Codebase gap scans

When the input is "find what needs doing in this code":
- Gather findings with evidence: `TODO`/`FIXME`/`HACK` comments, modules with no tests, deprecated or
  vulnerable dependencies, swallowed errors, missing docs for public APIs.
- Group findings into epics by area or theme, not by finding type alone.
- Each ticket cites `file:line` evidence in its Context, and states the risk or benefit, which sets
  its priority.
- Merge trivial findings in the same file into one ticket. Don't file a ticket per TODO.
- **Security findings need care.** A ticket that describes an exploitable weakness (an injection, an auth
  bypass, an exposed secret) discloses it to everyone who can read the board. On a public repo, don't
  push it as an issue: suggest a private security advisory or a private tracker instead. `plan` flags
  such tickets, and `push` stops before sending them to a public repo.

## Large inputs

More than about 15 tickets in one epic is hard to review and track. Propose splitting into multiple
epics (by milestone, user flow, or MVP vs. follow-up), confirm the split with the user, then draft
each epic in its own folder.
