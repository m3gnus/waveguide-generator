# Backlog

Last updated: March 16, 2026

This file is the active source of truth for unfinished product and engineering work.
Detailed completion history from the March 11-12, 2026 cleanup phase lives in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.

## Working Rules

### Upstream-downstream integrity

Modules must not compensate for defects that belong upstream. Each module should receive correct input and fail visibly if it does not. When downstream code contains a workaround for an upstream defect, the fix belongs in the upstream module, not in the workaround.

### Tessellation-last principle

Tessellation (mesh generation) must always be the **last** geometry transformation step. Never modify, clip, or transform a tessellated mesh to achieve geometric changes — instead, modify the upstream parametric/B-Rep geometry and re-tessellate. Tessellated meshes are consumed directly by solvers and exporters without further geometric mutation.

Rationale: OCC free-meshing does not produce mirror-symmetric vertices, so post-tessellation clipping creates meshes that are not equivalent to the original (measured: 14.8 dB BEM error from clipping artifacts). Cutting the smooth B-Rep geometry before tessellation produces clean, purpose-built meshes.

### Docs and audit discipline

Keep durable decisions in `docs/architecture.md`, active work in this file, and per-module contracts in `docs/modules/`. Put generated audits, comparisons, and experiment output under `research/`.

## Current Baseline

Status as of March 15, 2026:

- The architecture cleanup plan is complete.
- The enclosure BEM simulation bug (self-intersecting geometry when `enc_depth < horn_length`) is fixed — depth clamping applied in `_build_enclosure_box`.
- Settings modal, viewer settings, folder workspace, parameter naming/hover-help, geometry diagnostics, advanced solver controls, and job feed cleanup are all shipped.
- Active runtime docs are `README.md`, `docs/PROJECT_DOCUMENTATION.md`, `tests/TESTING.md`, `server/README.md`, and `AGENTS.md`.
- MSH import: viewport display, return-to-parametric, and filename-derived export naming are all working. Return-to-parametric viewport blank bug and MSH naming bug both fixed.
- Help tooltips moved from `?` button to label hover; `ƒ` formula button now in the label row.
- Directivity Map section now has expand/collapse matching geometry tab sections.
- Measurement distance propagation verified correct end-to-end (UI → solver → observation frame).
- Enclosure mesh edge over-refinement fixed — explicit Gmsh sizing field for enclosure edges.
- All 257 tests pass (0 failures) — 12 pre-existing failures fixed.

## Active Backlog

### P1. Symmetry Performance — Half-Model Slower Than Full Model

**IN PROGRESS — implementing Approach A (image source method).**

#### Status (March 16, 2026)

Profiling and A/B testing complete. The performance potential is real (2.66x speedup, 2.96x DOF reduction at 700 elements; scales quadratically so ~4x speedup expected at 4000 elements).

**Key finding**: Post-tessellation mesh clipping is fundamentally flawed — OCC free-meshing produces asymmetric vertices (171 vs 172 DOFs per side), so clipping a tessellated mesh creates a different discretization (measured 14.8 dB BEM error). The image source method itself is correct (validated at 0.000 dB error on a proper symmetric mesh), but must operate on a mesh that was **purpose-built as a half-model** by the OCC builder.

**Revised approach**: Cut the B-Rep geometry at the symmetry plane BEFORE tessellation (tessellation-last principle). Two approaches were attempted:

1. **`quadrants=12` in OCC builder** — The builder already supports this (limits φ ∈ [0, π]), but `closed=False` changes upstream geometry construction in fundamental ways (`_build_annular_surface_from_boundaries`, `_build_mouth_rim_from_boundaries` etc. all assume closed loops when `quadrants=1234`), causing "Curve loop is not closed" errors. Not viable without significant refactoring of the builder's topology construction.

2. **B-Rep symmetry cut via `_apply_symmetry_cut_yz()`** — Build full geometry with `quadrants=1234`, then use `gmsh.model.occ.fragment()` to split all surfaces at the YZ plane (X=0) before tessellation. This is implemented in `waveguide_builder.py` with a `symmetry_cut="yz"` parameter. **Current blocker**: The cut function has a bug — the Gmsh `addRectangle()` API creates surfaces in the XY plane only. The function was creating the cutting surface in the wrong plane (XY instead of YZ). Fixed to use explicit point/line/surface construction, but now hits a new Gmsh error: `Unknown surface 2` during `fragment()` — likely the OCC `fragment` operation is failing because the cutting surface intersects complex BSpline patches in ways that OCC cannot resolve cleanly.

**SPL error status**: When the cut function was creating a wrongly-oriented cutting plane (no actual cut), the half mesh was identical to the full mesh (same verts/tris) but the image source BEM still showed 12-13 dB errors. This confirms the image source implementation itself has remaining issues beyond just the mesh cut. The 0.000 dB validation was on a synthetically-constructed symmetric mesh, not on the actual OCC-built mesh.

#### Decisions

