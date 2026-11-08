---
id: T03
title: Todo API lets clients set, change, and clear a due date
type: feature
size: S
priority: medium
labels: [backend, api]
blocked_by: [T01]
related: []
remote_key: null
remote_url: null
remote_id: null
---

## Context

"Add due dates to todos" (chat request, 2026-11-06). After T01 the `due_date` column exists, but the
API can't write to it. This ticket makes due dates settable by any client, and the web UI (T05) builds
on it. The reminder job (T04) only reads the column, so it doesn't wait for this ticket.

## Scope

**In scope**
- `POST /api/todos` accepts an optional `due_date`, either `"YYYY-MM-DD"` or `null`.
- `PATCH /api/todos/:id` accepts `due_date`: a date sets or changes it, `null` clears it, and leaving the
  key out keeps the current value.
- Validation: the value must be a real calendar date in `YYYY-MM-DD` format. Anything else returns 400
  with the existing zod error shape.
- Every todo in a response includes `due_date` (`null` when unset).

**Out of scope**
- The web UI (T05)
- Sorting or filtering by due date, and overdue status (non-goals for this epic)
- Due times or timezones
- Rejecting past dates (past dates are allowed)
- Reminder emails (T04)

## Acceptance criteria

- [ ] `POST /api/todos` with `{ "title": "Pay rent", "due_date": "2026-12-01" }` returns 201, and the body
      has `"due_date": "2026-12-01"`
- [ ] `POST /api/todos` without `due_date` returns 201 with `"due_date": null`, so existing clients keep
      working
- [ ] `PATCH /api/todos/:id` with `{ "due_date": "2026-12-02" }` changes the date, and
      `{ "due_date": null }` clears it
- [ ] `PATCH /api/todos/:id` with `{ "done": true }` and no `due_date` key leaves the due date unchanged
- [ ] `"2026-02-30"`, `"2026-13-01"`, `"12/01/2026"`, `"2026-12-01T09:00:00Z"`, and `""` are each
      rejected with 400, and nothing is written
- [ ] Setting the due date on another user's todo returns 404, the same as any other `PATCH` on it today
- [ ] `GET /api/todos` returns `due_date` on every todo

## Technical notes

- Relevant code:
  - `src/schemas/todo.ts`: `createTodoSchema` and `updateTodoSchema`
  - `src/services/todoService.ts`: the `INSERT` in `create()`, and the `UPDATE ... SET title = ?, done = ?`
    in `update()`, which merges `{ ...current, ...input }`
  - `src/routes/todos.ts`: the POST and PATCH handlers already pass `parsed.data` through and return
    400 with `parsed.error.flatten()`, so they probably need no changes
- Approach:
  - Define one shared `isoDate` zod schema: `z.string().regex(/^\d{4}-\d{2}-\d{2}$/)` plus a
    `.refine()` that round-trips through `Date` (for example,
    `new Date(s + "T00:00:00Z").toISOString().slice(0, 10) === s`) to reject impossible dates.
  - Use `isoDate.nullable().optional()` in both schemas.
  - Add `due_date` to the `INSERT` in `create()` (`input.due_date ?? null`) and to the `SET` list in
    `update()`.
- Naming: the field is `due_date` (snake_case) in requests and responses. That matches `user_id` and
  `created_at`, which are returned as raw column names through `toTodo()`.
- Watch out for:
  - The `{ ...current, ...input }` merge only keeps the current date when zod leaves absent keys out
    of the output. `.optional()` does that. Don't add `.default(null)`, or every `PATCH` will clear the
    date.
  - Same wave: T04 (the reminder job) runs alongside this ticket. It's scoped to keep its query in
    `src/reminders/` and its tests in a new file, so it shouldn't touch `src/schemas/todo.ts`,
    `src/services/todoService.ts`, or `tests/todos.test.ts`. If it ends up needing to, coordinate.

## Verification

- Automated: add one test per acceptance criterion to `tests/todos.test.ts`. Reuse the existing
  `beforeEach`, which uses an in-memory DB with user 1. Include a second user to cover the 404 case.
  Run `npm test` and `npm run lint`.
- Manual:
  - Run `npm run dev`.
  - Create a todo with a date:
    `curl -X POST http://localhost:3000/api/todos -H "X-User-Id: 1" -H "Content-Type: application/json" -d "{\"title\":\"x\",\"due_date\":\"2026-12-01\"}"`
  - Send a `PATCH` with `"due_date": null` and check that the date clears.
  - If your local `todo.db` has no users, insert one first, as the tests do.
