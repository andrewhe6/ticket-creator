---
tags: [ticket-creator]
max_turns: 60
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

file a bug for me: PATCH /api/todos/:id with an empty body {} returns 200 and silently does nothing, it should be a 400. we use github but just draft it, i'll push it myself later. don't ask me anything.

Input files: package.json, README.md, src/, public/, tests/, .claude/ticket-creator.yml
