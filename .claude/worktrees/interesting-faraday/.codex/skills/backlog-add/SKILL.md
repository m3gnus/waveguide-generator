---
name: "backlog-add"
description: "Add a new item to docs/backlog.md with a researched implementation plan, using Codex subagents when the scope warrants it"
metadata:
  short-description: "Research and add backlog items"
---

<codex_skill_adapter>
Codex skills-first mode:
- Invoke this skill by mentioning `$backlog-add`.
- Treat all user text after `$backlog-add` as the backlog request to analyze.
</codex_skill_adapter>

<objective>
Turn a user request into a well-scoped, researched entry in `docs/backlog.md`.

The skill should:
1. Understand the requested bug, feature, refactor, doc task, or investigation.
2. Clarify only when the missing detail would make the backlog item misleading.
3. Research the current codebase and recent commits before drafting the plan.
4. Draft the backlog entry in the existing project format and priority system.
5. Show the proposed entry to the user for confirmation before editing the backlog.
6. Add the approved entry, update the backlog date, and commit the change.
</objective>

<process>
## 1. Understand the request
Read the user description carefully and determine:
- task type: bug fix, feature, refactor, docs, or investigation
- likely scope: which modules or contracts are involved
- whether the request is clear enough to research without inventing product intent

Ask the user a concise direct question only if a missing decision would materially change the backlog entry.

## 2. Pick research depth
Choose the lightest approach that can produce a credible plan:
- `low`: obvious single-file or docs-only change
- `medium`: multiple files, one subsystem, or moderate design choice
- `high`: cross-module or contract-heavy work

If parallel research would help and the task is not blocked on the answer immediately, spawn one or two subagents.

Suggested subagent policy:
- model: `gpt-5.4-mini`
- reasoning: match the chosen depth
- use explorers for read-only investigation

Research should answer:
- which files are likely to change
- what current code paths and tests already exist
- whether similar functionality is already implemented
- notable risks, dependencies, or gotchas

If research shows the request is already done or obsolete, tell the user and stop.

## 3. Draft the entry
Read `docs/backlog.md` to match the existing style and placement.

Draft an entry with:
- a priority tag `P1` to `P4`
- a short title
- one concise problem statement
- implementation notes naming the affected files or modules
- an action plan using unchecked `- [ ]` items
- any key research findings or constraints

Priority guidance:
- `P1`: correctness bugs or missing core capability
- `P2`: important UX or architectural improvements
- `P3`: useful but non-critical enhancements
- `P4`: cleanup, docs, or low-impact polish

## 4. Review with the user
Before editing the file, present:
- proposed priority
- brief implementation plan summary
- where it will be inserted

Ask for confirmation or adjustment.

## 5. Apply the change
After approval:
- insert the entry into `docs/backlog.md` in the correct priority section
- keep the backlog ordering consistent
- update the "Last updated" date
- commit with `docs(backlog): add <short description>`

## 6. End-of-turn report
Report:
- final priority chosen
- files researched
- whether subagents were used
- exact backlog file updated
- commit hash
</process>
