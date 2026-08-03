# Phase 2/5, Batch P — the COMPLETE parameter panel (all ATH params + all WG inputs)

Owner requirement (Magnus, verbatim intent): "Make sure all the ath parameters are visible in the parameters window as well as all the WG inputs." The current panel shows a curated R-OSSE subset — replace it with the full inventory, well-organized.

**Path discipline (concurrent agents in frontend/): create/modify ONLY `frontend/src/design/**`, `frontend/src/stores/design.ts`, and `frontend/src/shell/ParamPanel-hosting component if separate`. Do NOT touch src/jobs, src/results, src/viewport, src/api (import-only).**

## Ground truth for the inventory

1. `docs/TRACEABILITY-TABLE.md` — the 110 parameter keys inventoried from v1 (the authoritative list; every row of the parameter section must appear in the panel or be explicitly listed by you as deliberately-deferred with reason).
2. `../Waveguide Generator/src/ui/parameterInventory.js` + `paramPanel.js` — v1's grouping, labels, units, ranges, conditional visibility (which fields show per family/mode).
3. `server/design/schema.py` — the v2 schema (batch A): field names, types, validators. THE STORE MUST MATCH THE SCHEMA — if a v1 parameter is missing from the schema, note it in your final message (schema gaps are a server-side follow-up, do not hack the schema yourself).
4. ATH-side params (the `.cfg` vocabulary): mesh/sampling controls (angular/length segments, Z-map modes, resolution, max-edge guards), source (shape, contours, velocity convention, radius/curvature), quadrants, morph, rollback, coverage, enclosure block, ABEC/Report passthrough blocks get a read-only "passthrough blocks present" indicator (NOT an editor — they are preserved verbatim by design).

## Deliverables

1. Full sectioned panel: Profile (per family: OSSE, R-OSSE complete field sets; ICW and FREEFORM sections with their COMPLETE scalar params — FREEFORM's spline-point tables get a structured read-only table view + "editor arrives in a later phase" note), Source (all fields incl. velocity convention + contours where schema carries them), Symmetry/Quadrants, Geometry extras (morph, rollback, coverage, extensions), Enclosure (full block), Mesh & Sampling (all knobs incl. Z-map mode + max-edge with the λ/6 hint pattern), Simulation (freq range, count, spacing, spherical sampling toggle DEFAULT OFF per plan §3), Passthrough indicator.
2. Conditional visibility mirroring v1 semantics (fields irrelevant to the active family/mode hidden or disabled-with-reason, per parameterInventory.js).
3. Search/filter box at the panel top (the inventory is long; type-to-filter across labels and keys — cheap and massively useful).
4. Every field: label, unit, drag-adjust where numeric, validation from the schema (client-side pattern per batch G's NumberField), commit → designRevision bump. Collapsible sections with persisted open/closed state.
5. Store: extend the design store to carry the full DesignConfig shape (typed from the schema-generated types if present; else mirror schema.py faithfully) with per-family defaults; keep undo/grouped-drag semantics intact.
6. Tests: inventory completeness test (panel field registry ⊇ traceability parameter keys minus your documented deferrals — encode the list in the test so drift fails), conditional visibility per family, filter behavior, store round-trip of a fully-populated design.
7. `npm run build` + `npx vitest run` green; the preview must still update live when any geometry-affecting field changes.

## Rules
- Organize for daily use (the sketch's hierarchy), not as a flat dump; but completeness beats beauty this batch.
- Final message: field count per section, the deferred list with reasons, schema gaps found, test counts.
