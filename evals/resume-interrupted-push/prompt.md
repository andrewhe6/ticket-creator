---
tags: [ticket-creator]
max_turns: 60
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

i was pushing the due dates tickets in tickets/ to github and my laptop died partway through. finish the push, and don't create any duplicates.

Input files: package.json, README.md, src/, public/, tests/, .claude/ticket-creator.yml, tickets/todo-due-dates-reminders/, .fakegh/ (a stand-in for the GitHub CLI: the config's github.cli points at it, and .fakegh/fake_gh_state.json is the simulated GitHub it changes)