1. **Approach A** (image source method) — implement now. Keep **Approach B** (block-Toeplitz) as a fallback or future settings option.
2. **OpenCL on Apple Silicon**: Apple Silicon has no OpenCL GPU driver (Metal-only). The pocl library provides CPU-only OpenCL. The current "GPU" detection in the API is probably selecting the pocl CPU device and misclassifying it — or hitting a pocl kernel compilation issue. This is a separate bug to investigate later; not blocking the symmetry work (numba backend works fine for development).
3. **Typical mesh size**: ~4000 elements. At N=4000: full solve ≈ (4000/700)² × 10.7s ≈ 350s, half-model ≈ 87s — roughly 4× speedup, well worth implementing.

#### Root cause (why mesh-slicing fails)

`apply_neumann_bc_on_symmetry_planes` in `solve_optimized.py` is a **no-op**. BEM requires the **Green's function** to encode the symmetry plane, not just a boundary condition tag. For a rigid (Neumann) wall at X=0:

> G_half(x, y) = G(x, y) + G(x, ȳ) where ȳ = (−y_x, y_y, y_z)

This means each BEM operator O must be replaced by O_direct + O_image, where O_image assembles the same kernel but with trial sources at their mirror positions (and mirror normals). The solve is then done on the reduced mesh (fewer DOFs) with correct physics.

#### How the image method works in bempp-cl

bempp-cl has no built-in image/symmetry support, but supports cross-grid operators (test and trial on different grids). This lets us implement the image method manually:

1. Build **mirrored grid**: flip X-coordinates of all vertices, **reverse triangle winding** (swap index columns 1↔2). The winding reversal is critical — it produces the correct reflected normals n_ȳ = (−n_x, n_y, n_z) for all operators that involve trial normals (DLP, HYP).

2. For each of the 4 BEM operators, assemble two matrices:
   - Direct: trial on reduced grid, test on reduced grid (standard)
   - Image: trial on **mirrored** grid, test on reduced grid

3. Sum: A_total = A_direct + A_image. The matrix is the same size as the half-model (N/2 DOFs) but encodes the rigid wall correctly.

4. Similarly double the RHS: for Neumann BC the throat source and its mirror have equal amplitude, so b_total = b_direct + b_image.

5. For quarter-model (quadrants=1, both planes): 4 contributions — direct + X-mirror + Z-mirror + XZ-mirror.

#### Completed work

- [x] Decide whether to disable symmetry entirely or fix it — decided: fix.
- [x] Profile a half-model vs full-model solve — Done: `server/scripts/benchmark_bem_symmetry.py`. Full: 12.2s, Half: 4.5s (3 freqs, 700 elements, numba backend).
- [x] Verify proportional DOF reduction — Done: P1 DOF full=352, half=119 (2.96x). DP0 full=700, half=297 (2.36x).
- [x] Replace O(N²) vertex-matching with parameter-driven detection — Done: `_symmetry_from_quadrants()` in `symmetry.py`, O(1).
- [x] Remove `enable_symmetry: false` override in `src/solver/index.js`.
- [x] A/B test — FAILED: 18-25 dB SPL errors. Script: `server/scripts/ab_test_symmetry.py`.
- [x] Re-enable `quadrants == 1234` safety gate — Done in `simulation_runner.py` and `simulation_validation.py`.
- [x] Implement image source operators (`_assemble_image_operators`, cross-grid assembly) in `solve_optimized.py`.
- [x] Implement `create_mirror_grid()` in `symmetry.py` (flip + reverse winding).
- [x] Add `symmetry_cut` parameter to `build_waveguide_mesh()` and `_apply_symmetry_cut_yz()`.
- [x] Add `_resolve_occ_adaptive_quadrants()` to detect symmetric geometries in `simulation_runner.py`.
- [x] Update `evaluate_symmetry_policy()` to skip clipping when mesh is already a half-model.
- [x] Validate image source method at 0.000 dB on synthetic symmetric mesh.
- [x] Identify tessellation-last principle and add as working rule.
- [x] Fix `_apply_symmetry_cut_yz` to construct cutting plane in correct orientation (YZ, not XY).
- [x] Fix tool surface removal in `_apply_symmetry_cut_yz` (pure tool fragments were not being removed).
- [x] Fix `closed` flag regression (`closed` must not depend on `symmetry_cut`).

#### Action plan (revised — geometry-first approach)

**Step 0 — Build half-model mesh at the geometry level** _(BLOCKED — see problems below)_

Two sub-approaches were attempted:

**0a. Direct `quadrants=12` build** — FAILED

- The builder's `closed` flag gates all downstream topology construction (throat disc, mouth rim, rear disc, annular surfaces). Setting `closed=False` via `quadrants≠1234` causes "Curve loop is not closed" errors because surface constructors assume closed curve loops.
- Would require significant refactoring of `_build_annular_surface_from_boundaries`, `_build_mouth_rim_from_boundaries`, `_build_throat_disc_from_inner_boundary`, and `_build_rear_disc_assembly` to handle open-topology half-models.

**0b. B-Rep symmetry cut (`symmetry_cut="yz"`)** — PARTIALLY IMPLEMENTED, NEEDS DEBUGGING

