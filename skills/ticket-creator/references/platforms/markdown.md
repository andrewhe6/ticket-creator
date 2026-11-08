# Markdown only (no board)

With `platform: markdown`, or when no platform can be reached, the draft folder is the final output.
It works as a lightweight backlog that lives next to the code.

## Finish

1. Run `tickets.py render` and `tickets.py validate` until validation passes. The epic's Plan block is
   the board view: the table, the build waves, and the graph.
2. Leave `remote_*` fields as `null`.
3. In the hand-off, give the folder path and the wave-1 tickets, and explain how to use it:
   - **Implement a ticket**: hand the file to a developer or an agent, for example
     `/implement tickets/<epic>/02-<slug>.md` in Claude Code. Each file is self-contained.
   - **Track progress**: set `status: in-progress` or `status: done` in a ticket's frontmatter, then
     run `tickets.py render`. The epic's table gains a Status column, so it works as a simple board. Keep
     finished tickets in the folder; moving or deleting them breaks the `blocked_by` references
     that point at them.
   - **Commit the folder** so the team shares one backlog and changes show up in review.
   - **Push later**: set `platform` in `.claude/ticket-creator.yml` and ask to "push
     tickets/<epic>". Pending items get created, and the drafts then record the links. Remove the
     `status:` fields first or keep them; the board becomes the truth once pushed.

## Duplicate search without a board

Search the existing drafts folder (`<drafts_dir>/**/*.md`) for overlapping titles. If the repo uses
GitHub but the user chose markdown, you may still search issues read-only with `gh` to report overlap.
Never write to the board in this mode.
