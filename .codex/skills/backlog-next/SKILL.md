---
name: "backlog-next"
description: "Continue docs/backlog.md by running one unfinished backlog slice after another, grounding on the latest git commits, and orchestrating fresh Codex 5.3 subagents with reasoning scaled to each slice"
metadata:
  short-description: "Run backlog slices in sequence"
---

<codex_skill_adapter>
Codex skills-first mode:
- This skill is invoked by mentioning `$backlog-next`.
- Treat all user text after `$backlog-next` as optional constraints.

Execution helper:
- Run `node ./.codex/skills/backlog-next/scripts/next-backlog-status.mjs --json` from repo root first.
- Use that output as the source of truth for the current backlog priority, recent commits, and the active backlog task list.
</codex_skill_adapter>

<objective>
Replace the repeated prompt "do the next backlog item; see last git commit for what has been done" with a deterministic multi-slice workflow.

The skill should:
1. Read the backlog status helper output.
2. Inspect the latest commit(s) only as additional grounding, not as the sole source of truth.
3. Choose the smallest coherent unfinished slice from the highest active backlog priority.
4. Orchestrate a fresh Codex 5.3 subagent to execute that slice.
5. After the slice lands and is committed, rerun the helper, choose the next slice, and hand it to a new Codex 5.3 subagent.
6. Pick reasoning effort per slice complexity, not once for the whole phase.
7. Run the narrowest relevant tests first, then broader tests if the slice completes.
8. Update docs affected by the change, including `docs/backlog.md` when priorities, relevance, or approach notes need to change.
9. Stop only when the backlog is empty, a blocker requires user input, or a test failure means the slice is not safely shippable.
10. When a broad Markdown documentation-overhaul item exists, keep it as the final cleanup slice after behavior-changing backlog work has settled.
</objective>

<process>
## 1. Backlog loop
Work in a loop for the active backlog:
1. Run the helper script and capture:
   - `currentPriority`
   - `currentPriorityTitle`
   - `recentCommits`
   - `openItems`
   - `baselineNotes`
   - `defaultReasoning`
2. Merge any user-supplied constraints after the generated briefing.
3. Pick the next slice using the selection rules below.
4. Spawn a fresh implementation subagent for that slice.
5. When the slice returns with code, tests, docs, and a commit, rerun the helper and repeat.

Do not keep implementing multiple slices in one long-lived worker. The whole point is to keep slice context isolated.

## 2. Pick the next slice
Use the active backlog priority as the planning boundary. Select the next slice with these rules:
- Prefer the smallest slice that removes one real architectural seam.
- Prefer slices that eliminate remaining direct imports, duplicate orchestration, or UI-owned business logic.
- Avoid broad file moves unless the current phase explicitly requires them.
- If the last commit already completed a likely slice, move to the next non-overlapping seam.
- If the backlog has researched item notes, treat unchecked items in the highest priority section as remaining work.
- If a slice would exceed one focused subagent session, split it again before execution.

State the chosen slice explicitly before execution in one sentence.

## 3. Subagent model and reasoning policy
Use Codex 5.3 for subagents.

Reasoning policy:
- `low`: docs-only, tests-only, single-file adapter cleanup, or obvious mechanical import rewires.
- `medium`: 2-5 file refactors inside one subsystem, targeted API reshaping, or controller/service extraction with stable contracts.
- `high`: cross-module contract moves, public API changes, task orchestration rewrites, or anything likely to require broad regression testing.

Default to one implementation subagent. Use a second subagent only when there is a clean split such as:
- implementation + test/doc follow-through
- parallel investigation of two independent candidate seams

Keep the orchestrator lean. Subagents should read only the files needed for the selected slice plus any scoped `AGENTS.md`.

## 4. Execution requirements
Subagents must:
- preserve the plan invariants from `AGENTS.md`
- keep edits local to the selected slice
- avoid unrelated cleanup
- run targeted tests first
- update plan notes/docs in the same slice when behavior or boundaries change
- commit the finished slice before handing back

## 5. Loop stop conditions
Continue phase execution until one of these conditions is true:
- there are no unchecked backlog items left
- there is no clear next slice without a product decision or missing information
- a targeted or broad regression test fails and the slice cannot be safely completed in the same run
- the user provided a stopping constraint such as a commit-count or time limit

If stopping early, report the exact blocker and the next candidate slice.

## 6. Documentation-overhaul trailing slice
If the backlog includes a broad Markdown-documentation overhaul, treat it as the last slice in the active phase rather than mixing it into earlier runtime refactors.

For that slice:
- re-audit maintained `.md` docs against current code, tests, and architecture boundaries
- optimize for readability, navigation, and source-of-truth clarity
- avoid rewriting archived historical records except for archive indexes or cross-links
- add or refresh docs-parity coverage when maintained claims should stay machine-checked

## 7. Required end-of-turn output
Report:
- slices completed in this run
- reasoning level used for each slice
- files changed
- tests run
- commit hashes
- whether the backlog is empty
- what the next likely slice is if backlog items remain
</process>
