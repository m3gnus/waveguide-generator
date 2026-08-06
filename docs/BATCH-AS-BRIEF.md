# Batch AS — automatic symmetry + demoting CircSym from a backend to a fast path

Repo: `.`, branch `freeform-simplify` (already checked out; there are uncommitted edits in `frontend/` — leave them alone, they are another lane's work).

Python: `"../Waveguide Generator/.venv/bin/python"`. Server tests: `"../Waveguide Generator/.venv/bin/python" -m pytest server/tests -q` (baseline 669 passed / 1 skipped).

**Path discipline — you may only modify:**
- `server/**` (including `server/tests/**`)
- `frontend/src/stores/solveOptions.ts` and its test
- `frontend/src/design/SolveOptionsSections.tsx`
- `frontend/src/jobs/actions.ts` and its test
- this brief's companion doc `docs/SYMMETRY-CONTRACT.md` (new, you write it)

Do NOT touch `frontend/src/shell/**`, `frontend/src/design/ParamPanel.tsx`, `frontend/src/design/parameterRegistry.ts`, `frontend/src/viewport/**`, `frontend/src/prefs/**`, `frontend/src/styles/**` — other lanes own those. Do not run git commands that write; the overseer commits.

Context for both tasks, in the user's words:

> "I really like how the quarter domain and the reduced domain or full domain window is, but I think the default should be auto, which means that the program automatically figures out if the model can be cut into either two pieces or four pieces, and then based on that it can do the fastest solve."

> "Solver backend... there's also another solver called CircSym. It doesn't really make sense to have CircSym as a separate solver. What I wanted with CircSym is that for perfectly circular round horns, CircSym only simulates a small slice of the horn and then rotates that into 3D space. It's supposed to be faster... It shouldn't be a different mode, because the metal-based solver should also then do CircSym solves. It should not be a separate solver."

---

## Task 1 — automatic symmetry resolution

Today `design.mesh.quadrants` is the only symmetry control (ATH's `Mesh.Quadrants`: 1234 full, 12 upper half / xz plane, 14 right half / yz plane, 1 quarter — see `server/solver/quadrants.py`). The user must pick it by hand and can pick one the geometry does not actually have, which silently corrupts the solve.

Add an **`auto` symmetry mode that resolves the smallest domain the geometry genuinely supports**, defaulting on.

1. New module `server/solver/symmetry.py` with a mesher-authoritative resolver:
   `resolve_symmetry(design) -> SymmetryResolution` carrying the chosen quadrants mask, whether each mirror plane holds (`xz`, `yz`), and human-readable `reasons` for every plane that was rejected.
   Determine the planes **geometrically, not by guessing from parameter names** — the same philosophy as `circsym_rejection_reasons`, which defers to `build_meridian` so it agrees with the real path by construction. Build the full-circle point grid once at a modest resolution through the existing translate path (`server.preview.translate.design_to_mesher_config` → `hornlab_mesher.config_builder.build_point_grid`), then test the sampled surface for mirror symmetry about y→−y (xz plane) and x→−x (yz plane) within a tolerance stated relative to the model's own size. Compare *sets of points*, not row order — the azimuth lattice is not guaranteed to be mirror-aligned, so resample or match by nearest neighbour and state the tolerance you chose in the new doc.
   The enclosure is not part of that grid: reject a plane when the enclosure is active and its spacings break it (`space_l` vs `space_r` for yz, `space_t` vs `space_b` for xz), and reject the xz plane when `mesh.vertical_offset` is non-zero. Infinite-baffle and source geometry that breaks a plane must be rejected too — think about each, and say in `docs/SYMMETRY-CONTRACT.md` which inputs you audited.
   Be conservative: any doubt resolves to the larger domain. A wrong reduction is a silently wrong solve; a missed reduction only costs time.

2. `SolveOptions` (`server/jobs/models.py`) gains `symmetry: str = "auto"` accepting `auto | full | half_xz | half_yz | quarter`, validated like the existing `engine` field.

3. At submit (`server/jobs/runtime.py`), resolve `auto` to a concrete mask, use it for the mesh build and the solve, and record on the job: the mode requested, the mask resolved, and the reasons. Explicit modes are honoured as given but still **validated** — if the user forces a plane the geometry does not have, fail with a clear validation error naming the plane and the reason rather than solving a corrupt domain. `design.mesh.quadrants` stays untouched in the design document (it is the ATH field and must round-trip byte-identically); auto simply overrides it for the solve, and the job record must make that visible.

4. New endpoint `POST /api/design/symmetry` taking a design payload and returning the resolution — the parameter panel uses it to show the user what auto resolved to, live. Keep it cheap (it is called on design edits): reuse the preview grid path at coarse resolution and say in the doc how long it takes for each family.

5. `frontend/src/stores/solveOptions.ts` gains the symmetry mode with `auto` default and persistence alongside the existing options; `frontend/src/jobs/actions.ts` sends it and exposes a typed `fetchSymmetry(design)` helper for the panel lane to call. Do **not** build the quadrant UI — another lane owns `ParamPanel.tsx`. Just export the helper and the types.

## Task 2 — CircSym stops being a user-facing backend

CircSym is not a solver. It is the axisymmetric meridian fast path: for a body of revolution it solves one meridian slice and rotates the result. The adapter in `server/solver/circsym.py` already does exactly that and is correct — the mistake is presenting it in the same list as Metal and BEMPP, where it reads as a competing backend.

1. `detect_engines()` (`server/engines/registry.py`) stops emitting `circsym` as an engine. The user-facing engine list becomes Metal / BEMPP (/ dry-run when gated on). Report the meridian capability instead as a **property of the Metal engine** — extend `EngineInfo` with something like `fast_paths: tuple[str, ...]` (or an equivalent honest field) so `/api/capabilities` can still say "metal, with axisymmetric meridian" and the UI can show it as a capability rather than a choice.

2. Route it automatically. When the resolved engine is `metal` and the design's `simulation.solver_mode` is `auto` (the default) and `circsym_rejection_reasons(config)` returns empty, run the meridian path; otherwise run the full-3D path. Keep `solver_mode: circsym` working as an explicit **force** (error clearly if the geometry is not eligible, listing the rejection reasons) and `full_3d` as an explicit opt-out. Do not change the `.cfg`/ATH spellings of those values — they must round-trip.

3. Honesty in the record: every solve records which path actually ran and why (`solve_path: "full-3d" | "axisymmetric-meridian"`, plus the eligibility reasons when it declined), and the solve wall-clock. The user's experience is that the meridian path "didn't turn out to be faster"; the job record must carry the numbers to settle that rather than either of us assuming. Surface `solve_path` in the job's result metadata so the results panel can display it.

4. `resolve_auto_engine` loses the `circsym` special case for `solver_mode`, since the mode no longer selects an engine. Keep BEMPP's existing rejection of circsym mode meaningful: with `solver_mode: circsym` forced and BEMPP selected, fail with the existing clear message.

5. Keep `create_engine("circsym")`/`CircSymEngine` working — it is now an internal fast path, reachable by the Metal route and by tests, just not offered as a backend choice.

6. `frontend/src/design/SolveOptionsSections.tsx`: the backend dropdown shows only real backends; add a line stating the resolved fast path capability for the selected engine. Relabel the solver-mode select so it stops reading as a backend switch — `Auto`, `Full 3D`, `Axisymmetric (force)` — without changing the stored values.

---

## Definition of done

- `"../Waveguide Generator/.venv/bin/python" -m pytest server/tests -q` passes; report the new count against the 669 baseline.
- `cd frontend && npm test` and `npx tsc --noEmit` pass for the files you touched.
- New tests cover: a circular R-OSSE resolving to quarter; a design with a rotated/asymmetric guiding curve resolving to a larger domain with a stated reason; a vertical offset killing the xz plane; asymmetric enclosure spacing killing the matching plane; an explicit forced plane the geometry lacks being rejected; `circsym` absent from the engine list; a metal solve on an eligible circular design taking the meridian path and recording it; an ineligible design taking full-3D with reasons recorded.
- `docs/SYMMETRY-CONTRACT.md` documents the resolver: what is sampled, the tolerance and why, the inputs audited per plane, the cost per family, and the auto-vs-explicit precedence.
- Report in your final message: test counts, the symmetry tolerance you chose, resolver timings, and anything you found but did not fix.
