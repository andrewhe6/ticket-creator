---
id: T01
title: Todos table stores an optional due date
type: enabler
size: XS
priority: high
labels: [backend, database]
blocked_by: []
related: []
remote_key: "#2"
remote_url: "https://github.com/acme-example-org/todo-app/issues/2"
remote_id: "1002"
remote_platform: "github"
remote_hash: "3b3ef696aa9b"
remote_sent: "9bc3a7c97684"
remote_blocked_by: []
remote_pushed_at: "2026-11-07T20:27:04Z"
---

## Context

We're adding due dates to todos so people can get a morning email for anything due that day (chat
request, 2026-11-06). This ticket only adds the storage. It's a separate enabler because three tickets
need the column: the API (T03), the reminder job (T04), and through the API, the web UI (T05).

## Scope

**In scope**
- A new migration, `src/db/migrations/002_add_todo_due_date.sql`, that adds a nullable `due_date TEXT`
  column to `todos`. The format is `YYYY-MM-DD`, and `NULL` means no due date.
- A partial index on `due_date` for open todos, which supports the reminder query in T04
- `due_date: string | null` added to the `Todo` interface in `src/services/todoService.ts`

**Out of scope**
- Accepting or validating `due_date` in `POST`/`PATCH` (T03)
- Any UI (T05)
- The reminder query and the `reminder_sends` table (T04)
- Due times or timezones (dates only)

## Acceptance criteria

- [ ] Given a fresh database, when migrations run, then `todos` has a nullable `due_date` column and
      `schema_migrations` contains `002_add_todo_due_date.sql`
- [ ] Given a database that only has `001_create_users_todos.sql` applied and already holds todos, when
      `migrate()` runs, then the migration applies once, existing todos keep all their data, and their
      `due_date` is `NULL`
- [ ] Running `migrate()` a second time is a no-op
- [ ] `GET /api/todos` includes `due_date: null` on every existing todo, and no other response field
      changes

## Technical notes

- Relevant code: `src/db/index.ts: migrate()` runs every `*.sql` file in `src/db/migrations/` in
  sorted filename order and records each one in `schema_migrations`. Follow the style of
  `001_create_users_todos.sql`.
- Approach:
  - `ALTER TABLE todos ADD COLUMN due_date TEXT;`
  - `CREATE INDEX todos_due_date_open ON todos(due_date) WHERE done = 0;`
  - A nullable column needs no backfill.
  - `toTodo()` spreads the DB row, so once the interface is updated, the column shows up in API
    responses without any route changes.
- Watch out for:
  - `migrate()` runs each file with `db.exec` and no transaction. Keep the file to these two
    statements so a partial failure is easy to reason about.
  - Numbering: T04 adds `003_create_reminder_sends.sql`, so keep this file as `002_`.
  - The `INSERT`/`UPDATE` statements in `todoService` list their columns explicitly, so they're
    unaffected. Writing `due_date` is T03's job.
- Same wave: T02 (email service) doesn't touch any of these files.

## Verification

- Automated: add tests to `tests/todos.test.ts`, or a new `tests/migrations.test.ts`:
  - On a fresh `:memory:` DB, after `migrate(db)`, `PRAGMA table_info(todos)` lists `due_date`.
  - Upgrade path: on a `:memory:` DB, exec only `001_create_users_todos.sql`, record it in
    `schema_migrations`, and insert a user and a todo. Then call `migrate(db)` and assert the todo has
    `due_date === null`. Call `migrate(db)` again and expect no error.
  - Run `npm test`.
- Manual: against an existing local `todo.db`, run `npm run db:migrate` and then `npm run dev`.
  `curl -H "X-User-Id: 1" http://localhost:3000/api/todos` should show `"due_date": null` on each todo.
