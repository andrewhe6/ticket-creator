import Database from "better-sqlite3";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const MIGRATIONS = join(import.meta.dirname, "migrations");

export function openDb(path: string) {
  const db = new Database(path);
  db.pragma("journal_mode = WAL");
  migrate(db);
  return db;
}

export function migrate(db: Database.Database) {
  db.exec("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)");
  const done = new Set(db.prepare("SELECT name FROM schema_migrations").pluck().all() as string[]);
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    if (done.has(file)) continue;
    db.exec(readFileSync(join(MIGRATIONS, file), "utf8"));
    db.prepare("INSERT INTO schema_migrations (name) VALUES (?)").run(file);
  }
}
