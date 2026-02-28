# Roadmap: Waveguide Generator

## Overview

This milestone delivers a reliable simulation operations layer on top of the existing waveguide app: a unified settings experience, contract-safe simulation control wiring, folder-centric task workspace management, robust export bundles, and scalable completed-task UX with rating/filter/sort behavior.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [x] **Phase 1: Settings Modal Entry + System Migration** - Replace update-check entry behavior with the new Settings shell and system actions. (completed 2026-02-28)
- [ ] **Phase 2: Viewer Controls Persistence + Reset UX** - Implement viewer settings model, apply-on-load behavior, and recommended resets.
- [ ] **Phase 3: Simulation Basic Payload Wiring** - Connect Basic simulation controls to `/api/solve` contract fields.
- [ ] **Phase 4: Folder Workspace Data Model** - Introduce folder index/task manifest model with tolerant rebuild behavior.
- [ ] **Phase 5: Export Bundle + Auto-Export Engine** - Deliver selected-format bundle exports and idempotent auto-export behavior.
- [ ] **Phase 6: Completed Task Source Modes** - Implement folder-only viewer mode with backend-source fallback.
- [ ] **Phase 7: Ratings + Sorting + Filtering** - Add rating persistence and quality-of-life task list controls.
- [ ] **Phase 8: Advanced Controls Gating + Hardening** - Gate advanced/expert controls and finalize regression/docs hardening.

## Phase Details

### Phase 1: Settings Modal Entry + System Migration
**Goal**: Deliver a unified Settings modal entry point and preserve update-check capability under System.
**Depends on**: Nothing (first phase)
**Requirements**: [SET-01, SET-02, SET-03]
**Success Criteria** (what must be TRUE):
  1. User opens Settings from the current update-check button location.
  2. Settings modal visibly contains Viewer, Simulation Basic, Simulation Advanced, and System sections.
  3. System section can trigger update-check flow successfully.
**Plans**: 3 plans

Plans:
- [ ] 01-01: Create settings modal shell and section structure in UI.
- [ ] 01-02: Rewire update-check button behavior and preserve system action path.
- [ ] 01-03: Add integration tests for modal entry and update-check migration.

### Phase 2: Viewer Controls Persistence + Reset UX
**Goal**: Ship stable viewer settings persistence with recommended defaults and reset controls.
**Depends on**: Phase 1
**Requirements**: [SET-04, SET-05, VIEW-01, VIEW-02, VIEW-03, VIEW-04, VIEW-05, VIEW-06]
**Success Criteria** (what must be TRUE):
  1. Viewer settings persist/reload using tolerant schema version handling.
  2. Viewer runtime behavior reflects selected controls after reload.
  3. Reset section/all actions restore recommended values correctly.
**Plans**: 4 plans

Plans:
- [ ] 02-01: Implement versioned settings schema and local persistence service.
- [ ] 02-02: Wire viewer settings into scene/control runtime application.
- [ ] 02-03: Implement recommended badges and reset handlers.
- [ ] 02-04: Add frontend tests for persistence and reset behavior.

### Phase 3: Simulation Basic Payload Wiring
**Goal**: Ensure Simulation Basic controls reliably affect submitted solve requests.
**Depends on**: Phase 2
**Requirements**: [SIMB-01, SIMB-02, SIMB-03, SIMB-04, SIMB-05, SIMB-06]
**Success Criteria** (what must be TRUE):
  1. `/api/solve` requests include selected basic settings fields.
  2. Device recommendation/fallback messaging reflects runtime availability.
  3. Existing solve behavior remains backward-compatible.
**Plans**: 3 plans

Plans:
- [ ] 03-01: Add Simulation Basic settings controls and payload mapping.
- [ ] 03-02: Integrate `/health` runtime-aware recommendation/fallback logic.
- [ ] 03-03: Add contract tests for request field inclusion and fallback behavior.

### Phase 4: Folder Workspace Data Model
**Goal**: Add folder-centric task model with index and per-task manifest lifecycle.
**Depends on**: Phase 3
**Requirements**: [FOLD-01, FOLD-02, FOLD-03, FOLD-04, FOLD-05]
**Success Criteria** (what must be TRUE):
  1. User can load/select folder and app can read/write index metadata.
  2. Task subfolders/manifests are written with required metadata fields.
  3. Index-missing/corrupt flow can rebuild task list from manifests.
