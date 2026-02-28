---
phase: 04-folder-workspace-data-model
plan: 03
subsystem: ui
tags: [index, restore, simulation, workspace]
requires:
  - phase: 04-01
    provides: folder workspace lifecycle
  - phase: 04-02
    provides: per-task manifest persistence
provides:
  - Root task index schema/read-write lifecycle
  - Manifest-scan rebuild path for missing/corrupt index
  - Simulation restore flow integration with auto-repair and re-persist
affects: [simulation, restore, workspace]
tech-stack:
  added: []
  patterns: [index-first restore with tolerant fallback, serialized index writes]
key-files:
  created: [src/ui/workspace/taskIndex.js]
  modified: [src/ui/simulation/SimulationPanel.js, src/ui/simulation/jobTracker.js]
key-decisions:
  - "Restore flow uses index-first semantics and automatically rebuilds from manifests when index is absent/corrupt."
  - "Tracker persistence mirrors local jobs into folder index with serialized writes."
patterns-established:
  - "Folder index is treated as durable root cache while local storage remains compatible fallback."
requirements-completed: [FOLD-02, FOLD-04]
duration: 29 min
completed: 2026-02-28
---

# Phase 04 Plan 03: Root Index Recovery Summary

**Folder mode now maintains a root task index and can rebuild that index from per-task manifests to recover safely from missing or corrupt index state.**

## Performance

- **Duration:** 29 min
- **Started:** 2026-02-28T19:06:00Z
- **Completed:** 2026-02-28T19:35:00Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- Added root index helpers for `.waveguide-tasks.index.v1.json` lifecycle and rebuild diagnostics.
- Integrated index-first restore in `SimulationPanel` with manifest rebuild + auto-repair persistence.
- Updated tracker persistence to mirror job entries into folder index with deterministic write ordering.

## Task Commits

1. **Task 1: Build root index module** - `7c02651` (feat)
2. **Task 2: Integrate index-first restore in SimulationPanel** - `41b2e6b` (feat)
3. **Task 3: Align tracker merge/persist behavior** - `0ffd132` (feat)

## Files Created/Modified
- `src/ui/workspace/taskIndex.js` - Root index load/write/rebuild primitives.
- `src/ui/simulation/SimulationPanel.js` - Restore flow with index-first + manifest repair fallback.
- `src/ui/simulation/jobTracker.js` - Folder index persistence alignment.

## Decisions Made
- Rebuild flow surfaces warning-level feedback and persists repaired index for subsequent restores.
- Keep merge semantics compatible with existing local + backend behavior while adding folder index sources.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Restore/recovery path is in place for robust folder-mode history.
- Ready for dedicated regression coverage in plan 04-04.

---
*Phase: 04-folder-workspace-data-model*
*Completed: 2026-02-28*
