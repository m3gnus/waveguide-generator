# Simulation Contract

## Scope

Primary files:

- `src/modules/simulation/index.js`
- `src/modules/simulation/domain.js`
- `src/modules/simulation/state.js`
- `src/modules/simulation/jobs.js`
- `src/modules/simulation/useCases.js` (compatibility barrel)
- `src/ui/simulation/controller.js`
- `src/ui/simulation/polling.js`
- `src/ui/simulation/workspaceTasks.js`
- `src/ui/workspace/taskManifest.js`
- `src/ui/workspace/taskIndex.js`

## Responsibilities

- Prepare canonical simulation payloads and OCC adaptive submit options.
- Keep pure simulation-domain helpers separate from state/job facades.
- Keep the `GlobalState` bridge isolated to `src/modules/simulation/state.js`.
- Submit jobs through the backend solver client.
- Track backend jobs and folder-backed task history.
- Persist task metadata such as exports, ratings, and script snapshots.

## Runtime Contract

- Real simulation requires the backend `/api/solve` path.
- Completed-result UI reads backend result metadata directly; the View Results modal surfaces `metadata.symmetry_policy` / `metadata.symmetry` as a read-only symmetry decision summary when present.
- Frontend restore logic chooses one source mode at a time:
  - folder workspace selected -> folder manifests/index only
  - no folder workspace -> backend jobs plus local cache
- Task export metadata persists through:
  - `exportedFiles`
  - `autoExportCompletedAt`
- Task rating metadata persists through:
  - local job storage
  - folder task manifests/index when a workspace is active
- When a folder workspace is active, completed-task export bundles write into `<workspace>/<jobId>/`; if those writes fail, the app clears the workspace selection and falls back to the browser save/download path.

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
