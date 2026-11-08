---
id: T04
title: Users get one morning email listing their open todos due today
type: feature
size: M
priority: medium
labels: [backend, email]
blocked_by: [T01, T02]
related: []
remote_key: null
remote_url: null
remote_id: null
---

## Context

"A reminder email in the morning for anything that's due that day" (chat request, 2026-11-06). This
ticket builds the job: it finds each user's open todos due on a given date and sends that user one
digest email. It's safe to re-run. T06 schedules it in production, so until then nothing is sent
automatically.

## Scope

**In scope**
- A function, `sendDueReminders({ db, mailer, date, dryRun })`
- Selection: todos with `due_date = date` and `done = 0`, grouped by user, sent to `users.email`
- One plain-text email per user per date (a subject plus the list of titles). Users with nothing due get
  no email.
- Idempotency: a new `reminder_sends` table (migration `003`) with one row per (user, date), written
  after a successful send. Users who already have a row for that date are skipped.
- Partial failure: a failed send for one user is logged and counted, doesn't stop the other users, and
  isn't recorded, so a re-run retries that user.
- A CLI entry point and `npm run job:due-reminders`, with `--date YYYY-MM-DD` and `--dry-run` options
- "Today" is computed in `REMINDER_TIMEZONE` (an IANA name), which defaults to the host's timezone.
- README: how to run the job, and its env vars (`REMINDER_TIMEZONE`, `APP_BASE_URL`, plus the mail vars
  from T02)

**Out of scope**
- Running the job on a schedule, and production config (T06)
- The mail transport itself (T02)
- Overdue todos (due before the run date), which aren't included
- Per-user timezones, opt-out or preferences, and HTML email
- Setting due dates through the API (T03). Tests insert rows directly.

## Acceptance criteria

- [ ] Given user A has two open todos and one done todo due 2026-11-09, when the job runs with
      `--date 2026-11-09`, then A receives exactly one email listing only the two open titles
- [ ] The subject is "You have 1 todo due today" or "You have N todos due today". The body greets the
      user by `display_name`, lists each title on its own line, and links to the app when
      `APP_BASE_URL` is set.
- [ ] A's email never includes todos due on other dates, todos with no due date, or other users' todos
- [ ] A user with no open todos due on that date gets no email
- [ ] Running the job twice for the same date sends each user at most one email
- [ ] If the send to user A fails, user B still gets their email and the job exits non-zero. Re-running
      for the same date then emails A only.
- [ ] `--dry-run` prints who would get which todos, and sends and records nothing
- [ ] Without `--date`, the date is "today" in `REMINDER_TIMEZONE`. For example, at 2026-11-06T23:30Z
      it's `2026-11-06` for `America/Los_Angeles` and `2026-11-07` for `Asia/Tokyo`.
- [ ] An invalid timezone name exits with a clear error before anything is sent
- [ ] The job ends by printing a one-line summary: the date, how many were sent, how many were skipped
      as already sent, and how many failed

## Technical notes

- Relevant code:
  - `src/db/index.ts: openDb()` opens the DB, enables WAL, and runs migrations. The job should call
    `openDb(process.env.DB_PATH ?? "todo.db")`, the same way `src/server.ts` and `src/db/migrate.ts` do.
  - The `users(id, email, display_name)` and `todos` tables are in
    `src/db/migrations/001_create_users_todos.sql`, and `due_date` comes from T01.
  - `Mailer`, `createMailerFromEnv()`, and `createMemoryMailer()` are in `src/email/mailer.ts` (T02).
- Approach:
  - New migration `src/db/migrations/003_create_reminder_sends.sql`:
    `reminder_sends(user_id INTEGER NOT NULL REFERENCES users(id), reminder_date TEXT NOT NULL,
    todo_count INTEGER NOT NULL, sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, reminder_date))`
  - New file `src/reminders/dueReminders.ts`:
    - One query that joins `todos` and `users` with `due_date = ? AND done = 0`, excluding users who
      already have a `reminder_sends` row for that date.
    - Group the rows by user in JS, send, and insert the `reminder_sends` row after each successful
      send.
  - `todayIn(timeZone, now = new Date())`: `Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric",
    month: "2-digit", day: "2-digit" }).format(now)` returns `YYYY-MM-DD`, so no date library is needed.
    An invalid zone throws a `RangeError`, which the CLI turns into a clear message.
  - New file `src/jobs/sendDueReminders.ts`:
    - Parse `--date` and `--dry-run` from `process.argv`.
    - Build the mailer with `createMailerFromEnv()`, call `sendDueReminders()`, and print the summary.
    - Set `process.exitCode = 1` if anything failed, and close the DB.
  - In `package.json`, add `"job:due-reminders": "tsx src/jobs/sendDueReminders.ts"`, next to the
    existing `db:migrate` script.
- Semantics: because the row is recorded after the send, delivery is at-least-once. If the process dies
  between the send and the insert, that user can get a duplicate on re-run. That's acceptable for
  reminders, so don't over-engineer it.
- Watch out for:
  - Same wave: T03 edits `src/schemas/todo.ts`, `src/services/todoService.ts`, and
    `tests/todos.test.ts`. Keep this ticket's query in `src/reminders/` and its tests in a new file, so
    the two don't conflict.
  - Migration numbering: T01 owns `002_`, so this file must be `003_`.
  - The server holds the same SQLite file open. `openDb()` turns on WAL mode, so a separate job process
    can read and write alongside it.
  - Titles are user input. The email is plain text, so there's no HTML escaping to worry about. Keep it
    plain text.

## Verification

- Automated:
  - New `tests/dueReminders.test.ts`. Copy the setup from `tests/todos.test.ts`
    (`new Database(":memory:")`, `migrate(db)`, insert users) and use `createMemoryMailer()`.
  - Write one test per acceptance criterion, plus `todayIn` tests with fixed instants.
  - For the partial-failure case, use a mailer that throws for one recipient.
  - Run `npm test` and `npm run lint`.
- Manual:
  - Run `npm run dev` and give one of your todos today's date (with T03's API if it's merged, otherwise
    directly in SQLite).
  - Run `npm run job:due-reminders -- --dry-run` and check that the output lists that todo.
  - Run `npm run job:due-reminders` with the default console mailer and check that the email is printed.
  - Run it again and check that the summary reports the user as skipped.
