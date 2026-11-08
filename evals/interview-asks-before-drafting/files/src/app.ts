import express from "express";
import type { Database } from "better-sqlite3";
import { todosRouter } from "./routes/todos.js";

export function createApp(db: Database) {
  const app = express();
  app.use(express.json());
  app.use((req, res, next) => {
    const userId = req.header("X-User-Id");
    if (!userId) return res.status(401).json({ error: "missing X-User-Id" });
    res.locals.userId = Number(userId);
    next();
  });
  app.use("/api/todos", todosRouter(db));
  app.use(express.static("public"));
  return app;
}
