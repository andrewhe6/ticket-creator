---
id: T05
title: Users can set, see, change, and clear a due date in the web app
type: feature
size: S
priority: medium
labels: [frontend]
blocked_by: [T03]
related: []
remote_key: null
remote_url: null
remote_id: null
---

## Context

"Add due dates to todos" (chat request, 2026-11-06). People need to set due dates and see them in the
web app (`public/`), not just through the API. This ticket uses the `due_date` field that T03 adds to
`POST` and `PATCH /api/todos`.

## Scope

**In scope**
- An optional date input on the new-todo form (`#new-todo`)
- Each todo row shows its due date in a date input that can be changed or cleared in place
- Changing or clearing the date sends `PATCH /api/todos/:id` with `{ "due_date": ... }` (`null` when
  cleared) and then re-renders the list

**Out of scope**
- API changes or validation (T03)
- Overdue or due-today highlighting, and sorting or filtering by due date (non-goals for this epic)
- Creating `public/styles.css` or any restyling (see Technical notes)
- Reminder emails (T04)

## Acceptance criteria

- [ ] Given I enter a title and pick a date, when I click Add, then the new todo appears with that date
- [ ] Given I enter only a title, when I click Add, then the todo is created with an empty date, as it is
      today
- [ ] Given a todo with a due date, when I pick a different date in its row, then the change is saved and
      survives a page reload
- [ ] Given a todo with a due date, when I clear its date field, then the todo has no due date after a
      reload
- [ ] Checking or unchecking "done" and deleting still work, and neither changes the due date
- [ ] Each row's date input has an accessible name that includes the todo's title (for example,
      `aria-label="Due date for Buy milk"`), and the form's date input is labelled too
- [ ] Todo titles are still rendered as text, not HTML

## Technical notes

- Relevant code:
  - `public/index.html`: the `#new-todo` form has a single `title` input.
  - `public/app.js`, `render()`: builds each `<li>` from an `innerHTML` template, sets the title with
    `textContent`, and wires the checkbox's `onchange` and the delete button's `onclick`.
  - `public/app.js`, the submit handler: posts `{ title }`.
- Approach:
  - Add `<input type="date" name="due_date" aria-label="Due date">` to the form. In the submit handler,
    send `due_date: formData.get("due_date") || null`.
  - In `render()`, add `<input type="date" class="due">` to the row template. After creating the row,
    set `.value = t.due_date ?? ""` and the `aria-label` through DOM properties.
  - Wire `onchange` to `api(`/${t.id}`, { method: "PATCH", body: JSON.stringify({ due_date:
    e.target.value || null }) }).then(render)`.
  - A native `<input type="date">` always gives back `YYYY-MM-DD`, which matches the API format, so no
    date parsing is needed.
- Watch out for:
  - The done checkbox is currently selected with `li.querySelector("input")`. Once a row has a second
    `<input>`, make that selector specific (for example, `input[type=checkbox]`), or the done toggle
    may bind to the wrong element.
  - Don't interpolate `t.due_date` or `t.title` into the `innerHTML` string. Set values through
    properties, as the title already is.
  - `public/index.html` links `styles.css`, which doesn't exist in `public/`. That's pre-existing, so
    leave it alone here.
  - The repo has no frontend tests, and `npm run lint` only covers `src` and `tests`, so this ticket is
    verified by hand.
- Same wave: T06 (production scheduling) doesn't touch `public/`.

## Verification

- Automated: no UI test setup exists. Run `npm test` to confirm the API still passes.
- Manual:
  - Run `npm run dev`, open http://localhost:3000, and go through each acceptance criterion, reloading
    between steps.
  - In DevTools > Network, check that a date change sends a `PATCH` with only `due_date` in the body.
  - Using only the keyboard, tab to a row's date input, change it, and confirm it saves.