**Plans**: 4 plans

Plans:
- [ ] 04-01: Implement folder workspace service and handle lifecycle.
- [ ] 04-02: Implement task manifest writer/reader with schema versioning.
- [ ] 04-03: Implement root index writer/reader and recovery scan logic.
- [ ] 04-04: Add tests for index load/rebuild and schema-warning behavior.

### Phase 5: Export Bundle + Auto-Export Engine
**Goal**: Deliver reliable selected-format bundle export and idempotent auto-export behavior.
**Depends on**: Phase 4
**Requirements**: [EXPO-01, EXPO-02, EXPO-03, EXPO-04, EXPO-05, EXPO-06]
**Success Criteria** (what must be TRUE):
  1. Task export action writes the full selected format bundle.
  2. Auto-export runs once on completion and avoids duplicate runs.
  3. Unsupported folder APIs/permission failures use fallback path without breaking export.
**Plans**: 4 plans

Plans:
- [ ] 05-01: Build export bundle coordinator over existing format generators.
- [ ] 05-02: Wire settings-selected formats and auto-export triggers.
- [ ] 05-03: Add idempotency marker and partial-failure result reporting.
- [ ] 05-04: Add fallback path tests for unsupported/permission-failure scenarios.

### Phase 6: Completed Task Source Modes
**Goal**: Implement clear viewer source switching between folder-mode tasks and backend jobs.
**Depends on**: Phase 5
**Requirements**: [TASK-01, TASK-02]
**Success Criteria** (what must be TRUE):
  1. Folder-loaded mode displays folder tasks only.
  2. No-folder mode displays backend job source by default.
  3. Source mode is clear in UI state.
**Plans**: 3 plans

Plans:
- [ ] 06-01: Add source abstraction for completed-task list data.
- [ ] 06-02: Wire folder-only vs backend-default mode behavior.
- [ ] 06-03: Add tests for source switching and mode-specific rendering.

### Phase 7: Ratings + Sorting + Filtering
**Goal**: Add rating persistence and management controls for larger task histories.
**Depends on**: Phase 6
**Requirements**: [TASK-03, TASK-04, TASK-05]
**Success Criteria** (what must be TRUE):
  1. User can edit ratings and ratings persist in folder artifacts.
  2. Sorting by completion date, rating, and label works consistently.
  3. Minimum-rating filter narrows task list correctly.
**Plans**: 3 plans

Plans:
- [ ] 07-01: Implement task rating UI and manifest/index persistence updates.
- [ ] 07-02: Implement sort/filter controls and stable list application.
- [ ] 07-03: Add frontend tests for rating persistence and sorting/filtering behavior.

### Phase 8: Advanced Controls Gating + Hardening
**Goal**: Stage advanced controls safely and finish regression/documentation hardening.
**Depends on**: Phase 7
**Requirements**: [ADVC-01, ADVC-02, EXPT-01]
**Success Criteria** (what must be TRUE):
  1. Advanced/expert controls remain gated to backend capability/phase readiness.
  2. Regression suites pass for touched frontend and backend surfaces.
  3. Documentation reflects delivered runtime behavior and deferred items clearly.
**Plans**: 3 plans

Plans:
- [ ] 08-01: Add gated advanced/expert placeholders and backend-capability checks.
- [ ] 08-02: Implement/verify backend optional-field validation paths where enabled.
- [ ] 08-03: Update docs/test inventory and run full regression suites.

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Settings Modal Entry + System Migration | 3/3 | Complete   | 2026-02-28 |
| 2. Viewer Controls Persistence + Reset UX | 0/4 | Not started | - |
| 3. Simulation Basic Payload Wiring | 0/3 | Not started | - |
| 4. Folder Workspace Data Model | 0/4 | Not started | - |
| 5. Export Bundle + Auto-Export Engine | 0/4 | Not started | - |
| 6. Completed Task Source Modes | 0/3 | Not started | - |
| 7. Ratings + Sorting + Filtering | 0/3 | Not started | - |
| 8. Advanced Controls Gating + Hardening | 0/3 | Not started | - |
