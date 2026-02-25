# BEM Settings Menu Plan

Last updated: February 25, 2026

## 1. Purpose

Define an implementation-ready plan for a **Settings** menu that opens from the current **Check for App Updates** button location and exposes practical configuration for:

- Viewer controls
- BEM simulation runtime behavior
- Update-check action (moved into Settings)

This is a specification document only. It does not change runtime code.

## 2. Product Decisions (Locked)

- Settings scope includes both viewer controls and simulation controls.
- Settings depth is Basic + Advanced.
- Every setting includes one Recommended value.
- Existing update-check flow remains available, but from inside Settings.

## 3. Entry Point and UX

- Replace current button behavior:
  - `Check for App Updates` opens a Settings modal.
- Inside Settings, add a `System` section:
  - `Check for Updates` action, using existing `/api/updates/check` flow.
- Modal sections:
  1. Viewer Controls
  2. Simulation (Basic)
  3. Simulation (Advanced)
  4. System
- Add reset actions:
  - Reset section to Recommended
  - Reset all settings to Recommended

## 4. Settings Matrix

## 4.1 Viewer Controls (v1)

- Rotate speed
  - Recommended: current OrbitControls default-equivalent
- Zoom speed
  - Recommended: current default-equivalent
- Pan speed
  - Recommended: current default-equivalent
- Enable damping
  - Recommended: on
- Damping factor
  - Recommended: current default-equivalent
- Invert wheel zoom
  - Recommended: off
- Default camera mode on startup
  - Options: perspective, orthographic
  - Recommended: perspective
- Enable smoothing keyboard shortcuts
  - Recommended: on

## 4.2 Simulation Basic (v1, existing backend contract)

- Device mode (`device_mode`)
  - Options: `auto`, `opencl_gpu`, `opencl_cpu`, `numba`
  - Recommended: `auto`
- Mesh validation mode (`mesh_validation_mode`)
  - Options: `warn`, `strict`, `off`
  - Recommended: `warn`
- Frequency spacing (`frequency_spacing`)
  - Options: `log`, `linear`
  - Recommended: `log`
- Optimized solver path (`use_optimized`)
  - Recommended: on
- Symmetry reduction (`enable_symmetry`)
  - Recommended: on
- Verbose backend logging (`verbose`)
  - Recommended: off

## 4.3 Simulation Advanced (v2/v3, backend extension)

- Warm-up pass (`enable_warmup`)
  - Recommended: on
- Linear solver method
  - Options: `gmres`, `cg`
  - Recommended: `gmres`
- Linear solver tolerance (`tol`)
  - Recommended: `1e-5`
- GMRES restart (`restart`)
  - Recommended: backend default
- Max iterations (`maxiter`)
  - Recommended: backend default
- Strong-form preconditioner (`use_strong_form`)
  - Options: `auto`, `on`, `off`
  - Recommended: `auto`
- Burton-Miller coupling (`use_burton_miller`)
  - Recommended: on
- Symmetry tolerance (`symmetry_tolerance`)
  - Recommended: `1e-3`

## 4.4 Expert Assembly (v3 optional)

- Boundary assembler mode
- Potential assembler mode
- FMM parameters (expansion order / ncrit / nlevels)
- Quadrature order tuning

Default stance: hidden behind Advanced/Expert disclosure; not exposed in v1.

## 5. Recommendation Logic

- Always display one recommended choice per setting.
- Device recommendations are runtime-aware:
  - Read `/health.deviceInterface.mode_availability`.
  - If selected mode becomes unavailable, fallback to `auto` and show reason.
- Solver method recommendation:
  - Keep `gmres` recommended because `cg` requires Hermitian positive-definite systems and is not generally robust for this BEM system.

## 6. API / Contract Plan

### Phase 1 (no new API fields)

Use existing `SimulationRequest` fields:

- `device_mode`
- `mesh_validation_mode`
- `frequency_spacing`
- `use_optimized`
- `enable_symmetry`
- `verbose`

### Phase 2 (new optional `solver_options`)

Add optional object to `/api/solve`:

- `method`, `tol`, `restart`, `maxiter`, `strong_form`
- `enable_warmup`
- `use_burton_miller`
- `symmetry_tolerance`

### Phase 3 (new optional `assembly_options`)

Add optional object to `/api/solve`:

- Assembler mode selectors
- FMM controls
- Quadrature controls

## 7. Persistence Model (Frontend)

Store settings as `wg_settings_v1` in localStorage:

- `viewer` subtree
- `simulation.basic` subtree
- `simulation.advanced` subtree
- Schema version + tolerant load/reset behavior

Settings apply to new runs only; existing completed jobs are not modified.

## 8. Failure and Fallback Behavior

- Invalid persisted settings: fallback to recommended defaults.
- Unsupported runtime options: backend returns 422 or applies defined fallback behavior.
- OpenCL runtime failures during solve: keep existing fallback-to-numba behavior and metadata reporting.
- Partial failure model remains unchanged (`metadata.failures`, warnings, partial success).

## 9. Test Plan

### Frontend

- Settings button opens modal from previous update-check location.
- Check-for-updates action still works from inside Settings.
- Viewer settings persist and apply after reload.
- Simulation submit payload includes selected Basic settings.
- Recommended badges and reset actions behave correctly.

### Backend (Phase 2/3)

- Validation tests for new `solver_options` and `assembly_options`.
- GMRES/CG handling and fallback metadata behavior.
- Warm-up toggle reflected in `metadata.performance.warmup_time_seconds`.
- Strong-form mode behavior matches runtime capabilities.

### Full regression

- `npm test`
- `npm run test:server`

## 10. Implementation Map

Frontend integration:

- `index.html`
- `src/app/events.js`
- `src/app/App.js`
- `src/ui/feedback.js` or a new `src/ui/settingsModal.js`
- `src/style.css`
- `src/app/scene.js`
- `src/ui/simulation/jobActions.js`
- `src/solver/index.js`

Backend integration (Phase 2/3):

- `server/models.py`
- `server/api/routes_simulation.py`
- `server/services/simulation_runner.py`
- `server/solver/bem_solver.py`
- `server/solver/solve_optimized.py`
- `server/solver/solve.py`
- related tests in `server/tests/`

## 11. Rollout Order

1. Build Settings modal and migrate update-check button behavior.
2. Add Viewer Controls settings with persistence and apply-on-load.
3. Wire Simulation Basic settings into submission payload.
4. Add Advanced UI placeholders (disabled/hidden until backend support is merged).
5. Add backend Phase 2 support and enable corresponding Advanced controls.
6. Add Phase 3 expert controls only after validation and docs are updated.

## 12. Assumptions and Defaults

- `auto` device mode is default and recommended.
- `gmres` is default and recommended.
- `warn` validation mode is default and recommended.
- `log` frequency spacing is default and recommended.
- Advanced and expert controls are opt-in and separated from Basic UI.

## 13. Research References

- Bempp linalg wrappers (`cg`, `gmres`):  
  https://bempp-cl.readthedocs.io/en/latest/docs/bempp_cl/api/linalg/index.html
- Bempp boundary operators and assembler selection:  
  https://bempp.com/handbook/api/boundary_operators.html  
  https://bempp.com/handbook/core/assembling_operators.html
- Bempp global parameters (`assembly`, `quadrature`, `fmm`):  
  https://bempp-cl.readthedocs.io/en/latest/docs/bempp_cl/api/utils/parameters/
- SciPy CG requirements (SPD):  
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.linalg.cg.html
- SciPy GMRES behavior/options:  
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.linalg.gmres.html