- Build full geometry with `quadrants=1234`, then use `gmsh.model.occ.fragment()` to split all surfaces at the YZ plane before meshing.
- **Implementation**: `_apply_symmetry_cut_yz()` in `waveguide_builder.py` (~line 2041). Creates a planar surface at X=0, fragments all model surfaces against it, removes surfaces with COM at X<0, removes tool surface fragments, updates `surface_groups` mapping.
- **Bug fixed**: `addRectangle()` always creates in XY plane — replaced with explicit point/line/planeSurface construction in the YZ plane (X=0).
- **Bug fixed**: Tool surface fragments (from the cutting rectangle) were not being removed, causing the half mesh to have more elements than the full mesh.
- **Bug fixed**: `closed` flag was incorrectly gated on `symmetry_cut` (`closed = (quadrants == 1234) and not symmetry_cut`), breaking upstream geometry construction. Reverted to `closed = (quadrants == 1234)`. Watertight validation now uses `require_watertight=closed and not symmetry_cut`.
- **Current error**: `Unknown surface 2` — the `fragment()` call fails because OCC cannot cleanly fragment complex BSpline surface patches against a planar surface. This may be a fundamental limitation of the OCC boolean fragment operation on the BSpline patches used by the builder.
- **Possible fix**: Instead of fragmenting BSpline surfaces (which OCC may not handle well), try a 3D approach: create a large half-space box (X≥0) and use `gmsh.model.occ.intersect()` on individual surfaces. Or try `gmsh.model.occ.cut()`. Alternatively, synchronize the OCC model before fragment (call `occ.synchronize()` before the fragment step) — the surfaces may not be registered in the OCC kernel yet when fragment is called.

**Step 1 — `create_mirror_grid()` in `server/solver/symmetry.py`** _(already implemented)_

- Flip vertex coordinates + reverse winding for mirror grids
- Returns `(mirrored_vertices, mirrored_indices)` tuples

**Step 2 — `_assemble_image_operators()` in `HornBEMSolver`** _(already implemented)_

- Builds bempp Grid + DP0/P1 spaces on each mirror grid
- Cross-grid operators assembled per-frequency in `_solve_single_frequency`

**Step 3 — Dense matrix image solve in `_solve_single_frequency()`** _(already implemented)_

- LHS: `A = A_direct + A_image` (dense matrices)
- RHS: `b = b_direct + b_image`
- Scipy GMRES on the dense system
- On-axis SPL via direct + image potential operators

**Step 4 — Directivity re-expansion** _(already implemented)_

- Reconstruct full mesh by concatenating reduced + mirror grids
- Replicate P1/DP0 solution coefficients for each mirror section

**Step 5 — Validate with A/B test** _(blocked on Step 0)_

- The image method itself validated at 0.000 dB on a synthetically-constructed symmetric mesh
- **WARNING**: When the B-Rep cut was broken (no actual cut, same mesh used for both), image source BEM still showed 12-13 dB errors vs full model. This suggests the image source operator assembly/solve has issues beyond just the mesh quality. The 0.000 dB validation used a handcrafted symmetric mesh, not the actual OCC mesh pipeline. Need to investigate whether the image source operators are being assembled correctly on real OCC meshes.
- A/B test script: `server/scripts/ab_test_symmetry.py`

**Step 6 — Remove safety gate and update docs**

- Remove `quadrants=1234` override in `simulation_validation.py` and `simulation_runner.py`
- Remove `clip_mesh_at_plane()` and related post-tessellation clipping code from `symmetry.py`
- Update backlog

**Fallback: Approach B (block-Toeplitz)**
If Approach A's cross-grid operator assembly turns out to be unsupported by bempp-cl at runtime, fall back to:

- Assemble full BEM matrix on the full mesh (quadrants=1234)
- After `operator.weak_form().to_dense()`, partition into blocks: A = [[A₁₁, A₁₂], [A₂₁, A₂₂]]
- By symmetry: A₁₁ ≈ A₂₂ and A₁₂ ≈ A₂₁. Solve symmetric mode: (A₁₁ + A₁₂)x_s = b_s. This halves GMRES time; assembly is unchanged.
- This approach is a settings-selectable option if both methods are implemented.

#### Additional items (approach-independent)

