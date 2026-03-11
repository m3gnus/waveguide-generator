---
name: "architecture-cleanup-next"
description: "Continue docs/ARCHITECTURE_CLEANUP_PLAN.md by running one unfinished slice after another from the current phase, grounding on the latest git commits, and orchestrating fresh Codex 5.3 subagents with reasoning scaled to each slice"
metadata:
  short-description: "Run architecture cleanup slices in sequence"
---

<codex_skill_adapter>
Codex skills-first mode:
- This skill is invoked by mentioning `$architecture-cleanup-next`.
- Treat all user text after `$architecture-cleanup-next` as optional constraints.

Execution helper:
- Run `node ./.codex/skills/architecture-cleanup-next/scripts/next-architecture-cleanup-status.mjs --json` from repo root first.
- Use that output as the source of truth for current phase, recent commits, and the current phase task list.
</codex_skill_adapter>

<objective>
Replace the repeated prompt "do the next task in ARCHITECTURE_CLEANUP_PLAN.md; see last git commit for what has been done" with a deterministic multi-slice workflow.

The skill should:
1. Read the cleanup-plan status helper output.
2. Inspect the latest commit(s) only as additional grounding, not as the sole source of truth.
3. Choose the smallest coherent unfinished slice in the current phase.
4. Orchestrate a fresh Codex 5.3 subagent to execute that slice.
5. After the slice lands and is committed, rerun the helper, choose the next slice, and hand it to a new Codex 5.3 subagent.
6. Pick reasoning effort per slice complexity, not once for the whole phase.
7. Run the narrowest relevant tests first, then broader tests if the slice completes.
8. Update docs affected by the change, including `docs/ARCHITECTURE_CLEANUP_PLAN.md` implementation notes when appropriate.
9. Stop only when the current phase is complete, a blocker requires user input, or a test failure means the slice is not safely shippable.
</objective>

<process>
## 1. Phase loop
Work in a loop for the active phase:
1. Run the helper script and capture:
   - `currentPhase`
   - `phaseTitle`
   - `recentCommits`
   - `phaseTasks`
   - `implementationNotes`
   - `defaultReasoning`
2. Merge any user-supplied constraints after the generated briefing.
3. Pick the next slice using the selection rules below.
4. Spawn a fresh implementation subagent for that slice.
5. When the slice returns with code, tests, docs, and a commit, rerun the helper and repeat.

Do not keep implementing multiple slices in one long-lived worker. The whole point is to keep slice context isolated.

## 2. Pick the next slice
Use the current phase section as the planning boundary. Select the next slice with these rules:
- Prefer the smallest slice that removes one real architectural seam.
- Prefer slices that eliminate remaining direct imports, duplicate orchestration, or UI-owned business logic.
- Avoid broad file moves unless the current phase explicitly requires them.
- If the last commit already completed a likely slice, move to the next non-overlapping seam.
- If the phase has a checklist or numbered implementation notes, treat unchecked or unmentioned seams as remaining work.
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
- the phase exit criteria are satisfied and the plan status can be advanced
- there is no clear next slice without a product decision or missing information
- a targeted or broad regression test fails and the slice cannot be safely completed in the same run
- the user provided a stopping constraint such as a commit-count or time limit

If stopping early, report the exact blocker and the next candidate slice.

## 6. Required end-of-turn output
Report:
- slices completed in this run
- reasoning level used for each slice
- files changed
- tests run
- commit hashes
- whether the current phase is complete
- what the next likely slice is if the phase is still open
</process>
