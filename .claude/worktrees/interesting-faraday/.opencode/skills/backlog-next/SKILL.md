---
name: backlog-next
description: Continue docs/backlog.md by running one unfinished backlog slice after another, grounding on the latest git commits, and orchestrating fresh subagents with reasoning scaled to each slice
user-invokable: true
args:
  - name: constraint
    description: Optional stopping constraint (e.g., "only P1 items", "max 2 slices", "stop after symmetry")
    required: false
---

Execute unfinished backlog slices from `docs/backlog.md` one at a time until the backlog is empty or blocked.

## Process

### 1. Read backlog status

Read `docs/backlog.md` directly and gather context:

- Recent git commits: `git log --oneline -10`
- Current priority level and open items
- Implementation notes for the next item

### 2. Pick the next slice

From the highest-priority section with unchecked items:

- Pick the smallest coherent slice that removes one real issue or adds one real capability.
- If the backlog item has an action plan with unchecked sub-items, use the first unchecked sub-item as the slice.
- If the last commit already completed a likely slice, move to the next non-overlapping item.
- If a slice would be too large for one focused session, split it before execution.

State the chosen slice explicitly in one sentence before starting work.

### 3. Choose reasoning level per slice

Assign a model based on the slice's complexity:

| Level  | Model    | Use when                                                                            |
| ------ | -------- | ----------------------------------------------------------------------------------- |
| Low    | `sonnet` | Docs-only, single-file fix, mechanical cleanup, test update                         |
| Medium | `sonnet` | 2-5 file changes within one subsystem, targeted refactor                            |
| High   | `opus`   | Cross-module changes, public API changes, anything needing broad regression testing |

### 4. Execute the slice

Spawn a subagent (Task tool with subagent_type="general" or "explore") for the slice with a clear, self-contained prompt that includes:

- What to change and why
- Which files to read first
- What tests to run
- What to update in `docs/backlog.md` when done (check off completed items)

Subagent requirements:

- Keep edits local to the selected slice
- No unrelated cleanup
- Run targeted tests first, broader tests if slice completes
- Update docs affected by the change
- Check off completed items in `docs/backlog.md`

### 5. After each slice

- Verify the subagent's changes (check git diff, test results)
- Commit the finished slice
- Re-read `docs/backlog.md` to see what remains
- Pick the next slice and repeat

### 6. Stop conditions

Stop when any of these are true:

- No unchecked backlog items remain
- A slice needs a product decision or user input — ask the user
- A test failure means the slice cannot be safely completed
- The user provided a stopping constraint (slice count, priority filter, etc.)

If stopping early, report the blocker and what the next slice would be.

### 7. End-of-run report

After all slices are done (or stopped), report:

- Slices completed and reasoning level used for each
- Files changed
- Tests run and results
- Whether the backlog is empty
- Next likely slice if items remain
