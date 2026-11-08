---
id: T02
title: Email service with SMTP transport and console and in-memory dev mailers
type: enabler
size: S
priority: medium
labels: [backend, email]
blocked_by: []
related: []
remote_key: null
remote_url: null
remote_id: null
push_started: "2026-11-07T20:27:04Z"
---

## Context

The morning reminder (T04, "a reminder email in the morning for anything that's due that day") has to
send email, and the app can't do that today. Its only runtime dependencies are `express`,
`better-sqlite3`, and `zod`. This enabler adds a small mailer module. It's split out for two reasons: it
brings in a new third-party dependency and new env config worth reviewing on their own, and it lets T04
be built and tested against a fake without a real provider.

## Scope

**In scope**
- A `Mailer` interface: `send({ to, subject, text }): Promise<void>` (plain text only)
- An SMTP mailer built on `nodemailer`, configured by `SMTP_URL` and `MAIL_FROM`
- A console mailer for local dev that prints messages instead of sending them
- An in-memory mailer for tests that records what it was asked to send
- `createMailerFromEnv()`, which picks a mailer from `MAIL_TRANSPORT` (`console` by default, or `smtp`)
  and fails fast with a clear message when config is missing
- A README section listing the new env vars

**Out of scope**
- Choosing the production provider and credentials (T06)
- Reminder logic and email content (T04)
- HTML email, templates, attachments, retries, or queues
- Wiring the mailer into `src/server.ts` or any route (nothing sends email yet)

## Acceptance criteria

- [ ] With `MAIL_TRANSPORT` unset, `createMailerFromEnv()` returns the console mailer, and `send()`
      prints to/subject/text to stdout without sending anything
- [ ] With `MAIL_TRANSPORT=smtp`, `SMTP_URL`, and `MAIL_FROM` set, `send()` delivers a plain-text message
      with `From: <MAIL_FROM>` through that SMTP server
- [ ] With `MAIL_TRANSPORT=smtp` and `SMTP_URL` or `MAIL_FROM` missing, `createMailerFromEnv()` throws
      immediately with an error that names the missing variable(s), rather than failing on first send
- [ ] An unknown `MAIL_TRANSPORT` value throws an error that lists the allowed values
- [ ] The in-memory mailer exposes the messages it received, in order
- [ ] A failed SMTP send rejects the promise returned by `send()`, so callers decide how to handle it
- [ ] `SMTP_URL`, which can contain a password, never appears in logs or error messages

## Technical notes

- Relevant code: there's no existing email code, so this adds a new folder, `src/email/`. Config
  elsewhere is read straight from `process.env` (`src/server.ts` reads `DB_PATH` and `PORT`). Follow
  that pattern rather than adding a config library.
- Approach:
  - New file `src/email/mailer.ts` exporting `Mailer`, `createSmtpMailer()`, `createConsoleMailer()`,
    `createMemoryMailer()`, and `createMailerFromEnv(env = process.env)`.
  - `nodemailer.createTransport(url)` accepts a connection URL (`smtp://` or
    `smtps://user:pass@host:port`), which covers SES, SendGrid, Postmark, and internal relays.
  - Let `createSmtpMailer()` take an optional nodemailer transport or options object, so tests can pass
    `{ jsonTransport: true }` and avoid the network.
  - Add `nodemailer` to `dependencies` and `@types/nodemailer` to `devDependencies`.
- Watch out for:
  - The project is ESM (`"type": "module"`), and local imports use `.js` suffixes (for example
    `./routes/todos.js`). Match that.
  - `MAIL_TRANSPORT` defaults to `console` so a dev machine never emails real people by accident.
    Production sets `smtp` explicitly (T06).
- Same wave: T01 only touches `src/db/migrations/` and the `Todo` interface, so there's no overlap.

## Verification

- Automated:
  - New `tests/mailer.test.ts` covering env parsing (the default is console, smtp with full config
    works, missing variables throw, an unknown transport throws).
  - The memory mailer records messages.
  - The console mailer writes to stdout (spy on `console.log`).
  - The SMTP mailer, given `{ jsonTransport: true }`, produces the right From, To, Subject, and text.
  - Run `npm test` and `npm run lint`.
- Manual:
  - Start a local SMTP catcher, for example Mailpit:
    `docker run --rm -p 1025:1025 -p 8025:8025 axllent/mailpit`.
  - Set `MAIL_TRANSPORT=smtp`, `SMTP_URL=smtp://localhost:1025`, and `MAIL_FROM=todos@example.com`.
  - Call `send()` from a scratch script with `npx tsx` and check that the message appears at
    http://localhost:8025.
