# Phase 3/5, Batch R — exports + `.cfg` design file I/O

Implement the export endpoints per `docs/EXPORT-CONTRACTS.md` (binding — batch C mined it from v1 with file:line citations; read it fully) and the design open/save/import surface per plan §6.1.

**Path discipline: create/modify ONLY `server/exports/**`, `server/design_io/**`, `server/tests/test_exports_*.py`, `server/tests/test_design_io_*.py`, mount lines in `server/app.py`, plus EXACTLY these frontend files: `frontend/src/api/designIo.ts`, `frontend/src/design/DesignFileMenu.tsx`, and the file-menu mount in `frontend/src/shell/TopBar.tsx` (mount + menu wiring only — batch S owns all other shell polish; do not touch viewport/, jobs/, results/, stores beyond imports).**

## Server deliverables

1. `server/design_io/` — REST: `POST /api/design/save` (DesignConfig JSON → `.cfg` text via batch A's serializer; returns text + suggested filename), `POST /api/design/open` (text body → parse+migrate → DesignConfig JSON + dialect/migration report; accepts `.cfg`/`.txt`/legacy `.mwg` content), `POST /api/design/import-report` (dry-run classification of a text payload). Round-trip law tests against the batch-A corpus fixtures (reuse, don't duplicate).
2. `server/exports/` per the contracts doc:
   - STEP: full-domain inner acoustic surface via the mesher (v1 semantics: forces all quadrants, no enclosure/wall) — `POST /api/export/step`.
   - STL: **server-side now** (plan §6.3): from the solver-mesh build at v1's densified display semantics (densify angular, horn surface only, `(x,−z,y)` axes per the contract), binary STL — `POST /api/export/stl`.
   - Profile/curve CSV exports per contract (axes `(x,z,y)`, no verticalOffset) — `POST /api/export/profiles`.
   - Each response carries the designRevision it was built from (client passes it; server echoes) + correct content-type/filename headers.
3. Tests: contract fixtures (axes transforms verified numerically on small meshes; STEP text contains geometry; STL binary header/facet counts; profile CSV columns), design round-trip semantic equality, legacy `.mwg` open path, error cases (invalid text → 422 with parse detail).

## Frontend deliverables

4. `DesignFileMenu` on the filename chip in the top bar: Open (file picker accepting .cfg/.txt/.mwg → POST open → hydrate the store via its public hydrate/load API — check `stores/design.ts` exports; if no hydrate exists, add ONLY a `loadDesignDocument(doc)` export there as a narrowly-scoped exception to path discipline), Save (.cfg download via save endpoint, filename from chip), Import report shown as a toast/dialog summary (dialect, migrations applied, passthrough blocks preserved). Export submenu: STEP / STL / Profiles CSV downloads with the current designRevision.
5. Unsaved-dot wiring: clears on save, sets on revision change after last save.

## Rules
- No new deps. Downloads via blob URLs; no external anything.
- Self-verify: server suite + vitest + build green. Final message: endpoints, per-export contract compliance notes (cite the contract lines you implemented), test counts, the overseer's live checklist (open a real legacy .mwg from the v1 output corpus! save a .cfg; export all three).
