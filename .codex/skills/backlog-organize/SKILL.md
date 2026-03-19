---
name: "backlog-organize"
description: "Review and reorganize docs/backlog.md by removing resolved items, updating stale plans, clarifying blocked work, and reprioritizing in dependency order"
metadata:
  short-description: "Audit and reorganize backlog"
---

<codex_skill_adapter>
Codex skills-first mode:
- Invoke this skill by mentioning `$backlog-organize`.
- Treat all user text after `$backlog-organize` as optional focus guidance such as `just check P1` or `only prune resolved items`.
</codex_skill_adapter>

<objective>
Keep `docs/backlog.md` accurate, current, and ordered for execution.

The skill should:
1. Audit the active backlog against the current codebase and recent commits.
2. Identify resolved, stale, blocked, or mis-prioritized items.
3. Ask the user only for decisions that cannot be inferred safely.
4. Propose a concrete reorganization before editing.
5. Apply approved backlog updates and commit them.
</objective>

<process>
## 1. Read the current state
Read `docs/backlog.md` fully.
Also gather:
- recent commits, such as `git log --oneline -20`
- current code and tests for items that may already be resolved

## 2. Pick audit depth
Choose the lightest credible depth:
- `low`: a small backlog or simple resolved-item pruning
- `medium`: several items need verification across a few subsystems
- `high`: a broad audit with many stale or interdependent items

If independent verification work can run in parallel, use explorer subagents.

Suggested subagent policy:
- model: `gpt-5.4-mini`
- reasoning: match the chosen depth

## 3. Evaluate open items
For each active unchecked item, verify:
- still relevant
- accurately described
- correct priority
- blocked or unblocked by recent code changes

Prefer evidence from code, tests, and recent commits over backlog wording.

## 4. Resolve blocked items
For items that need product decisions or clarification:
- summarize the item briefly
- explain the decision needed
- present concrete options with tradeoffs
- recommend one option when the evidence supports it

Ask the user directly in concise plain text. Group related questions, but keep them small enough to answer easily.

## 5. Propose changes
Before editing, present a short plan grouped as:
- remove
- unblock
- update
- reprioritize
- reorder

Order remaining items within a priority by:
1. upstream blockers first
2. impact
3. effort when impact is similar

## 6. Apply the approved reorganization
After approval:
- update `docs/backlog.md`
- move resolved items to the completed section if the file uses one
- refresh stale descriptions and action plans
- reorder sections and items consistently
- update the "Last updated" date
- commit with `docs(backlog): reorganize - <short summary>`

## 7. End-of-turn report
Report:
- items removed, updated, or reprioritized
- any still-blocked items and the missing decisions
- backlog size after cleanup
- commit hash
</process>
