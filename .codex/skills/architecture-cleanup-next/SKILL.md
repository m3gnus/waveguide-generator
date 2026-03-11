---
name: "architecture-cleanup-next"
description: "Continue docs/ARCHITECTURE_CLEANUP_PLAN.md by selecting the next unfinished slice from the current phase, grounding on the latest git commit, and orchestrating Codex 5.3 subagents with reasoning scaled to task complexity"
metadata:
  short-description: "Run the next architecture-cleanup slice"
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
Replace the repeated prompt "do the next task in ARCHITECTURE_CLEANUP_PLAN.md; see last git commit for what has been done" with a deterministic workflow.

The skill should:
1. Read the cleanup-plan status helper output.
2. Inspect the latest commit(s) only as additional grounding, not as the sole source of truth.
3. Choose the smallest coherent unfinished slice in the current phase.
4. Orchestrate Codex 5.3 subagents to execute that slice.
5. Pick reasoning effort from slice complexity.
6. Run the narrowest relevant tests first, then broader tests if the slice completes.
7. Update docs affected by the change, including `docs/ARCHITECTURE_CLEANUP_PLAN.md` implementation notes when appropriate.
8. End with a commit for the completed slice.
</objective>

<process>
## 1. Build the briefing
Run the helper script and capture:
- `currentPhase`
- `phaseTitle`
- `recentCommits`
- `phaseTasks`
- `implementationNotes`
- `defaultReasoning`

If the user supplied extra constraints, merge them after the generated briefing.

## 2. Pick the next slice
Use the current phase section as the planning boundary. Select the next slice with these rules:
- Prefer the smallest slice that removes one real architectural seam.
- Prefer slices that eliminate remaining direct imports, duplicate orchestration, or UI-owned business logic.
- Avoid broad file moves unless the current phase explicitly requires them.
- If the last commit already completed a likely slice, move to the next non-overlapping seam.
- If the phase has a checklist or numbered implementation notes, treat unchecked or unmentioned seams as remaining work.

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

## 5. Required end-of-turn output
Report:
- chosen slice
- reasoning level used
- files changed
- tests run
- commit hash
- what the next likely slice is
</process>
