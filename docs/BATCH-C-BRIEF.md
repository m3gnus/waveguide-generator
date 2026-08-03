# Phase 1, Batch C — contract mining: exports, results, traceability

Mine the v1 app (read-only, at `../Waveguide Generator` — note the space) and write three contract documents in THIS repo.

**Path discipline (other agents work in this repo concurrently): you may create/modify ONLY `docs/EXPORT-CONTRACTS.md`, `docs/RESULT-CONTRACTS.md`, `docs/TRACEABILITY-TABLE.md`. Nothing else.**

Every claim cites `file:line` from the v1 tree. Mark anything you could not verify as OPEN with what you'd need. These documents become binding contracts for later implementation batches — precision beats prose.

## 1. docs/EXPORT-CONTRACTS.md

One section per export path, covering: source geometry (which tier/state), included/excluded bodies, units, coordinate system/axis transforms, winding/orientation, density/smoothing, physical tags/names, filename conventions, and which design state it reads (job snapshot vs live editor). Known starting points to verify and expand:
- STL: browser-side today — `src/modules/export/index.js` (~line 358: densification, enclosure/wall/source removal, axis rotation) + `src/export/stl.browser.js` (serialization, normals).
- STEP: `server/api/routes_mesh.py` (~line 107: forces all four quadrants, inner acoustic surface, no enclosure/wall).
- Profile/curve exports: `src/export/profiles.js` + `/api/export-file` route (formats, columns, units).
- `.msh` artifact retrieval + parsing scope: `server/api/routes_simulation.py` (~line 230, original text passthrough) + `src/import/mshParser.js` (ASCII 2.2 only, physical names).
- Job export bookkeeping: `src/ui/simulation/exports.js` (~line 72 snapshot rule; export prefix/counter persistence), auto-export-once + mesh auto-download semantics in `src/ui/settings/simulationManagementSettings.js`.
- Workspace manifest + deterministic folders: `docs/modules/export.md` (~line 44, `waveguide.project.v1.json`).
End with a "v2 decisions required" list (e.g. STL source tier server-side; designRevision binding) — questions, not answers.

## 2. docs/RESULT-CONTRACTS.md

The numerical/failure-state contract matrix for every result quantity (FR/SPL, phase, impedance, DI, directivity maps, polars, beamwidth, balloon, beam-shape, solver log): units, normalization, phase/time convention (note: metal-bem is e^{-iωt}), observation origin, plane availability H/V/D, frequency alignment, NaN/missing-frequency policy, partial-success display, warnings. Known starting points:
- `server/solver/result_mapping.py` (~line 357: balloon four-state contract disabled/requested/unsupported/available).
- `src/results/smoothing.js` (line 1 on: enumerate ALL nine smoothing modes incl. psychoacoustic/ERB with their parameters and reference behavior).
- `src/ui/simulation/exports.js` (~line 333: phase + impedance conventions preserved on export).
- `src/ui/simulation/results.js` (~line 262: partial success + per-frequency failures).
- `server/contracts/` package (module contracts) and `server/solver/beam_shape.py` (DI weighting, ray sampling).
Present as tables where possible. End with "v2 decisions required" (e.g. where smoothing runs in v2, golden-fixture list to freeze).

## 3. docs/TRACEABILITY-TABLE.md

Seed the plan §3 traceability table (see `../WG-REBUILD-PLAN.md` §3 for the intended columns: v1 behavior/route/control · v2 owner · phase · test · compat status · deferral note). Rows from: the full §3 seed list, every HTTP route in `server/api/*.py`, every `package.json` script (diag/doctor/preflight workflows), the settings surfaces under `src/ui/settings/`, and viewer display/camera modes in `src/viewer/index.js`. Owner/phase columns: fill from the plan where obvious, else `TBD`. This is a living inventory — completeness of ROWS is the goal; hundreds of rows are expected and fine.

## Rules

- Read-only outside this repo. Bounded exploration: the named files first; follow imports only where a contract genuinely lives elsewhere.
- Final message: the three files' section/row counts, the 5 most surprising findings, and your consolidated "v2 decisions required" list.
