# Simulation Management System Plan (Folder-Centric)

Last updated: February 25, 2026

## 1. Purpose

Define a complete, implementation-ready plan for improving simulation management with:

- User-selectable output folder.
- Settings-driven automatic exports.
- Settings-driven export file selection.
- Folder loading to show completed tasks.
- Task ratings (1-5 stars) with filtering and sorting.
- Additional quality-of-life mechanisms for managing larger simulation histories.

This plan does not change runtime code. It is a specification.

## 2. Product Decisions (Locked)

These decisions are fixed and should be treated as requirements:

- Folder workflows are prioritized, with graceful fallback where folder APIs are unsupported.
- Automatic export is based on settings-selected formats only.
- Task-level `Export` action exports the full configured bundle for that task.
- Completed task viewer behavior:
  - If a folder is loaded: show folder tasks only.
  - If no folder is loaded: backend jobs are the default source.
- Export organization is one subfolder per task (named from output name).
- Ratings are persisted in folder index/manifest in folder mode.
- Auto-export format scope is fully user-controlled in settings (including CAD formats).

## 3. Current State Summary

### Frontend capabilities already present

- Simulation jobs are tracked with polling and local cache.
- Backend jobs are listed and can be viewed/exported manually.
- Output folder selection exists (`Choose Folder`) via file system APIs.
- Some project exports already use shared `saveFile(...)` file operation path.
- Simulation result exports mostly still use direct download links (not folder-routed).

### Backend capabilities already present

- Async job lifecycle endpoints are implemented.
- Job metadata persisted in SQLite with status/progress/stage.
- Results and mesh artifact persistence are implemented.
- Cleanup mechanisms exist (`clear failed`, terminal pruning).

### Gap summary

- No unified simulation export-bundle pipeline.
- No folder task index/manifests.
- No folder-load task viewer mode.
- No ratings field or rating UI.
- No settings panel for auto-export and export format selection.

## 4. Scope

### In scope

- Frontend simulation management architecture and UI behavior.
- Folder index/manifest model.
- Export bundle orchestration.
- Rating/filter/sort model.
- Fallback behavior for unsupported folder APIs.
- Documentation and tests for above.

### Out of scope (v1)

- Mandatory backend API/schema changes.
- Cross-device rating sync beyond folder artifacts.
- Migration of old folder structures from prior versions (only tolerant read/rebuild).

## 5. Architecture Overview

### Core concept

Treat the selected output folder as a task workspace.

Each completed/exported task is represented by:

- A task subfolder containing exported files.
- A task manifest (`task.manifest.json`) containing metadata, rating, export list, and script compatibility fields.
- A root folder index (`.waveguide-tasks.index.v1.json`) for fast list loading/filtering.

### Data sources for viewer

- Folder loaded:
  - source = folder index/manifests only.
- Folder not loaded:
  - source = backend jobs (current behavior), optionally enriched by local overlays.

### Export orchestration model

- Existing format generators remain.
- A new bundle coordinator executes selected formats for a target task.
- All writes route through file operations abstraction.
- One operation can write multiple files in one task subfolder.

## 6. Data Model Specification

### 6.1 Simulation management settings (frontend persisted)

```json
{
  "version": 1,
  "autoExportOnComplete": true,
  "selectedFormats": ["png", "csv", "json", "txt", "polar_csv", "impedance_csv", "vacs", "stl", "fusion_csv"],
  "defaultSort": "completed_desc",
  "minRatingFilter": 0
}
```

### Notes

- `selectedFormats` is authoritative for auto-export and default task export.
- IDs can map internally to existing export handlers (`1..9`) but should use stable string keys in new settings schema.

### 6.2 Task manifest (per task folder)

`task.manifest.json`

```json
{
  "schemaVersion": 1,
  "taskId": "task_2026-02-25T18-22-11Z_abc123",
  "jobId": "backend-job-id-or-null",
  "label": "horn_design_14",
  "status": "complete",
  "createdAt": "2026-02-25T18:20:01.000Z",
  "completedAt": "2026-02-25T18:22:11.000Z",
  "rating": 4,
  "source": "folder",
  "exportedFiles": [
    { "name": "bem_results_1740500000.csv", "format": "csv", "createdAt": "2026-02-25T18:22:12.000Z" }
  ],
  "scriptSnapshot": {},
  "scriptSchemaVersion": 1,
  "notes": ""
}
```

### 6.3 Folder index (root of selected folder)

`.waveguide-tasks.index.v1.json`

```json
{
  "version": 1,
  "updatedAt": "2026-02-25T18:22:15.000Z",
  "tasks": [
    {
      "taskId": "task_2026-02-25T18-22-11Z_abc123",
      "label": "horn_design_14",
      "status": "complete",
      "completedAt": "2026-02-25T18:22:11.000Z",
      "rating": 4,
      "taskFolder": "horn_design_14"
    }
  ]
}
```

