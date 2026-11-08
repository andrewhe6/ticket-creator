import { Router } from "express";
import type { Database } from "better-sqlite3";
import { createTodoSchema, updateTodoSchema } from "../schemas/todo.js";
import { todoService } from "../services/todoService.js";

export function todosRouter(db: Database) {
  const router = Router();
  const todos = todoService(db);

  router.get("/", (_req, res) => res.json(todos.list(res.locals.userId)));

  router.post("/", (req, res) => {
    const parsed = createTodoSchema.safeParse(req.body);
    if (!parsed.success) return res.status(400).json({ error: parsed.error.flatten() });
    res.status(201).json(todos.create(res.locals.userId, parsed.data));
  });

  router.patch("/:id", (req, res) => {
    const parsed = updateTodoSchema.safeParse(req.body);
    if (!parsed.success) return res.status(400).json({ error: parsed.error.flatten() });
    const todo = todos.update(res.locals.userId, Number(req.params.id), parsed.data);
    return todo ? res.json(todo) : res.sendStatus(404);
  });

  router.delete("/:id", (req, res) =>
    todos.remove(res.locals.userId, Number(req.params.id)) ? res.sendStatus(204) : res.sendStatus(404));

  return router;
}
