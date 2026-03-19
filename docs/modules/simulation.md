# Simulation Contract

## Scope

**Core module files**:
- `src/modules/simulation/index.js` — public module interface
- `src/modules/simulation/domain.js` — pure simulation logic (payload building, job submission)
- `src/modules/simulation/state.js` — isolated `GlobalState` bridge
- `src/modules/simulation/jobs.js` — job metadata and history
- `src/modules/simulation/useCases.js` — compatibility barrel export

**UI coordination files**:
- `src/ui/simulation/controller.js` — job lifecycle and UI polling
- `src/ui/simulation/workspaceTasks.js` — folder workspace task management
- `src/ui/workspace/taskManifest.js`, `taskIndex.js` — folder-backed task persistence

## Core Responsibilities

- **Payload preparation**: Build canonical simulation payloads and OCC adaptive submit options
- **Job submission**: Route jobs to backend `/api/solve` with correct request shape
- **Result handling**: Poll backend, fetch results, extract runtime metadata (performance, observation distance, failures)
- **History management**: Track backend jobs and folder-workspace task manifests
- **Metadata persistence**: Save task ratings, export status, auto-export markers, and script snapshots

## Runtime Contract

**Simulation execution**:
- Real simulation requires backend `/api/solve` path; no mock/fallback solver supported
- Payload submission includes canonical mesh + optional OCC adaptive parameters
- Active BEM solves run the full-domain mesh; imported quadrants remain metadata only

**Results handling**:
- Pre-submit geometry diagnostics report face triangle counts (not just numeric tags)
- Backend performance metadata included in results under `metadata.performance`

**History & source selection**:
- **One source mode at a time**: either folder workspace (manifests only) OR backend jobs + local cache (never mixed)
- Folder workspace: completed-task bundles write to `<workspace>/<jobLabel>/`; when File System Access is unavailable, exports use backend workspace root + `workspace_subdir`
- If task-folder/direct writes fail, app falls back through backend workspace writes and finally browser download/save

**Task metadata persistence**:
- Ratings: stored locally and in folder task manifests when workspace active
- Exports: tracked via `exportedFiles` list and `autoExportCompletedAt` timestamp
- Script snapshots: stored with task manifest and mirrored to deterministic generation artifact `script.snapshot.mwg`
- User-facing project manifest: `waveguide.project.v1.json` in each generation folder records selected exports and script-snapshot artifact metadata
- Manifest/index folders use generation naming (`<workspace>/<jobLabel>/`) when available, but manifest/index `id` remains the stable backend job identity

**Settings** (persisted):
- `autoExportOnComplete` — auto-run exports on job completion
- `selectedFormats` — export bundle format selection
- `defaultSort` — task-list ordering (date, name, rating)
- `minRatingFilter` — minimum star rating to display

## Test Coverage

Contract validation tests:
- `tests/simulation-module.test.js` — module interface
- `tests/simulation-controller.test.js` — job lifecycle and polling
- `tests/simulation-flow.test.js` — end-to-end submission/polling/results
- `tests/simulation-job-tracker.test.js` — job state management
- `tests/simulation-export-bundle.test.js` — bundle coordination
- `tests/simulation-management-settings.test.js` — settings persistence
- `tests/task-manifest.test.js`, `task-index-rebuild.test.js` — folder workspace persistence
- `tests/generation-artifacts.test.js` — generation project manifest and deterministic artifact naming
