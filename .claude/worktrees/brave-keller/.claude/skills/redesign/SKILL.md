---
name: redesign
description: Upgrade existing interfaces incrementally. Audits current state, identifies highest-impact problems, and applies targeted fixes rather than rebuilding from zero.
user-invokable: true
args:
  - name: area
    description: The component, page, or area to redesign (optional)
    required: false
---

Systematically improve an existing interface by diagnosing problems, prioritizing fixes, and applying targeted improvements. Don't rebuild—refine.

**First**: Use the frontend-design skill for design principles and anti-patterns.

## The Redesign Philosophy

This is not a rebuild. The goal is maximum improvement with minimum disruption:

1. **Preserve what works** - Don't change functional, well-designed elements
2. **Fix the biggest problems first** - 80% of improvement comes from 20% of changes
3. **Make incremental progress** - Each change should be shippable independently
4. **Maintain existing behavior** - Don't break working functionality

## Phase 1: Discovery & Audit

Before changing anything, understand what you're working with.

### A. Quick Scan (5 minutes)

Get the lay of the land:

1. **Identify the target**: What component/page/area are we redesigning?
2. **Find the entry points**: Where is this code? List all relevant files.
3. **Understand dependencies**: What does this depend on? What depends on it?
4. **Check for tests**: Are there tests that might break?

### B. Anti-Pattern Scan (CRITICAL)

Run the AI Slop Detection from the frontend-design skill:

- [ ] AI color palette (purple/blue gradients, neon accents)
- [ ] Generic fonts (Inter, system-ui without intention)
- [ ] Gradient text on headings
- [ ] Centered hero sections with symmetric layouts
- [ ] Identical 3-column card grids
- [ ] Glassmorphism without purpose
- [ ] Dark mode with glowing button accents
- [ ] Generic placeholder content ("John Doe", "99.99%")
- [ ] Missing or poor empty/loading/error states
- [ ] Oversaturated accent colors

**Score**: Count the checks. If 5+, the interface needs significant work. If 8+, consider if a rebuild is actually warranted.

### C. Severity Assessment

Rate issues on impact vs effort:

| Issue                      | User Impact  | Effort to Fix | Priority  |
| -------------------------- | ------------ | ------------- | --------- |
| Critical blockers          | Blocks users | Any           | DO NOW    |
| High impact, low effort    | Significant  | < 1 hour      | QUICK WIN |
| High impact, medium effort | Significant  | 1-4 hours     | PRIORITY  |
| Medium impact, low effort  | Moderate     | < 30 min      | BATCH     |
| Low impact                 | Minor        | Any           | LATER     |

## Phase 2: Prioritize & Plan

### The 80/20 Redesign Matrix

Focus on changes that give the most visual/functional improvement:

**High Impact, Low Effort (Do First)**:

- Fix typography hierarchy (change font sizes/weights)
- Remove anti-patterns (purple gradients, centered layouts)
- Improve spacing rhythm (consistent gaps/padding)
- Add proper empty/loading/error states
- Fix broken responsive behavior
- Improve button/link affordance

**High Impact, Medium Effort (Do Second)**:

- Replace generic fonts with distinctive ones
- Restructure layouts (symmetric → asymmetric)
- Add meaningful micro-interactions
- Improve color palette cohesion
- Fix accessibility issues

**Medium Impact (Do If Time Permits)**:

- Add subtle animations/transitions
- Improve copy/microcopy
- Add hover/focus states
- Refine component details

**Low Priority (Consider Skipping)**:

- Nice-to-have visual polish
- Edge case handling
- Performance optimizations (unless critical)

### Create the Fix Plan

Document your plan before executing:

```
## Redesign Plan: [Component/Page Name]

### Context
- Files affected: [list]
- Dependencies: [list]
- Tests to run: [list]

### Anti-Pattern Score: X/10
[List specific anti-patterns found]

### Priority Fixes (in order)
1. [Fix 1] - Why: [reason] - Effort: [estimate]
2. [Fix 2] - Why: [reason] - Effort: [estimate]
3. [Fix 3] - Why: [reason] - Effort: [estimate]

### Quick Wins (batch these)
- [ ] [Win 1]
- [ ] [Win 2]

### Deferred
- [Item] - Reason: [why deferring]
```

## Phase 3: Execute Fixes

### Order of Operations

Apply fixes in this order to minimize rework:

1. **Structure first** - Fix layout, hierarchy, information architecture
2. **Then styling** - Colors, typography, spacing, shadows
3. **Then interaction** - States, hover, focus, animations
4. **Then polish** - Details, edge cases, refinements

