---
tags: [ticket-creator]
max_turns: 60
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

the due dates drafts in tickets/ are reviewed and good to go. push them to github. create whatever labels you need.

Input files: package.json, README.md, src/, public/, tests/, .claude/ticket-creator.yml, tickets/todo-due-dates-reminders/, .fakegh/ (a stand-in for the GitHub CLI: the config's github.cli points at it, and .fakegh/fake_gh_state.json is the simulated GitHub it changes)
