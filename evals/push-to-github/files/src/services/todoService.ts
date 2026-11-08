import type { Database } from "better-sqlite3";
import type { CreateTodo, UpdateTodo } from "../schemas/todo.js";

export interface Todo {
  id: number;
  user_id: number;
  title: string;
  done: boolean;
  created_at: string;
}

const toTodo = (row: any): Todo => ({ ...row, done: Boolean(row.done) });

export function todoService(db: Database) {
  return {
    list(userId: number): Todo[] {
      return db.prepare("SELECT * FROM todos WHERE user_id = ? ORDER BY created_at DESC").all(userId).map(toTodo);
    },
    create(userId: number, input: CreateTodo): Todo {
      const info = db.prepare("INSERT INTO todos (user_id, title) VALUES (?, ?)").run(userId, input.title);
      return this.get(userId, Number(info.lastInsertRowid))!;
    },
    get(userId: number, id: number): Todo | undefined {
      const row = db.prepare("SELECT * FROM todos WHERE id = ? AND user_id = ?").get(id, userId);
      return row ? toTodo(row) : undefined;
    },
    update(userId: number, id: number, input: UpdateTodo): Todo | undefined {
      const current = this.get(userId, id);
      if (!current) return undefined;
      const next = { ...current, ...input };
      db.prepare("UPDATE todos SET title = ?, done = ? WHERE id = ? AND user_id = ?")
        .run(next.title, next.done ? 1 : 0, id, userId);
      return this.get(userId, id);
    },
    remove(userId: number, id: number): boolean {
      return db.prepare("DELETE FROM todos WHERE id = ? AND user_id = ?").run(id, userId).changes > 0;
    },
  };
}
