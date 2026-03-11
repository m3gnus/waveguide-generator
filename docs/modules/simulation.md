# Simulation Contract

## Scope

Primary files:

- `src/modules/simulation/index.js`
- `src/modules/simulation/useCases.js`
- `src/ui/simulation/controller.js`
- `src/ui/simulation/polling.js`
- `src/ui/workspace/taskManifest.js`
- `src/ui/workspace/taskIndex.js`

## Responsibilities

- Prepare canonical simulation payloads and OCC adaptive submit options.
- Submit jobs through the backend solver client.
- Track backend jobs and folder-backed task history.
- Persist task metadata such as exports, ratings, and script snapshots.

## Runtime Contract

- Real simulation requires the backend `/api/solve` path.
- Frontend restore logic chooses one source mode at a time:
  - folder workspace selected -> folder manifests/index only
  - no folder workspace -> backend jobs plus local cache
- Task export metadata persists through:
  - `exportedFiles`
  - `autoExportCompletedAt`
- Task rating metadata persists through:
  - local job storage
  - folder task manifests/index when a workspace is active

## UI Preference Contract

Persisted simulation-management settings include:

- `autoExportOnComplete`
- `selectedFormats`
- `defaultSort`
- `minRatingFilter`

These settings drive completed-task export bundles and task-list ordering/filtering.

## Regression Coverage

- `tests/simulation-controller.test.js`
- `tests/simulation-flow.test.js`
- `tests/simulation-job-tracker.test.js`
- `tests/simulation-module.test.js`
- `tests/simulation-export-bundle.test.js`
- `tests/simulation-management-settings.test.js`
- `tests/task-manifest.test.js`
- `tests/task-index-rebuild.test.js`
