---
tags: [ticket-creator]
max_turns: 60
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

starting a new side project: a Go CLI called notesync that takes an Obsidian vault and publishes it as a static site to GitHub Pages. it needs to turn wikilinks into proper links, tags from frontmatter become tag index pages, and anything in a private/ folder or with `publish: false` in the frontmatter must never be published. solo dev, with AI agents doing a lot of the implementation. give me a starting backlog i can load into jira later (project key NS). don't ask me anything, just decide and tell me what you assumed. empty repo, nothing written yet.