## 7. UX Specification

### 7.1 Settings panel additions

Add simulation management section with:

- `Auto-export when simulation completes` toggle.
- `Export formats` checklist.
- `Default sort` selector.
- `Minimum rating` filter selector.
- `Loaded folder` indicator and `Load Folder` action.
- `Use backend default source` info when no folder loaded.

### 7.2 Completed task viewer additions

Task row shows:

- Label.
- Status and duration.
- Timestamp.
- Rating stars (editable 1-5).
- Source badge.
- Actions:
  - `View`
  - `Export` (bundle export)
  - `Script` (if snapshot present)
  - `Redo` (if applicable)
  - `Remove`

Viewer controls:

- Sort by:
  - completion date desc
  - rating desc
  - label asc
- Filter by minimum rating.
- Optional search by label.

### 7.3 Export behavior

- Clicking `Export` on a completed task:
  - Exports all settings-selected formats for that task.
  - Writes into that task subfolder.
- On completion with auto-export on:
  - Same bundle export runs once automatically.
- CAD formats:
  - Included only if selected in settings.
  - If context unavailable, show non-fatal per-format warning and continue remaining formats.

## 8. File and Folder Behavior

### 8.1 Task folder naming

- Base name from output name/counter.
- Ensure uniqueness by appending short suffix if needed.

### 8.2 Write path

- Preferred:
  - selected output folder handle.
- Fallback:
  - save-file picker or browser download when folder APIs are unavailable or permission fails.

### 8.3 Folder loading

- User chooses folder.
- App attempts to read index.
- If index absent/corrupt:
  - scan subfolders for `task.manifest.json`.
  - rebuild in memory.
- Viewer switches to folder-only mode after successful load.

## 9. Compatibility and Safety

### 9.1 Script snapshot compatibility hardening

- Save `scriptSchemaVersion` on every new task manifest.
- On load mismatch:
  - show warning in task row:
    - "Script saved with an older schema - some fields may not apply."
- No migration logic required in v1.

### 9.2 Idempotency

- Auto-export must not run repeatedly for the same completion event.
- Track export-complete marker per task in runtime state/index.

### 9.3 Error handling

- Export bundle uses partial-failure model:
  - successful formats are written.
  - failed formats are reported in toast/log and manifest notes.
- Folder permission loss:
  - degrade to fallback export path.
  - preserve task metadata update where possible.

## 10. Relation to FUTURE_ADDITIONS.md

This plan directly maps to:

- Simulation management enhancements:
  - richer filtering/grouping
  - labels/traceability
  - cleanup-ready metadata
- Script snapshot compatibility hardening:
  - schemaVersion + load warnings

Additional suggested next steps after v1:

- Retention policies configurable from UI (age/count).
- Annotations/notes first-class in task metadata.
- Compare two tasks view (including rating-aware sorting and diff focus).

## 11. Testing Plan

### 11.1 Frontend tests to add/update

- `tests/simulation-flow.test.js`
  - auto-export once-on-complete.
  - bundle export from task action.
- `tests/ui-behavior.test.js`
  - settings persistence.
  - sort/filter/rating behavior.
- New `tests/simulation-folder-index.test.js`
  - index load.
  - manifest scan fallback.
  - schema warning behavior.
- New `tests/fileops-folder-fallback.test.js`
  - unsupported API fallback path.
  - permission failure fallback path.

### 11.2 Backend tests

- No required new backend tests for v1 unless APIs are extended.

## 12. Acceptance Criteria

All must be true:

- User can select and load an output folder.
- Completed simulations can auto-export selected formats into task subfolders.
- Task `Export` writes full selected bundle.
- Viewer shows folder tasks only when folder loaded.
- Viewer falls back to backend jobs when no folder loaded.
- Ratings are editable and persist in folder artifacts.
- Filtering/sorting by rating works.
- Unsupported folder API paths degrade gracefully without breaking export.
- Documentation is updated to reflect actual runtime behavior.

## 13. Non-Goals and Risks

### Non-goals

- Universal identical folder behavior across all browsers.
- Mandatory server-side rating storage in v1.

### Risks

- Browser API variability for folder access and permissions.
- CAD auto-export context mismatches for some tasks.
- Index/manifest consistency under interrupted writes.

### Mitigations

- Fallback export path always available.
- Partial-failure export model.
- Index rebuild from manifests.
- Versioned schemas with tolerant readers.

## 14. Proposed Implementation Order

1. Add settings model and UI controls.
2. Build export bundle coordinator.
3. Implement folder index + task manifest write/read.
4. Wire auto-export completion flow.
5. Add viewer source switching (folder-only/backend-default).
6. Add ratings + filter/sort.
7. Add schema version warnings for scripts.
8. Update docs and run targeted/full tests.
