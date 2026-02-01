# AI Agent Roles — Multi-Agent Workflow

## Overview

This project can benefit from multiple AI agents with different roles. This document describes the recommended workflow.

## Agent Roles

### 1. Architect Agent

**Purpose**: Design systems, plan implementations, make architectural decisions.

**When to use**:
- Starting a new feature
- Refactoring existing code
- Making technology decisions
- Planning multi-file changes

**Capabilities needed**: High reasoning, broad context, system design

**Typical workflow**:
1. Analyze existing code structure
2. Understand requirements
3. Design solution architecture
4. Write implementation plan
5. Hand off to Implementer

**Output**: Plan document in `plan/` folder

### 2. Implementer Agent

**Purpose**: Write code following a plan, implement features.

**When to use**:
- After Architect creates a plan
- For well-defined tasks
- Adding new features with clear requirements

**Capabilities needed**: Code generation, pattern following

**Typical workflow**:
1. Read the plan document
2. Read relevant domain AGENTS.md
3. Implement code following plan
4. Run tests
5. Hand off to Reviewer

**Output**: Code changes, test updates

### 3. Reviewer Agent

**Purpose**: Check code quality, find bugs, suggest improvements.

**When to use**:
- After Implementer finishes
- Before committing changes
- When debugging issues

**Capabilities needed**: Code analysis, pattern recognition

**Typical workflow**:
1. Start with fresh context (don't reuse Implementer's session)
2. Read changed files
3. Check against project patterns
4. Run tests
5. Report issues or approve

**Output**: Review comments, approval, or issue list

### 4. Quick-Fix Agent

**Purpose**: Simple tasks, formatting, small fixes.

**When to use**:
- Typo fixes
- Formatting cleanup
- Simple one-line changes
- Adding comments

**Capabilities needed**: Basic code understanding

**Typical workflow**:
1. Read the specific file
2. Make the small change
3. Done

**Output**: Minor code changes

---

## Workflow Patterns

### Pattern A: Full Feature Development

```
User Request
    ↓
Architect Agent → Creates plan in plan/
    ↓
Implementer Agent → Writes code following plan
    ↓
Reviewer Agent → Reviews with fresh context
    ↓
Commit (if approved)
```

### Pattern B: Bug Fix

```
Bug Report
    ↓
Reviewer Agent → Investigates, identifies cause
    ↓
Implementer Agent → Fixes the bug
    ↓
Reviewer Agent → Verifies fix
    ↓
Commit
```

### Pattern C: Quick Change

```
Simple Request
    ↓
Quick-Fix Agent → Makes change
    ↓
Commit (if trivial) or Review (if uncertain)
```

---

## Agent Handoff Protocol

### When handing off between agents:

1. **Document the state**: What's done, what's next
2. **Clear context**: Don't assume next agent knows history
3. **Specific files**: List which files to read
4. **Expected output**: What should the next agent produce

### Example Handoff (Architect → Implementer):

```markdown
## Handoff: Add New Export Format

### Completed (Architect):
- Analyzed existing export patterns
- Designed new format structure
- Created plan: plan/features/obj-export.md

### For Implementer:
- Read: src/export/AGENTS.md
- Read: plan/features/obj-export.md
- Create: src/export/obj.js
- Update: src/export/index.js
- Add test: tests/unit/export/obj.test.js

### Expected Output:
- Working OBJ export function
- Passing tests
- Ready for review
```

---

## Capability Matching

| Task Type | Minimum Capability | Recommended |
|-----------|-------------------|-------------|
| Architecture | GPT-4 / Claude / Opus | Claude Opus |
| Implementation | GPT-3.5 / Claude / Sonnet | Claude Sonnet |
| Review | GPT-4 / Claude | Any capable model |
| Quick Fix | Any | Local LLM / Haiku |

---

## Tips for Multi-Agent Success

1. **Clear boundaries**: Each agent has one job
2. **Fresh context**: Start new sessions for review
3. **Document everything**: Plans, handoffs, decisions
4. **Don't skip review**: Implementers make mistakes
5. **Small iterations**: Better to do more small handoffs than one big one
