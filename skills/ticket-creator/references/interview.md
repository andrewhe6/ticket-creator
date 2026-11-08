# Interview guide

Tickets are only as good as the understanding behind them. Whoever picks up a ticket later, whether a
teammate or an agent, can't ask the person who had the idea. But every question costs the user time, and
the review in step 7 already lets them correct anything. So ask few questions: only the ones whose answers
change the tickets.

## Ground rules

- **Research first.** Finish step 2 (input, codebase, existing tickets) before you ask anything. Never
  ask something the PRD or the code already answers. Ask about decisions, priorities, and intent.
- **Ask only what changes the tickets.** A question earns its place if a different answer would change
  which tickets exist, their scope, their order, or what their acceptance criteria say, *and* you can't
  pick a safe default. Everything else becomes a written assumption the user sees in the review.
  Test: "If they answer the other way, would I rewrite a ticket?" If not, don't ask.
- **Batch the questions.** Ask up to 4 per round. With a structured question tool (e.g.
  `AskUserQuestion`), give each question 2–4 concrete options and put your recommendation first, marked
  "(Recommended)". Without one, use a numbered list with a suggested default per question, so
  "defaults are fine" is a valid reply, and wait for the answer before drafting.
- **Make each question self-contained.** The user may see only the question dialog, so include the
  context needed to answer it ("The PRD says X but the code does Y; which wins?").
- **Use the team's words, not ours.** "Split by user-visible outcome" beats "vertical slices + enablers".
- **Show what you learned.** A one-line preface such as "I read the PRD and `src/billing/`; a few
  decisions are open" builds trust and lets the user correct a wrong premise early.
- **Record everything.** Each answer goes into the epic (Background, Non-goals, or Assumptions). Each
  assumption you make instead of asking is written down and marked *(assumed)*.
- **Nothing to ask is fine.** For a clear PRD or a small, well-specified feature, say "No open
  decisions; I'll draft with these assumptions: …" and continue.

## How deep to go

| Input | Rounds | Typical focus, when it's actually open |
|---|---|---|
| Detailed PRD / spec | 0–1 | The MVP cutline, gaps or contradictions you found in the doc |
| Small, clear feature in chat | 0–1 | The one or two behaviors that are truly ambiguous |
| Vague idea or large feature | 1–3 | Users and core flows, the MVP cutline, constraints, then slicing |
| Greenfield project | 2–4 | Product, stack and hosting, MVP scope, team, then slicing per epic |
| Codebase gap scan | 1 | Which areas, which kinds of gaps (tests, TODOs, debt, security), priority |
| Single ticket | 0–1, short | Expected vs. actual behavior (bugs), acceptance, priority |

**When to stop:** you can write acceptance criteria for every ticket without guessing what users should
see, and you know what's out of scope. Uncertainty that remains, and that only investigation can
resolve, becomes a time-boxed **spike** ticket, not another round of questions.

## Slicing strategy

Pick it without asking when it's already settled:

1. The user said it ("vertical slices", "one ticket per layer"). `slicing.md` explains how to read a
   bare "vertical slices".
2. You're adding to an existing epic: reuse its `slicing:`.
3. The config has `defaults.slicing`: the team already chose.

Otherwise use **vertical slices + enablers**, the right choice for most product features, and record it
in the epic's Assumptions. Ask only when the choice would really change the backlog: a greenfield project,
a large feature (more than ~8 tickets), or signs that separate people or teams own the layers. Ask in
plain words, for example:

> How should the work be split?
> 1. **By user-visible outcome (Recommended).** Each ticket delivers a thin working feature end to end;
>    shared groundwork (a migration, a storage adapter) gets its own small ticket.
> 2. **By outcome, no groundwork tickets.** Groundwork goes into the first ticket that needs it, so
>    every ticket is demoable.
> 3. **By layer** (database, API, UI). Only worth it when different people or teams own the layers.

These map to `slicing: vertical+enablers`, `vertical`, and `horizontal`. When the user answers, save it as
`defaults.slicing` in `.claude/ticket-creator.yml` (tell them), so the next run doesn't ask again.

What each one means, and when to recommend it:

- **Vertical slices + enablers** (default). Each ticket delivers a thin end-to-end behavior. Separate
  enabler tickets exist only for groundwork that several slices need, or that stands alone (CI, a
  migration with a backfill, a library upgrade). Recommend it for most product features in existing
  codebases.
- **Pure vertical slices.** Every ticket is user-visible, and groundwork is folded into the first slice
  that needs it. Recommend it when the infrastructure already exists, or the team wants a demo after
  every ticket.
- **Horizontal layers.** One ticket per layer or discipline (schema, API, UI, docs). Recommend it only
  when separate people or teams own the layers, or the work is mostly infrastructure with no
  user-facing slice.

## First run only: setup

With no `.claude/ticket-creator.yml`, add the setup questions from `config.md` (platform, destination,
epic style, board placement) to the **first** round. They usually fit in one or two questions, because
detection already narrowed the choices.

## When relevant: existing tickets

If step 2 found overlapping tickets, list them with one line each on how they overlap, and ask what to
do with each: **skip** (ours would duplicate it), **link** (related; add to `related`), or
**depend on** (ours needs it first; add its key to `blocked_by`). Also ask whether to file the new
tickets under an existing epic if one fits.

## Question bank

Pick the questions with the highest information value. Skip anything already answered, and anything
where a safe default exists.

**Outcome and scope**
- What should a user be able to do when this is done that they can't do today?
- What's the MVP cutline? Which parts could ship later as a follow-up epic?
- What is explicitly *not* part of this? (These become non-goals.)

**Users and flows** (vague ideas, greenfield)
- Who are the primary users, and what is the single most important flow?
- Are there roles or permissions that change behavior?

**Constraints**
- Deadlines or a release this must land in? (These can become `due:` or `milestone:`.)
- Technical constraints: must use or avoid a library, an API limit, backwards compatibility, data
  residency or compliance?
- Does it need a feature flag or a staged rollout?

**Data**
- New entities or schema changes? Existing data to migrate or backfill?

**Quality bar**
- Expected test coverage (unit/integration/e2e)? Performance targets? Accessibility or i18n
  requirements? Docs to update?

**Team and parallelism**
- How many people or agents will work on this in parallel? (More parallelism means wider waves and
  extra care about tickets touching the same files.)
- Who reviews? Is anything owned by another team? Should tickets be assigned to someone?

**Priority**
- Which parts are urgent or high priority versus nice-to-have?

**Greenfield extras**
- One-line product description and the first user.
- Stack preferences (language, framework, database), hosting/deploy target, auth approach.
- What does "first usable version" mean, and what can wait?
- Required from day one: CI, environments, observability, licensing?

**Codebase gap scan extras**
- Which directories or subsystems are in scope?
- Which kinds of gaps: missing tests, TODO/FIXME, outdated dependencies, error handling, security,
  docs?
- Should related small fixes be grouped into one ticket per area, or one ticket per finding?
- If security findings are in scope: is the board private? Vulnerability details shouldn't go on a
  public tracker.

## Non-interactive runs

If no one can answer (a scheduled job, an automated pipeline, or an explicit "don't ask me, just do
it"), still do the thinking. Pick the recommended option for each question and continue. Write the
questions and the answers you assumed into the epic's "Assumptions & open questions" section, so a
human can review them before pushing. For a single ticket, use an `## Assumptions` section at the end
of the ticket. In your reply, list only the most consequential assumptions (about 5–8), the ones that
would change tickets if wrong, and point to the epic for the rest.
