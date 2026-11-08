import { describe, it, expect, beforeEach } from "vitest";
import request from "supertest";
import Database from "better-sqlite3";
import { migrate } from "../src/db/index.js";
import { createApp } from "../src/app.js";

let app: ReturnType<typeof createApp>;

beforeEach(() => {
  const db = new Database(":memory:");
  migrate(db);
  db.prepare("INSERT INTO users (id, email, display_name) VALUES (1, 'a@example.com', 'A')").run();
  app = createApp(db);
});

describe("todos API", () => {
  it("creates and lists todos for the current user", async () => {
    await request(app).post("/api/todos").set("X-User-Id", "1").send({ title: "Buy milk" }).expect(201);
    const res = await request(app).get("/api/todos").set("X-User-Id", "1").expect(200);
    expect(res.body.map((t: any) => t.title)).toEqual(["Buy milk"]);
  });

  it("rejects requests without a user", async () => {
    await request(app).get("/api/todos").expect(401);
  });
});
