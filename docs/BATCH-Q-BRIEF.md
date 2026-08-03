# Phase 4, Batch Q — real engines: mesh build + metal/bempp/CircSym/IB adapters + result mapping

Port v1's solve pipeline into v2: real mesh construction and the four engine paths, with faithful result mapping. This is the largest port batch — the hard-won operational knowledge lives in these files; cite v1 file:line throughout.

**Path discipline: create/modify ONLY `server/mesh/**`, `server/solver/**`, `server/engines/**`, `server/tests/test_engines_*.py`, `server/tests/test_mesh_*.py`, and mount/wiring lines in `server/app.py` + the engine dispatch seam inside `server/jobs/runtime.py` (extend the existing engine-call seam only — do not restructure the scheduler).**

Read first: plan §4.4 (execution model), `docs/RESULT-CONTRACTS.md` (the mapping contract — batch C mined it), WS-PROTOCOL §2 (events), `server/jobs/` (batch J's seam).

## v1 ground truth (read-only, `../Waveguide Generator/server/`)

- `services/gmsh_worker.py` — THE single persistent gmsh worker thread; gmsh must initialize `interruptible=False` off-main-thread (SIGINT gotcha). Port as-is in spirit.
- `services/simulation_runner.py` — solve orchestration (`asyncio.to_thread`, staged progress, CircSym ~line 397, full-3D ~line 500).
- `solver/` — mesher_adapter (build_waveguide_mesh w/ cancellation callback), metal + bempp adapters, quadrants handling, `result_mapping.py` (balloon four-state ~line 357, conventions), `beam_shape.py`.
- `solver_bootstrap.py` + `services/runtime_preflight.py` — real capability detection (metal helper binary, bempp import, gmsh).

## Deliverables

1. `server/mesh/` — gmsh worker port + `build_solver_mesh(design, options, cancel_cb, progress_cb)` via `hornlab_mesher` (full OCC build — distinct from the preview path), mesh stats + integrity report (open-edge detection per v1), `.msh` artifact persisted via the jobs store.
2. `server/solver/` — engine adapters: `metal` (native helper via hornlab-metal-bem, the default on this Mac), `bempp` (CPU fallback; import guarded — absence is a clean "not detected"), `circsym`, `infinite-baffle` — each mapped through the v1 semantics (quadrant/symmetry rules, source conventions incl. velocity/acceleration drive). Solve stages report progress + logs through batch J's runtime hooks.
3. `server/solver/result_mapping.py` — port per RESULT-CONTRACTS: units, normalization, phase convention, observation origin, plane availability, NaN/partial-failure policy, balloon four-state, DI, beam-shape. The v2 result JSON shape = batch J's dry-run shape EXTENDED faithfully (dry-run stays a subset so the UI needs no branching).
4. `server/engines/registry.py` — real detection replacing the placeholders: metal (helper binary present + loadable), bempp (importable), circsym (mesher capability), each with honest reason strings; dryrun stays env-gated.
5. Tests: adapters unit-tested with mocked native layers (mapping, quadrants, staging, cancellation); result-mapping golden tests using fixtures you generate from SMALL real solves where feasible; capability detection under monkeypatched environments; mesh build cancellation mid-stage; integrity report on a known-open fixture mesh if one is constructible cheaply. Full suite green.
6. A tiny-design REAL metal smoke path: `server/tests/test_engines_metal_live.py` marked with `@pytest.mark.live` (skipped by default, run with `-m live`): submits a very small OSSE freestanding solve (few frequencies, coarse mesh) through the full jobs pipeline and asserts completion + plausible result ranges. The overseer runs it live.

## Rules
- No new deps. Engine work stays in `asyncio.to_thread`; gmsh calls ONLY through the worker.
- If bempp cannot initialize in this venv, its adapter still ports (tests mocked) and detection reports the true reason — do not fake availability.
- Final message: files, test counts, per-engine port status + deliberate deferrals, the exact live-test commands for the overseer (both the pytest -m live run AND a curl-based full-pipeline metal solve).
