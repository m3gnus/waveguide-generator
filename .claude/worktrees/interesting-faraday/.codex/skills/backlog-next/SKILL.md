---
name: "backlog-next"
description: "Continue docs/backlog.md by running one unfinished backlog slice after another, grounding on the latest git commits, and routing each slice to GLM-5 or Codex with local verification before acceptance"
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
- Route each selected slice through `node ./.codex/skills/backlog-next/scripts/run-backlog-worker.mjs`.
</codex_skill_adapter>

<objective>
Replace the repeated prompt "do the next backlog item; see last git commit for what has been done" with a deterministic multi-slice workflow.

The skill should:
1. Read the backlog status helper output.
2. Inspect the latest commit(s) only as additional grounding, not as the sole source of truth.
3. Choose the smallest coherent unfinished slice from the highest active backlog priority.
4. Route low/medium slices to a fresh GLM-5 worker via `opencode`, and route high-complexity slices to a fresh Codex subagent.
5. Verify every GLM-produced change locally before acceptance by reviewing the diff, running the relevant tests yourself, and confirming the resulting code still matches the selected slice.
6. After the slice lands, is verified, and is committed, rerun the helper, choose the next slice, and hand it to a new worker.
7. Pick reasoning effort per slice complexity, not once for the whole phase.
8. Run the narrowest relevant tests first, then broader tests if the slice completes.
9. Update docs affected by the change, including `docs/backlog.md` when priorities, relevance, or approach notes need to change.
10. Stop only when the backlog is empty, a blocker requires user input, or a test failure means the slice is not safely shippable.
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
   - `defaultExecutor`
   - `executorPolicy`
   - `verificationChecklist`
2. Merge any user-supplied constraints after the generated briefing.
3. Pick the next slice using the selection rules below.
4. Route the slice to a fresh worker using the executor rules below.
5. Verify the returned change locally, commit the accepted slice, then rerun the helper and repeat.

Do not keep implementing multiple slices in one long-lived worker. The whole point is to keep slice context isolated. That rule applies to both `opencode` GLM runs and Codex subagents.

## 2. Pick the next slice
Use the active backlog priority as the planning boundary. Select the next slice with these rules:
- Prefer the smallest slice that removes one real architectural seam.
- Prefer slices that eliminate remaining direct imports, duplicate orchestration, or UI-owned business logic.
- Avoid broad file moves unless the current phase explicitly requires them.
- If the last commit already completed a likely slice, move to the next non-overlapping seam.
- If the backlog has researched item notes, treat unchecked items in the highest priority section as remaining work.
- If a slice would exceed one focused subagent session, split it again before execution.

State the chosen slice explicitly before execution in one sentence.

## 3. Executor and reasoning policy
Use two worker types:
- `glm-5` via `opencode run` for most slices.
- a native Codex subagent for complex slices or recovery when GLM output fails verification.

Reasoning policy:
- `low`: docs-only, tests-only, single-file adapter cleanup, or obvious mechanical import rewires.
- `medium`: 2-5 file refactors inside one subsystem, targeted API reshaping, or controller/service extraction with stable contracts.
- `high`: cross-module contract moves, public API changes, task orchestration rewrites, or anything likely to require broad regression testing.

Executor policy:
- `low` => use `glm-5`.
- `medium` => use `glm-5`.
- `high` => use Codex.
- Escalate a `low` or `medium` slice from `glm-5` to Codex when GLM output fails verification, stalls, or starts spreading outside the selected seam.

When using `glm-5`, run it non-interactively through `opencode` and treat it as an implementation worker only. The orchestrator remains responsible for:
- selecting the slice
- protecting repo state and unrelated user changes
- checking the resulting diff
- running acceptance tests
- deciding whether the slice is actually complete

Recommended helper usage:
- Build or inspect the worker route:
  `node ./.codex/skills/backlog-next/scripts/run-backlog-worker.mjs --executor auto --reasoning <low|medium|high> --prompt-file <slice-prompt.txt>`
- Run a GLM slice:
  `node ./.codex/skills/backlog-next/scripts/run-backlog-worker.mjs --executor auto --reasoning <low|medium> --prompt-file <slice-prompt.txt> --run`
- Hand a Codex slice to a native subagent:
  `node ./.codex/skills/backlog-next/scripts/run-backlog-worker.mjs --executor auto --reasoning high --prompt-file <slice-prompt.txt>`
  Then use the returned `codex.handoff` payload when spawning the subagent.

Default to one implementation worker. Use a second worker only when there is a clean split such as:
- implementation + test/doc follow-through
- parallel investigation of two independent candidate seams

Keep the orchestrator lean. Workers should read only the files needed for the selected slice plus any scoped `AGENTS.md`.

## 4. Execution requirements
Workers must:
- preserve the plan invariants from `AGENTS.md`
- keep edits local to the selected slice
- avoid unrelated cleanup
- run targeted tests first
- update plan notes/docs in the same slice when behavior or boundaries change
- commit the finished slice before handing back

Additional GLM verification requirements:
- never trust the worker summary by itself; inspect the actual working tree and diff
- rerun the narrowest relevant tests yourself after the worker returns
- run broader tests when the slice touches a contract, entry point, shared helper, or exported behavior
- confirm docs and `docs/backlog.md` reflect what actually landed
- if the change is incomplete or suspect, either repair it locally or hand the same slice to Codex with the verification findings

Keep a local acceptance snapshot per slice with:
- selected slice
- executor used
- reasoning level
- files changed
- targeted tests run and result
- broader tests run and result
- commit hash
- residual risks or follow-up notes

## 5. Loop stop conditions
Continue phase execution until one of these conditions is true:
- there are no unchecked backlog items left
- there is no clear next slice without a product decision or missing information
- a targeted or broad regression test fails and the slice cannot be safely completed in the same run
- the user provided a stopping constraint such as a commit-count or time limit

If stopping early, report the exact blocker and the next candidate slice.

## 6. Required end-of-turn output
Report:
- slices completed in this run
- executor used for each slice
- reasoning level used for each slice
- files changed
- tests run and whether the orchestrator verified them locally
- commit hashes
- whether the backlog is empty
- what the next likely slice is if backlog items remain
</process>
