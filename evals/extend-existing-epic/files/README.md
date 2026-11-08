# todo-app

Small team todo list. Express + SQLite API, vanilla JS frontend in `public/`.

- `npm run dev` – start on http://localhost:3000
- `npm test` – vitest + supertest API tests
- `npm run db:migrate` – apply SQL files in `src/db/migrations/` in order

Users are identified by the `X-User-Id` header set by our internal auth proxy; there is no login in this app.
User emails live in the `users` table (synced nightly from the directory).