### Fix Application Guidelines

**Typography Fixes**:

- Establish clear hierarchy: H1 → H2 → body → small
- Use 1.25 scale ratio minimum between levels
- Ensure body text is 16px minimum, comfortable line-height (1.5-1.7)
- Replace Inter with: Geist, Satoshi, Outfit, or Cabinet Grotesk

**Layout Fixes**:

- Replace centered layouts with asymmetric alternatives
- Use CSS Grid instead of flexbox percentage math
- Ensure responsive collapse to single column on mobile
- Add intentional whitespace, not just "leftover" space

**Color Fixes**:

- Remove AI palette (purple/blue neon)
- Establish neutral base (zinc/slate, not pure gray)
- Pick ONE accent color, desaturated (< 80%)
- Ensure 4.5:1 contrast ratio minimum

**State Fixes**:

- Every interactive element needs: default, hover, focus, active, disabled
- Empty states should guide users to action
- Loading states should reduce perceived wait
- Error states should help users recover

**Anti-Pattern Removal**:

- Remove gradient text (use solid colors with proper hierarchy)
- Remove generic 3-column card grids (use asymmetric layouts)
- Remove glassmorphism (use solid surfaces with subtle shadows)
- Remove generic placeholder content (use realistic data)

## Phase 4: Verify & Document

### After Each Fix

1. **Visual check**: Does it look better? Worse? Unexpected?
2. **Functional check**: Does everything still work?
3. **Responsive check**: Does it work on mobile/tablet?
4. **Run tests**: `npm test` or relevant test command

### Redesign Summary

After completing fixes, document:

```markdown
## Redesign Summary: [Component/Page Name]

### Changes Made

1. [Change 1] - Impact: [high/medium/low]
2. [Change 2] - Impact: [high/medium/low]

### Anti-Pattern Score: Before X/10 → After Y/10

### What Was Preserved

- [Element 1] - Reason: already working well
- [Element 2] - Reason: [why not changed]

### Known Limitations

- [Limitation 1] - Reason: [why not addressed]
- [Limitation 2] - Reason: [why not addressed]

### Future Improvements

- [Improvement 1] - Would require: [effort/dependency]
```

## Redesign Checklist

Before considering the redesign complete:

### Anti-Patterns Eliminated

- [ ] No AI color palette (purple/blue neon gradients)
- [ ] No generic fonts (Inter replaced or intentional)
- [ ] No gradient text on headings
- [ ] No centered hero sections (unless intentional)
- [ ] No identical 3-column card grids
- [ ] No purposeless glassmorphism
- [ ] No dark mode with glowing accents
- [ ] No generic placeholder content
- [ ] Proper empty/loading/error states exist
- [ ] Accent colors desaturated appropriately

### Quality Checks

- [ ] Typography hierarchy is clear (3+ distinct levels)
- [ ] Layout is asymmetric or intentionally symmetric
- [ ] Color palette is cohesive (1 accent, neutral base)
- [ ] Interactive elements have all states
- [ ] Responsive design works on mobile
- [ ] Accessibility basics covered (contrast, focus, labels)
- [ ] Tests pass

### Documentation

- [ ] Changes documented
- [ ] Future improvements noted
- [ ] Limitations acknowledged

## Common Redesign Scenarios

### "This looks AI-generated"

Focus on: Typography replacement, color palette fix, layout restructuring
Commands: `/normalize`, `/colorize`, `/distill`

### "This feels flat/boring"

Focus on: Adding depth via shadows, micro-interactions, motion
Commands: `/animate`, `/delight`, `/bolder`

### "This is cluttered/overwhelming"

Focus on: Removing elements, improving hierarchy, adding whitespace
Commands: `/distill`, `/polish`

### "This is hard to use"

Focus on: Interaction states, affordance, feedback, error handling
Commands: `/harden`, `/clarify`, `/onboard`

### "This doesn't work on mobile"

Focus on: Responsive breakpoints, touch targets, layout collapse
Commands: `/adapt`

## What NOT to Do

- **Don't rebuild from scratch** unless anti-pattern score is 8+ AND stakeholder approves
- **Don't change working functionality** - preserve behavior
- **Don't ignore tests** - run them after changes
- **Don't skip the audit** - understand before changing
- **Don't fix low-priority items first** - stick to the matrix
- **Don't make changes you can't ship independently** - keep increments small
- **Don't forget to document** - future you needs to know what happened

Remember: The best redesign is invisible—users shouldn't notice things changed, just that everything feels better.
