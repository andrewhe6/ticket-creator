---
id: T06
title: Reminder job runs automatically every morning in production
type: chore
size: S
priority: medium
labels: [ops, email]
blocked_by: [T04]
related: []
remote_key: "#7"
remote_url: "https://github.com/acme-example-org/todo-app/issues/7"
remote_id: "1007"
remote_platform: "github"
remote_hash: "bfa7b4df9331"
remote_sent: "020dd30205bd"
remote_blocked_by: ["T04"]
remote_pushed_at: "2026-11-07T20:27:06Z"
---

## Context

"A reminder email in the morning for anything that's due that day" (chat request, 2026-11-06). T04
provides `npm run job:due-reminders`. This ticket makes it run every morning in production with real
email. Nothing in the repo shows how production runs (there's no Dockerfile, CI config, or process
manager config), so the first step is to confirm that with whoever deploys `todo-app`.

## Scope

**In scope**
- Choose the production SMTP provider or relay and a from-address, and store `SMTP_URL` as a secret
- Set production env for the job: `MAIL_TRANSPORT=smtp`, `SMTP_URL`, `MAIL_FROM`, `REMINDER_TIMEZONE`,
  `APP_BASE_URL`, and `DB_PATH` (the same file the server uses)
- Schedule a run at 08:00 in `REMINDER_TIMEZONE` every day, using whatever runs production: crontab, a
  systemd timer, a Kubernetes CronJob, or the platform's scheduler
- Make failed runs (non-zero exit) visible to the team
- Add a README section on reminders in production: where the schedule lives, the env vars, and how to
  run the job by hand or re-run it

**Out of scope**
- Changing the job's logic or the email content (T04)
- Per-user send times or timezones (non-goal)
- Dashboards or metrics beyond surfacing failures

## Acceptance criteria

- [ ] `npm run job:due-reminders -- --dry-run` (or its compiled equivalent), run in production, lists
      the expected recipients for today and sends nothing
- [ ] On the first scheduled morning, a teammate with an open todo due that day gets exactly one reminder
      from `MAIL_FROM`, and a teammate with nothing due gets nothing
- [ ] The scheduled job reads and writes the same SQLite file as the running server (`DB_PATH`)
- [ ] A failed run shows up somewhere the team will notice (cron mail, a log alert, or the platform's
      failed-job view), and the README says where
- [ ] The README documents the schedule, the env vars, and how to re-send a missed day with
      `npm run job:due-reminders -- --date YYYY-MM-DD`
- [ ] `SMTP_URL` lives in the secret store and isn't committed to the repo

## Technical notes

- Relevant code:
  - `src/server.ts`: production reads `DB_PATH` and `PORT`.
  - `package.json`: `start` is `node dist/server.js` after `build` (`tsc -p .`), and `tsx` is a
    devDependency.
- Approach: run the job the same way the server runs. If production runs the compiled `dist/`, the job
  is `node dist/jobs/sendDueReminders.js`. If dev dependencies are installed there,
  `npm run job:due-reminders` works as is.
- Watch out for:
  - SQLite is a local file, so the job has to run on the same host or volume as the server. A separate
    container needs the same volume mounted.
  - I didn't find a `tsconfig.json` in the repo, and `src/db/index.ts` reads `.sql` migrations from
    `import.meta.dirname/migrations`, which `tsc` doesn't copy into `dist/`. Check how production
    handles migrations today before choosing the compiled path.
  - Timezones: the scheduler's clock has to match `REMINDER_TIMEZONE`. Use a Kubernetes CronJob's
    `spec.timeZone`, or for crontab, leave `REMINDER_TIMEZONE` unset so the job falls back to the host
    timezone, as cron does.
  - Re-runs are safe, because T04 records each send per user per date.
- Same wave: T05 only touches `public/`, so there's no overlap.

## Verification

- Automated: none, since this is an ops change. If any repo files change (README, deploy config), run
  `npm test`.
- Manual:
  - Run the dry run in production. Compare its output with
    `SELECT user_id, title FROM todos WHERE due_date = '<today>' AND done = 0`.
  - Create a todo due tomorrow for yourself and confirm the email arrives around 08:00 the next morning.
  - Confirm `reminder_sends` has exactly one row for you for that date.
  - Run the job again by hand and confirm no second email arrives.
