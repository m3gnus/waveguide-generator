---
name: backlog-add
description: Add a new item to docs/backlog.md with researched implementation plan. Spawns subagents to investigate feasibility and approach, then writes a well-structured backlog entry.
user-invokable: true
args:
  - name: description
    description: What needs to be fixed, added, or changed
    required: true
---

Capture a user's request (bug fix, feature, improvement) and add it to `docs/backlog.md` as a well-researched backlog item.

## Process

### 1. Understand the request

Read the user's description carefully. Determine:

- **Type**: bug fix, new feature, refactor, documentation, or investigation
- **Scope**: which files/modules are likely involved
- **Clarity**: is anything ambiguous or underspecified?

If anything is unclear, ask clarifying questions BEFORE researching. Good questions to ask:

- "Should this replace the existing behavior or be an option?"
- "What's the expected behavior when X happens?"
- "Is this related to [existing backlog item Y]?"
- "What priority would you give this — blocking, important, or nice-to-have?"

Do NOT proceed with research until the request is clear enough to act on.

### 2. Choose reasoning level for research

Assess the complexity of the request to pick the right model for research subagents:

| Complexity | Model    | Indicators                                                    |
| ---------- | -------- | ------------------------------------------------------------- |
| Simple     | `haiku`  | Single file, obvious fix, well-understood area                |
| Moderate   | `sonnet` | Multiple files, needs codebase exploration, design choices    |
| Complex    | `opus`   | Cross-module, architectural implications, needs deep analysis |

Indicators of complexity:

- **Simple**: "fix typo in X", "add parameter Y to config", "update docs for Z"
- **Moderate**: "add export format", "fix rendering bug in viewport", "improve error handling in solver"
- **Complex**: "redesign mesh pipeline", "add new simulation mode", "refactor state management"

### 3. Research the implementation

Spawn one or two Explore subagents (using the chosen model) to investigate:

- Which files would need to change
- What existing patterns to follow
- Whether similar functionality already exists
- Potential risks or gotchas
- Dependencies on other backlog items

If the research reveals the item is already implemented or no longer relevant, report that to the user and skip adding it.

### 4. Draft the backlog entry

Read the current `docs/backlog.md` to understand the format and existing priorities.

Write a backlog entry following the established format:

- Title with priority level (P1-P4)
- One-paragraph description of the problem/feature
- Implementation notes listing affected files
- Action plan with unchecked `- [ ]` items
- Any research findings or gotchas

Priority guidelines:

- **P1**: Bugs affecting correctness, missing core functionality
- **P2**: Architecture improvements, significant UX improvements
- **P3**: Nice-to-have features, minor improvements
- **P4**: Documentation, cleanup, low-impact polish

### 5. Review with user

Present the drafted entry to the user before adding it. Show:

- The proposed priority level
- The implementation plan summary
- Where it will be inserted in the backlog

Ask: "Does this look right? Should I adjust the priority or plan?"

### 6. Add to backlog

Insert the entry into `docs/backlog.md` at the correct position:

- Within the Active Backlog section
- Under the appropriate priority heading
- After any existing items of the same priority

Update the "Last updated" date at the top of the file.

Commit the change with message: `docs(backlog): add [short description]`