- [x] Add committed ATH reference fixtures for reproducible regression testing.
- [ ] Fix OpenCL `kernel_function` error on Apple M1 Max — investigate whether pocl is being selected as "GPU" (it's CPU-only), and whether switching to `opencl_cpu` explicitly makes it work. This is separate from the symmetry work.

#### Bugs fixed during this investigation

- Fixed `grid_from_element_data` → `bempp_api.Grid()` in `solve_optimized.py` (bempp-cl API mismatch).
- Fixed `reduced_indices` shape `(M,3)` → `(3,M)` in `symmetry.py` (bempp Grid expects column-major indices).

#### Key files

- `server/solver/symmetry.py` — parameter-driven detection + mesh reduction + `create_mirror_grid()`
- `server/solver/solve_optimized.py` — BEM solver with image source operators (`_assemble_image_operators`, `_solve_single_frequency`)
- `server/solver/waveguide_builder.py` — OCC builder with `symmetry_cut` parameter, `_apply_symmetry_cut_yz()`
- `server/services/simulation_runner.py` — quadrants enforcement (safety gate) + `_resolve_occ_adaptive_quadrants()`
- `server/services/simulation_validation.py` — quadrants=1234 force
- `server/scripts/benchmark_bem_symmetry.py` — performance profiler
- `server/scripts/ab_test_symmetry.py` — A/B directivity comparison test (geometry-first approach)

#### Suggested next steps for a fresh session

1. **Debug `_apply_symmetry_cut_yz()`**: The `gmsh.model.occ.fragment()` call fails with "Unknown surface 2". Try:
   - Add `gmsh.model.occ.synchronize()` BEFORE the fragment call (surfaces may not be registered in OCC kernel yet)
   - If that fails, try `gmsh.model.occ.cut()` or `gmsh.model.occ.intersect()` with a half-space box instead of fragment with a plane
   - As a last resort, consider approach 0a (refactoring builder for open-topology half-models)

2. **Investigate image source BEM errors independently**: Even with identical meshes (no cut), the image source method shows 12-13 dB errors. Run a controlled test: use the SAME full mesh for both full and half solves, but apply image source operators on the half. If errors persist, the operator assembly/solve has bugs independent of mesh quality. Check:
   - Sign conventions in cross-grid operators (especially hypersingular + adjoint double-layer)
   - Whether bempp-cl cross-grid assembly handles P1 DOF deduplication correctly at mesh boundaries
   - The dense matrix solve (GMRES) convergence and condition number

3. **Run test suites**: `cd server && python -m pytest` and `npm test` to check for regressions from the current changes.

### P2. Observation Distance Measurement Origin — User-Selectable Reference Point

The observation frame origin was changed from throat to mouth (commit `caa3ce7`, IEC 60268-5). Instead of hardcoding either choice, expose it as a user-selectable parameter so the user can choose `mouth` (default) or `throat` as the measurement reference point.

The full data flow is straightforward — `PolarConfig` already passes through cleanly from UI → API → solver, so a new field flows end-to-end with no plumbing changes outside the listed files.

Action plan:

- [x] Clarify correct measurement origin — decided: mouth plane as default.
- [x] Update `infer_observation_frame` to use `mouth_center` as default origin.
- [x] Document the measurement convention in code comments.
- [x] Add `observation_origin` field to `PolarConfig` in `server/contracts/__init__.py` (values: `"mouth"` | `"throat"`, default `"mouth"`).
- [x] Thread `observation_origin` through `solve_optimized.py` → `infer_observation_frame`.
- [x] Make `infer_observation_frame` select `mouth_center` or `source_center` based on the parameter.
- [x] Add a "Measurement Origin" select control to polar settings UI (`polarSettings.js`).
- [x] Add tooltip: "Reference point for observation distance. Mouth (default, IEC 60268-5) or Throat."
- [x] Run test at 0.5m with both origins, verify results differ as expected.

Implementation notes:

- `server/contracts/__init__.py` (`PolarConfig`)
- `server/solver/observation.py` (`infer_observation_frame`)
- `server/solver/solve_optimized.py` (pass-through)
- `src/ui/simulation/polarSettings.js` (new select control)

### P2. Solver Settings Audit — Correctness, Defaults, and Tooltips

Review all solver settings for end-to-end correctness, appropriate defaults, and add mouse-over explanation text to every control.

Decision (March 15, 2026): **remove** the `enable_symmetry` UI toggle. `Mesh.Quadrants` is the sole symmetry control — the toggle is dead code (`src/solver/index.js` line 266 hardcodes it to `false`).

Issues identified:

- `enable_symmetry` toggle: remove from UI. `Mesh.Quadrants` controls symmetry reduction directly.
- `symmetryTolerance`: persisted in localStorage and sent in API contract but has no UI control. Either add a control or remove from the contract (defer until symmetry is working).
- `verbose` defaults to `true` (always detailed server logging) — consider defaulting to `false`.
- "Planned Controls" stubs (GMRES params) show in Advanced modal but are not implemented on the backend.

Action plan:

- [x] Remove `enable_symmetry` toggle from the Settings modal UI.
- [x] Remove the `enable_symmetry: false` hardcode in `src/solver/index.js` line 266 (no longer needed once the toggle is gone).
- [x] Audit each remaining active setting end-to-end: UI control → localStorage → API contract → backend solver.
- [x] Defer `symmetryTolerance` UI control until the P1 Symmetry Performance work is complete. (Confirmed: getter exists with default 0.001, no UI control needed until symmetry is working.)
- [x] Review `verbose` default — consider defaulting to `false`.
- [x] Either implement or remove the "Planned Controls" stubs (GMRES params, strong-form preconditioner).
- [x] Ensure every active setting has a tooltip explaining what it does, what changes when you raise/lower it, and what the recommended default is.

Implementation notes:

- `src/ui/settings/simBasicSettings.js`
- `src/ui/settings/simAdvancedSettings.js`
- `src/solver/index.js` (line 266 enable_symmetry override)
- `server/solver/solve_optimized.py`
- `server/contracts/__init__.py`

### P2. Cross-Platform Installation Hardening and Supported Runtime Matrix

**NOT STARTED — large scope touching installers, launchers, and docs across all platforms. Can be sliced into smaller pieces (e.g. preflight script first, then installer updates, then docs consolidation).**

- Replace the current "best effort" setup story with one explicit supported matrix and one verified install lane per supported OS/architecture.
- Publish support tiers instead of treating all Windows/macOS/Linux machines as equivalent:
  - Windows 10/11 x64: supported for the core app and OCC meshing; full simulation requires a verified OpenCL ICD/runtime.
  - macOS 13+ Intel x64: supported for the core app, OCC meshing, and a verified CPU-OpenCL simulation lane.
  - macOS 13+ Apple Silicon: supported for the core app and OCC meshing; full simulation remains a separately validated managed-env lane and must not be documented as parity-complete until it is verified end to end.
  - Linux x86_64: support Ubuntu 22.04/24.04 as the primary full-runtime target; keep other distros best effort unless they are added to CI.
  - Windows ARM64 and Linux ARM64: mark full simulation unsupported until gmsh/bempp/OpenCL parity is proven.
- Narrow the installer targets to exact repo-owned versions instead of open-ended ranges:
  - Node.js: pin one current LTS major for the repo and record it in a version file (`.nvmrc`, Volta, or equivalent).
  - Python: pin one tested minor for installation guidance and environment creation instead of accepting any `3.10 - 3.14` interpreter on PATH.
- Make installation modes explicit:
  - `Core app ready`: preview plus STL/CSV/config exports.
  - `OCC mesh ready`: `/api/mesh/build` available with supported Python + gmsh runtime.
  - `Simulation ready`: `/api/solve` available with supported bempp-cl + OpenCL runtime.
- Do not end installation with a generic success banner unless the achieved readiness level is stated clearly. If bempp/OpenCL is missing, finish as `Core app ready` or `OCC mesh ready`, not "fully installed".
- Add one backend/runtime preflight command that installers and launchers both call. It should verify:
  - selected interpreter path and version
  - active `pip` target / environment root
  - `gmsh` import + version
  - `bempp-cl` import + version
  - `pyopencl.get_platforms()`
  - `/health` dependency matrix alignment once the backend starts
- Fail early on unsupported newer Python as well as too-old Python. The current installers only enforce the lower bound, which still allows an unsupported interpreter to install partially and fail later at runtime.
- Stop reintroducing interpreter drift after setup:
  - launchers should prefer the installer-created environment deterministically
  - do not silently fall back to an arbitrary system `python3` once a managed project/runtime environment exists
  - persist the chosen backend interpreter path so launchers and diagnostics use the same runtime the installer validated
- Replace optimistic fallback wording in launch/start flows with actual runtime truth. Do not imply mock solver support or a working simulation path when dependencies are absent.
- Add install smoke coverage in CI across the supported matrix so dependency regressions are caught before release.
- Consolidate install/operator docs into one canonical installation guide, then keep `README.md`, `server/README.md`, and `docs/PROJECT_DOCUMENTATION.md` in parity with that guide and with `server/solver/deps.py`.

Research notes:

- `server/solver/deps.py` is the runtime truth: Python `>=3.10,<3.15`, gmsh `>=4.11,<5.0`, bempp-cl `>=0.4,<0.5`.
- `install/install.sh` and `install/install.bat` currently only reject Python older than 3.10. They do not reject unsupported newer Python, even though the backend dependency matrix does.
- `scripts/start-all.js` and `server/start.sh` can still fall back to a system `python3`, which can bypass the environment the installer configured.

Implementation notes:

- `install/install.sh`
- `install/install.bat`
- `scripts/start-all.js`
- `server/start.sh`
- `README.md`
- `server/README.md`
- `docs/PROJECT_DOCUMENTATION.md`
- add a shared runtime-check script invoked by both installers and launchers
- add repo-owned toolchain version files / environment spec

### P2. Firefox Output Folder — Path Display, Finder Link, and Setup Instructions

When using Firefox, the output folder UX is poor: there is no `showDirectoryPicker` API available (Firefox does not implement it and there is no browser setting to enable it), so users currently get a raw `window.prompt()` asking for a server-relative path string. The "Choose Folder" button in the Settings modal is disabled with generic help text.

Desired behavior:

- Show the current output folder as a human-readable absolute path (default when nothing is selected: `{repo_root}/output`).
- Provide a clickable link or button that opens the output folder in Finder (macOS) / Explorer (Windows) / file manager (Linux) directly from the browser.
- Replace the `window.prompt()` fallback with a proper in-UI panel: current path display at the top, "Open in Finder" button, and clear messaging that Firefox cannot select a custom folder via the browser (not a settings issue — the API is simply not implemented in Firefox).
- If the user is on Chrome/Edge and folder selection is supported, the existing `showDirectoryPicker` flow continues as-is.

Action plan:

- [x] Add a backend endpoint `GET /api/workspace/path` that returns the absolute path of the current output folder (defaulting to `{repo_root}/output`).
- [x] Add a backend endpoint `POST /api/workspace/open` that opens the folder in the OS file manager (`open` on macOS, `explorer` on Windows, `xdg-open` on Linux) via subprocess.
- [x] Set a hardcoded default output folder of `{repo_root}/output` so there is always a path to display, even before the user has selected anything.
- [x] Replace the `window.prompt()` fallback in `src/ui/fileOps.js` / `folderWorkspace.js` with a proper dialog/panel that: shows the current absolute path, has an "Open in Finder" button wired to the backend endpoint, and explains the Firefox limitation clearly.
- [x] In the Settings modal, when `supportsFolderSelection()` is false, show the path display + Finder button + explanation instead of a disabled button with generic help text.

Implementation notes:

- `src/ui/workspace/folderWorkspace.js`
- `src/ui/fileOps.js`
- `server/app.py` or `server/api/` (new workspace endpoints)

### P2. OpenCL GPU Support — macOS Research and Setup Instructions in UI

The system currently shows CPU-only OpenCL support when GPU support was expected. Research is complete (see below). The remaining work is exposing this context and platform-specific setup guidance in the UI.

**Research findings:**

- **Apple Silicon (M-series)**: No OpenCL GPU support is possible. Apple dropped first-class OpenCL after macOS 10.14 Mojave. The Apple GPU is Metal-only and has no OpenCL driver. `pocl` (via `brew install pocl` + `ocl-icd`) can provide a CPU-only OpenCL device, which is what the solver can use.
- **Intel Mac (macOS <=12)**: OpenCL GPU may work via Apple's built-in driver for Intel HD/Iris/UHD or AMD Radeon GPUs. Apple's driver has known workgroup-size quirks (hence the existing `configure_opencl_safe_profile()` workaround in the codebase).
- **Intel Mac (macOS 13+)**: OpenCL is increasingly deprecated and unreliable.
- **Linux**: Full OpenCL GPU support available via `intel-opencl-icd`, `rocm-opencl-runtime`, or `nvidia-opencl-icd` depending on GPU vendor.
- **Windows**: OpenCL GPU available via Intel OpenCL Runtime or CUDA toolkit (NVIDIA).

The backend already probes OpenCL at runtime via `_opencl_inventory()` in `device_interface.py` and exposes diagnostics through the `/health` endpoint. The settings modal already disables unavailable device options. What's missing is user-facing guidance explaining _why_ a device is unavailable and _what to do about it_.

Action plan:

- [x] Add OS and architecture fields to the `_opencl_inventory()` result (`sys.platform`, `platform.machine()`) so the frontend can show platform-specific instructions.
- [x] Add an expandable "Setup Help" affordance near the Compute Device control in the Settings modal that appears when the selected or requested device mode is unavailable.
- [x] Show platform-specific instructions dynamically based on OS/arch from the health endpoint:
  - Apple Silicon: explain GPU is Metal-only, provide pocl CPU setup via Homebrew (`brew install pocl ocl-icd`).
  - Intel Mac: suggest checking Apple driver status, note macOS 13+ deprecation.
  - Linux: suggest installing the appropriate OpenCL ICD package for the GPU vendor.
  - Windows: link to Intel OpenCL Runtime or CUDA toolkit.
- [x] The help section should be collapsed/hidden when the selected device is available, and shown automatically when it is not.

Implementation notes:

- `server/solver/device_interface.py` (`_opencl_inventory` — add OS/arch fields)
- `src/ui/settings/simBasicSettings.js` (add help affordance near device mode select)
- `server/api/routes_misc.py` (health endpoint — carry new fields through)

### P2. Measurement Distance — Surface Effective Distance in UI

Audit complete (March 15, 2026) — measurement distance propagates correctly end-to-end through all code paths. One remaining task: surface the actual distance used by the solver in the View Results modal.

The backend already returns `results.metadata.observation.effective_distance_m` and records any clamping in `results.metadata.warnings` (code: `"observation_distance_adjusted"`). This data is not yet shown in the frontend.

Action plan:

- [x] Trace and verify full propagation: UI → state → HTTP → solver (all correct)
- [x] Verify default (2.0m) applied when field is empty (correct)
- [x] Verify safe-distance clamping works (correct)
- [x] Show effective distance in the View Results modal (read from `results.metadata.observation`). Display any clamping warning when the solver adjusted the distance.

Implementation notes:

- `src/ui/simulation/viewResults.js` (display effective distance + clamping warning)

### P2. Tessellation Architecture — Geometry vs Mesh Separation

The current architecture should follow a clean separation: a geometry workflow constructs geometric data, then hands it to (a) the viewport tessellation module and (b) the .msh generation module. Neither tessellation module should construct geometry — they only translate and tessellate.

Audit status (March 14, 2026):

- The Python OCC path (`waveguide_builder.py`) combines geometry construction and meshing in one function.
- The JS viewport path (`buildWaveguideMesh.js`) also combines geometry computation with Three.js mesh construction.
- Both paths independently compute the same horn geometry from the same parameters — no shared geometric representation.

#### Geometry/mesh boundary analysis (March 15, 2026)

**Python OCC path (`server/solver/waveguide_builder.py`)**

Geometry layer (pure computation, no Gmsh calls):

- `_compute_rosse_profile` / `_compute_osse_profile_arrays` — evaluate the axial (x) and radial (y) profile at each (t, phi) sample.
- `_apply_morph` / `_compute_morph_target_info` — apply morph transformation to the radial coordinate.
- `_compute_outer_points` — offset inner surface by wall_thickness in the 2D profile plane.
- `_compute_point_grids` — the top-level geometry function. Takes the full params dict and returns `inner_points` (n_phi × n_length+1 × 3) and optionally `outer_points` (same shape). This is the geometry layer's output contract.

Meshing layer (everything that calls into `gmsh.model.occ.*`):

- Begins immediately after `_compute_point_grids` returns. `_build_surface_from_points` converts the point grid into BSpline surface patches using Gmsh OCC. All subsequent helpers (`_make_wire`, `_build_throat_disc_from_inner_boundary`, `_build_mouth_rim_from_boundaries`, `_build_enclosure_box`, `_build_rear_disc_assembly`, etc.) are Gmsh API calls only.
- The `build_waveguide_mesh` public entry point (not shown above, ~line 1700+) calls `_compute_point_grids`, then immediately invokes the Gmsh construction functions — there is no intermediate data hand-off.

**JS viewport path (`src/geometry/engine/`)**

Geometry layer:

- `src/geometry/engine/profiles/osse.js` (`calculateOSSE`) and `profiles/rosse.js` (`calculateROSSE`) — evaluate the 2D cross-section profile `{x, y}` at a given axial position `t` and azimuthal angle `p`.
- `src/geometry/engine/mesh/horn.js` (`evaluateInnerProfileAt`, `computeMouthExtents`, `buildMorphTargets`) — evaluate profiles and precompute morph extents. Also `createRingVertices` and `createAdaptiveRingVertices` which convert (t, phi) samples to flat `vertices[]` arrays.
- `src/geometry/engine/mesh/sliceMap.js` (`buildSliceMap`) — computes the axial slice distribution (t-values).

The geometry layer's implicit output is the flat `vertices` array (packed x/y/z triples) produced by `createRingVertices` / `createAdaptiveRingVertices`. There is no named struct or class that captures this as a "geometry result" — it is produced and immediately extended with indices in `buildWaveguideMesh`.

Meshing layer:

- `createHornIndices` / `createAdaptiveFanIndices` — generate the triangle index buffer from the vertex layout. This is the tessellation step.
- `addEnclosureGeometry` / `addFreestandingWallGeometry` — append additional vertex/index data for outer geometry.
- `generateThroatSource` — appends the throat disc fan.
- `orientMeshConsistently` / `validateMeshQuality` — post-processing on the completed mesh.
- The final output is `{ vertices, indices, ringCount, fullCircle, groups }` — a Three.js-ready flat mesh.

The boundary in both paths is the **point grid**: a 2D array of 3D sample points (n_phi × n_axial × 3), one per (phi, t) pair, with morph already applied. In the Python path this is explicitly named `inner_points`/`outer_points`. In the JS path it is the implicit pre-index state of `vertices[]` after `createRingVertices` returns but before `createHornIndices` runs.

#### Feasibility of a shared geometry representation

Not feasible as a simple shared module, for these reasons:

1. **Language barrier.** The geometry is computed in JS (for the viewport) and Python (for the mesh). The profile math is already duplicated and kept in parity (OSSE/R-OSSE both exist in `profiles/osse.js` and `waveguide_builder.py`). Sharing code would require either (a) running JS in Python (impractical), (b) running Python in the browser (impractical), or (c) extracting a JSON "geometry description" that gets sent over HTTP and consumed by the other side — which turns every viewport redraw into a network round-trip.

2. **Different resolution requirements.** The viewport uses `angularSegments` / `lengthSegments` (low-to-medium resolution, interactive). The BEM mesh uses `n_angular` / `n_length` (medium-to-high, controlled by solver quality settings). These are different numbers; a shared point grid would need to be generated at each path's own resolution anyway.

3. **Different topology needs.** The JS path uses a flat `vertices + indices` buffer optimised for GPU upload. The Python OCC path feeds point grids into Gmsh's BSpline surface constructor — it does not consume triangle meshes. The Gmsh path needs the raw control-point grid, not a triangulated surface.

4. **The existing parity is well-tested.** Both paths are kept in sync by the ATH parity test suite (`npm run test:ath`). Adding a shared layer would add complexity and a new failure mode without reducing duplication of the underlying math.

**Conclusion:** The architecture is intentionally duplicated and that is appropriate given the constraints. The correct boundary to maintain is the conceptual one already present: each path independently computes a point grid from parameters, then passes it to its own tessellation/meshing step. The action items below are closed without further implementation work.

Action plan:

- [x] Document the current geometry → mesh boundaries within each path.
- [x] Evaluate whether extracting a shared geometry representation (point grids + topology description) consumed by both paths is feasible without a full rewrite.
- [x] If feasible, design the shared geometry contract and implement incrementally. — **Not feasible (see analysis above). Closed.**

### ~~P3. Remove Simulation Jobs Refresh Button~~ — Done (2026-03-15)

Decision: keep but reduce to icon-only. The connection poller (`checkSolverConnection`, 10 s interval) does NOT trigger `refreshJobFeed`, so the edge cases (backend restart, external file changes, other browser sessions) remain realistic. Removed the text label; the button is now a small ↻ icon at 55% opacity, full opacity on hover/focus.

- [x] Confirm whether any of the above edge cases are realistic user scenarios — yes, connection poller does not call `refreshJobFeed`.
- [x] If keeping: move to a less prominent position (icon-only, lower visual weight) — done via `.button-icon-only` CSS class.

Files changed: `index.html`, `src/style.css`.

### P3. Symmetry Runtime Truth and Operator Control Follow-Through

Depends on P1 Symmetry Performance. Deferred until the symmetry performance root causes are resolved.

- [ ] Add a reproducible diagnostics lane for ATH reference configs, capturing imported params, canonical mesh topology, and resulting `metadata.symmetry_policy` / `metadata.symmetry`.
- [ ] Add regression coverage for reference cases so future geometry or solver changes cannot silently change reduction eligibility.
- [ ] Commit ATH reference fixtures or agreed repo-owned surrogate set for reproducible regression testing.

Previously completed:

- [x] Audit the existing `Enable Symmetry` control in the Settings modal and verify visibility, persistence, and payload behavior.
- [x] Surface the requested symmetry setting and the resulting `symmetry_policy` together in user-visible job/result surfaces.
- [x] Update runtime docs to clarify that imported ATH `Mesh.Quadrants` values do not directly trim the canonical simulation payload.

### P4. Maintained Markdown Document Overhaul

- [ ] Audit the maintained Markdown documentation set, then rewrite it for readability and architecture parity without inventing behavior that the code does not ship.
  - Scope the pass to the maintained `.md` docs that describe the live system: `README.md`, `AGENTS.md`, `docs/architecture.md`, `docs/PROJECT_DOCUMENTATION.md`, `docs/modules/*.md`, `tests/TESTING.md`, `server/README.md`, and `docs/archive/README.md`.
  - Verify each document against the actual runtime entry points, layer boundaries, active backend capabilities, supported export/simulation flows, and the current test map before rewriting copy.
  - Improve scannability with clearer section ordering, tighter wording, explicit source-of-truth links, and removal of stale or duplicated explanations that drift from the real architecture.

## Completed / Resolved

### P0. Enclosure BEM Simulation — RESOLVED

Fix applied March 14, 2026: `_build_enclosure_box` now clamps `enc_depth` to at least `horn_length + 1mm` so the back wall is always behind the throat. This prevents the self-intersecting geometry that caused omnidirectional-looking directivity.

### P2. Safety Mechanisms in .msh Generation — RESOLVED (Keep)

Audited March 15, 2026. All three safety mechanisms are genuinely needed and do not mask upstream bugs.

### Completed Items (March 11-15, 2026)

The following items are fully shipped:

- P1. Remove Stale Local-Only Jobs From the Backend Feed
- P1. OCC Mesh Diagnostics Must Reflect The Backend Solve Mesh
- P1. Parameter Inventory, Naming, Hover Help, and Ordering
- P1. Settings Panel Completeness and Information Architecture
- P1. MSH File Import — Viewport Display and Simulation Workflow
- P1. Return to Parametric — Viewport Blank + MSH Import Naming (both bugs fixed)
- P1. Enclosure Mesh Resolution — Edge Over-Refinement (Gmsh sizing field fix)
- P2. Help Tooltip — Move from Button to Label Hover
- P2. Folder Workspace Discoverability and Export Routing
- P2. Geometry Diagnostics Instead of Numeric BEM Tag Diagnostics
- P2. Advanced Solver Controls and BEM Precision Scope
- P3. Directivity Map Section — Add Expand/Collapse
- P3. Pre-Existing Test Failures — 12 failures fixed (257/257 pass)
- P3. Simulation Job Feed Source-Badge Cleanup

Detailed completion history in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.

## Deferred Watchpoints

- The Gmsh export stack remains part of the active runtime until solve-mesh and export-artifact parity exists without it.
- Internal decomposition of `server/solver/solve_optimized.py` and `server/solver/waveguide_builder.py` stays deferred unless new feature work makes those files a delivery bottleneck.
- Internal decomposition of `server/services/job_runtime.py` stays deferred unless queueing, persistence, or multi-worker lifecycle requirements expand materially.

Re-open the backlog when:

- a new product or runtime requirement lands
- a deferred watchpoint becomes an active delivery bottleneck
- a regression or documentation drift needs tracked follow-through across multiple slices
