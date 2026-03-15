---
name: backlog-organize
description: Review and reorganize docs/backlog.md — remove resolved items, reprioritize, reorder by importance/upstream-first, and update stale plans.
user-invokable: true
args:
  - name: focus
    description: Optional focus area (e.g., "just check P1", "only prune resolved items")
    required: false
---

Audit the entire backlog for relevance, accuracy, and priority ordering. Remove what's done, update what's changed, and reorder for maximum impact.

## Process

### 1. Read current state

Read `docs/backlog.md` in full. Also gather context:
- Recent git commits (`git log --oneline -20`) to see what's been done recently
- The codebase state for items that might have been resolved outside the backlog process

### 2. Choose reasoning level

Assess the scope of the reorganization:

| Scope | Model | When |
|-------|-------|------|
| Light | `haiku` | Few items, mostly checking if things are done |
| Moderate | `sonnet` | 5-10 items to evaluate, some need codebase checks |
| Heavy | `opus` | Many items, need deep analysis of what's still relevant |

Count the open items and complexity of verification needed. Use the lightest model that can do the job reliably.

### 3. Check each open item

For each unchecked item in the Active Backlog, spawn Explore subagents (in parallel where possible) to verify:

- **Still relevant?** Check if the issue still exists in the code. Look at recent commits that may have fixed it.
- **Accurately described?** Has the codebase changed in ways that affect the implementation plan?
- **Right priority?** Has context changed (new bugs, user feedback, dependencies resolved) that should shift priority?
- **Blocked or unblocked?** Have dependencies been resolved that previously blocked this item?

### 4. Propose changes

Compile findings into a change summary:

- **Remove**: Items that are fully resolved (move to Completed section with date)
- **Update**: Items where the description or plan needs adjustment
- **Reprioritize**: Items that should move up or down
- **Reorder**: Within each priority level, order by:
  1. Upstream-first (items that unblock other items go first)
  2. Impact (higher user/correctness impact first)
  3. Effort (smaller items first when impact is similar)

### 5. Review with user

Present the proposed changes to the user. For each change, briefly explain why. Ask for confirmation before applying, especially for:
- Any item being removed (might still be wanted)
- Any priority changes (user may have different priorities)
- Any significant plan rewrites

Use AskUserQuestion if there are items where the right action is genuinely ambiguous.

### 6. Apply changes

Edit `docs/backlog.md` with all approved changes:
- Move resolved items to the Completed section
- Update descriptions and plans
- Reorder items within priority sections
- Update the "Last updated" date

Commit with message: `docs(backlog): reorganize — [brief summary of changes]`

### 7. Report

Summarize what changed:
- Items removed (and why)
- Items reprioritized (and why)
- Items updated (what changed)
- Current backlog size and top priority
