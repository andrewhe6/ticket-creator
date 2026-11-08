---
id: T01
title: <Outcome-focused title, e.g. "Login form signs the user in and redirects to dashboard">
type: feature          # feature | enabler | bug | chore | spike | task (non-code work)
size: S                # XS (<=1h) | S (<=half day) | M (~1 day). L/XL must be split.
priority: medium       # urgent | high | medium | low | none
labels: []
blocked_by: []         # ticket ids in this epic (T02), other epics (other-slug/T03), or existing tickets ("#12", ENG-45)
related: []            # existing tickets found during the duplicate check, e.g. ["#31"]
# Optional, only when the user gave them: assignee, milestone, due (2026-12-01), links ["https://..."], components
remote_key: null       # filled in by `tickets.py set-remote` / `push`
remote_url: null
remote_id: null
---

## Context

<!-- guide: 1-3 sentences. Why this ticket exists and what it unlocks. Quote the requirement it
implements so a reader never needs the PRD or the chat to understand it. For a bug, add a short list:
steps to reproduce, actual, expected, and the cause if known. -->

## Scope

**In scope**
- <!-- guide: concrete behaviors/changes this ticket delivers -->

**Out of scope**
- <!-- guide: nearby work that belongs elsewhere, with the owning ticket: "Image resizing (T04)" -->

## Acceptance criteria

- [ ] <!-- guide: observable, testable outcomes. Given/When/Then is fine for behavior. -->

## Technical notes

<!-- guide: Ground this in the real codebase. Files/modules to touch (verified paths only; mark
new files as "(new file)"), the existing pattern to follow, data/schema changes, gotchas, and any
files that tickets in the same wave also touch (merge-conflict risk). Point to patterns rather than
writing the code; literal code only for contracts other tickets rely on. Delete lines you don't use.
A `task` ticket (non-code work) may drop this section. -->
- Relevant code:
- Approach:
- Watch out for:

## Verification

<!-- guide: How a reviewer or agent proves this is done: tests to add, exact commands to run
(from the repo's real scripts), and short manual QA steps if UI is involved. -->
- Automated:
- Manual:
