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
- `src/ui/simulation/workspaceTasks.js` — backend workspace manifest and artifact writes
- `src/ui/workspace/taskManifest.js` — generation manifest construction

## Core Responsibilities

- **Payload preparation**: Build canonical simulation payloads and HornLab mesher submit options
- **Job submission**: Route jobs to backend `/api/solve` with correct request shape
- **Result handling**: Poll backend, fetch results, extract runtime metadata (performance, observation/directivity settings, failures)
- **History management**: Track backend jobs and cache fetched results
- **Metadata persistence**: Save task ratings, export status, auto-export markers, and script snapshots

## Runtime Contract

**Simulation execution**:
- Real simulation requires backend `/api/solve` path; no mock/fallback solver supported
- Payload submission includes a minimal source-tagged contract mesh plus required HornLab mesher parameters
- `solver_backend` supports `auto`, `bempp`, and `metal`; `auto` prefers a ready Metal backend when available, otherwise BEMPP
- Active BEM solves use HornLab mesher parameters; `quadrants` may reduce the
  solve/export mesh domain when manually selected or auto-resolved. Bempp and
  Metal both support transverse half/quarter symmetry for free-standing rigid
  Neumann models; unsupported coupled-IB/Robin symmetry requests fail
  explicitly.
- The default hard mesh ceiling is 50,000 full-domain-equivalent triangles. Realized meshes above 18,000 are allowed and publish a soft performance warning in the live UI and persisted mesh diagnostics.
- `solver_mode="auto"` is the default; on the Metal backend it selects CircSym automatically for eligible circular waveguides, including circular infinite-baffle jobs, otherwise it uses full 3D. `solver_mode="circsym"` selects the Metal-only axisymmetric meridian solver. Use it for round, circular waveguides when sweep speed matters. Use `solver_mode="full_3d"` for non-round or morphed geometry, enclosure models, forced full-surface parity, and any infinite-baffle job that is not CircSym-eligible.

**Results handling**:
- Pre-submit geometry diagnostics report face triangle counts (not just numeric tags)
- Backend result summaries read the reduced `metadata.performance` payload (`total_time_seconds`, `bem_precision`)
- Backend solve metadata includes `metadata.observation` and `metadata.directivity` for effective observation distance and persisted directivity-map settings
- Metal and Bempp results both publish the canonical spatial propagation
  convention `metadata.phase_time_convention="exp(+ikr)"`, consistent with
  the `e^{-i*omega*t}` time convention. Chart requests preserve canonical
  `exp(+ikr)` / `exp(-ikr)` values; backend names are accepted only as legacy
  ingestion aliases and are never emitted as phase conventions.
- View Results re-renders the directivity heatmap through `/api/render-directivity` for display-only reference-level changes without requesting a new solve

**History & workspace**:
- Backend jobs and local cache provide task history; workspace manifests persist export metadata and generation artifacts.
- Completed-task bundles write through the backend workspace root to `<workspace>/<jobLabel>/`.
- If a backend workspace write fails, the app falls back to browser download/save.

**Task metadata persistence**:
- Ratings: mirrored to backend job metadata and workspace manifests
- Exports: tracked via `exportedFiles` list and `autoExportCompletedAt` timestamp
- Script snapshots: stored with task manifest and mirrored to deterministic generation artifact `script.snapshot.mwg`
- Completion artifacts: first completion pass persists deterministic runtime snapshots in generation folder:
  - raw results: `<jobLabel>_raw.results.json` (from `/api/results/{jobId}`)
  - mesh artifact mirror: `<jobLabel>_solver.mesh.msh` (from `/api/mesh-artifact/{jobId}` when available)
- User-facing project manifest: `waveguide.project.v1.json` in each generation folder records script snapshots, selected exports, raw-results snapshot, and mesh-artifact metadata
- Manifest folders use generation naming (`<workspace>/<jobLabel>/`), while manifest `id` remains the stable backend job identity

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
- `tests/task-manifest.test.js` — folder workspace persistence
- `tests/generation-artifacts.test.js` — generation project manifest and deterministic artifact naming
