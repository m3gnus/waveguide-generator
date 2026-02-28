---
phase: 02-viewer-controls-persistence-and-reset-ux
plan: 02
subsystem: ui
tags: [OrbitControls, localStorage, viewer-settings, three.js, camera]

# Dependency graph
requires:
  - phase: 02-01
    provides: loadViewerSettings, applyViewerSettingsToControls, setInvertWheelZoom, getCurrentViewerSettings from viewerSettings.js
provides:
  - scene.js reads persisted viewer settings at startup (setupScene)
  - scene.js re-applies viewer settings after camera toggle (toggleCamera)
  - OrbitControls speeds, damping, keyboard pan, and wheel inversion driven by viewerSettings.js — nothing hardcoded
affects:
  - 02-03 (modal.js integration — scene.js wiring complete, settings will take effect immediately when saved)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "loadViewerSettings() called once at startup; result stored in local const for use in setupScene"
    - "getCurrentViewerSettings() called in toggleCamera to re-apply without re-reading localStorage"
    - "setInvertWheelZoom called at every OrbitControls construction point — prevents accumulation"

key-files:
  created: []
  modified:
    - src/app/scene.js

key-decisions:
  - "Camera creation in setupScene is now conditional on startupCameraMode — orthographic path uses getOrthoSize() consistent with toggleCamera"
  - "applyViewerSettingsToControls replaces all hardcoded OrbitControls property assignments — single call covers speeds, damping, and keyboard pan"

patterns-established:
  - "Every OrbitControls construction point calls applyViewerSettingsToControls + setInvertWheelZoom — pattern must be maintained if new construction points are added"

requirements-completed:
  - VIEW-01
  - VIEW-02
  - VIEW-03
  - VIEW-04
  - VIEW-05

# Metrics
duration: 3min
completed: 2026-02-28
---

# Phase 2 Plan 02: Wire viewerSettings into scene.js Summary

**scene.js startup and camera toggle now driven by persisted OrbitControls speeds, damping, keyboard pan, wheel inversion, and startupCameraMode from viewerSettings.js**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-28T16:53:53Z
- **Completed:** 2026-02-28T16:56:30Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- Added import of four functions from viewerSettings.js into scene.js
- setupScene reads loadViewerSettings() and uses startupCameraMode for conditional camera creation (perspective vs orthographic)
- setupScene replaces hardcoded `enableDamping = true` with applyViewerSettingsToControls + setInvertWheelZoom
- toggleCamera replaces hardcoded `enableDamping = true` with getCurrentViewerSettings() + applyViewerSettingsToControls + setInvertWheelZoom
- All 117 tests pass — no regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Apply viewer settings in setupScene and toggleCamera** - `d162527` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified
- `src/app/scene.js` - Imports viewerSettings.js functions; setupScene applies persisted settings at startup; toggleCamera re-applies after OrbitControls recreation

## Decisions Made
- Camera creation in setupScene is conditional on `startupCameraMode` using the same `getOrthoSize()` helper already used by toggleCamera, keeping orthographic size consistent
- No changes to any other logic in scene.js — onResize, renderModel, focusOnModel, zoom, animate, calculateCurvatureColors unchanged

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- scene.js wiring is complete — viewer settings now take effect at every OrbitControls construction point
- Plan 02-03 (modal.js integration) can wire UI controls to saveViewerSettings/debouncedSaveViewerSettings; changes will take effect immediately because scene.js reads them at startup and after toggle

## Self-Check: PASSED

- `src/app/scene.js`: FOUND (modified)
- Commit `d162527`: FOUND

---
*Phase: 02-viewer-controls-persistence-and-reset-ux*
*Completed: 2026-02-28*
