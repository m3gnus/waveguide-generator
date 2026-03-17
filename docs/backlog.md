# Backlog

Last updated: March 17, 2026

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

Status as of March 17, 2026:

- The architecture cleanup plan is complete.
- The enclosure BEM simulation bug (self-intersecting geometry when `enc_depth < horn_length`) is fixed — depth clamping applied in `_build_enclosure_box`.
- Settings modal, viewer settings, folder workspace, parameter naming/hover-help, geometry diagnostics, advanced solver controls, and job feed cleanup are all shipped.
- Active runtime docs are `README.md`, `docs/PROJECT_DOCUMENTATION.md`, `tests/TESTING.md`, `server/README.md`, and `AGENTS.md`.
- MSH import: viewport display, return-to-parametric, and filename-derived export naming are all working.
- Measurement distance propagation verified correct end-to-end (UI → solver → observation frame), with effective distance shown in View Results modal.
- All 268 JS tests pass. Python tests pass.
- **Symmetry optimization DISABLED** — image source method blocked by bempp-cl 0.4.x singular quadrature limitation. Code preserved for future revisit if bempp-cl adds cross-grid singular quadrature support.
- **Backlog effectively clean** — only UI quality issues and large-scope installation hardening remain.

## Active Backlog

### P2. UI Quality Audit — Accessibility & Theming

**Audit date: March 17, 2026**

#### Touch Targets Below Minimum Size

- **Location**: `src/style.css:1017-1027` (viewer controls)
- **Severity**: High
- **Description**: Viewer control buttons are 32×32px — below WCAG 2.1 minimum of 44×44px
- **WCAG**: 2.5.5 Target Size (AAA)

Action plan:

- [ ] Increase viewer control buttons to 44×44px minimum
- [ ] Audit all interactive elements for touch target compliance

#### Low Contrast Muted Text

- **Location**: `src/style.css:16` (`--text-muted`)
- **Severity**: High
- **Description**: `--text-muted` at `oklch(55% 0.015 268)` ≈ 3.5:1 contrast, below WCAG AA 4.5:1
- **WCAG**: 1.4.3 Contrast (Minimum)

Action plan:

- [ ] Darken `--text-muted` to at least `oklch(48% 0.015 268)` or increase font-weight

#### Hard-coded Scene Colors in JavaScript

- **Location**: `src/viewer/index.js:71-75`, `src/app/scene.js`
- **Severity**: High
- **Description**: 3D scene colors are hard-coded (`#080D16`, `#F4F0E8`, etc.) rather than using CSS design tokens
- **Impact**: Colors won't update if design tokens change

Action plan:

- [ ] Read CSS custom properties at runtime via `getComputedStyle()`, or
- [ ] Define scene colors in shared config that CSS also references

#### Missing Internationalization Infrastructure

- **Location**: Entire frontend codebase
- **Severity**: High
- **Description**: All UI strings hard-coded in English; no i18n library present
- **Impact**: Cannot localize for non-English users

Action plan:

- [ ] Decide on i18n approach (library vs. message file extraction)
- [ ] Extract UI strings to messages file
- [ ] Implement `Intl.MessageFormat` or similar

### P3. Replace Symmetry Policy with Solve Statistics in Results View

**Added: March 17, 2026**

**Description:** The Symmetry Policy section in the View Results modal is no longer relevant since symmetry optimization is disabled. Replace it with useful solve statistics: measurement distance (from mouth or throat), solve time, frequency count/range, and mesh complexity (vertices/triangles).

**Implementation notes:**

- Files: `src/ui/simulation/results.js`, `src/ui/simulation/viewResults.js`
- Available stats from backend response:
  - Total solve time: `metadata.performance.total_time_seconds`
  - Frequency count: `frequencies.length`, range from input config
  - Mesh stats: `mesh_stats.vertex_count`, `mesh_stats.triangle_count` (from job status)
  - Observation distance: `metadata.observation.effective_distance_m`, origin (mouth/throat)
  - Optional: GMRES iterations, BEM precision

Action plan:

- [ ] Create `renderSolveStatsSummary()` function to replace `renderSymmetryPolicySummary()`
- [ ] Display: solve time, frequency range/count, mesh complexity, measurement distance + origin
- [ ] Update `viewResults.js` to call new summary renderer instead of symmetry policy

### P3. Remove Symmetry Text from Job List Entries

**Added: March 17, 2026**

**Description:** The symmetry line "Symmetry: Requested Disabled | Decision Full model" displayed next to each solver task is unnecessary since symmetry optimization is disabled. Remove it to reduce visual noise.

**Implementation notes:**

- File: `src/ui/simulation/jobActions.js`
- Function: `getSymmetrySummaryLine()` (line 219)
- Render call in `renderJobList()` (line 368)
- Test dependency: `tests/simulation-flow.test.js` line 844

Action plan:

- [ ] Remove `getSymmetrySummaryLine()` function from `jobActions.js`
- [ ] Remove the symmetry line rendering in `renderJobList()`
- [ ] Update/remove symmetry assertions in `tests/simulation-flow.test.js`

### P3. UI Quality Audit — Medium-Severity Issues

**Audit date: March 17, 2026**

#### Viewer Controls Missing aria-label

- **Location**: `index.html:269-273`
- **Description**: Buttons use `title` but lack `aria-label`; screen readers announce only "+"
- **WCAG**: 4.1.2 Name, Role, Value

Action plan:

- [ ] Add `aria-label` attributes to all viewer control buttons

#### Form Field Error Recovery

- **Location**: `src/ui/inputValidation.js`
- **Description**: No `aria-describedby` linking inputs to error messages
- **WCAG**: 3.3.1 Error Identification

Action plan:

- [ ] Add `aria-describedby` linking inputs to their error messages

#### Modal Focus Management

- **Location**: `src/ui/feedback.js`, `src/ui/simulation/viewResults.js`
- **Description**: Focus not moved to modal on open; focus not trapped
- **WCAG**: 2.4.3 Focus Order

Action plan:

- [ ] Implement focus trapping for modals
- [ ] Set initial focus to modal when opened

#### Progress Bar Announcements

- **Location**: `index.html:191-208`
- **Description**: `aria-live="polite"` may flood screen readers during simulation
- **Recommendation**: Throttle to significant milestones (every 10%)

Action plan:

- [ ] Throttle aria-live updates to major progress milestones

#### Formula Info Panel Not a Dialog

- **Location**: `src/ui/paramPanel.js:365-442`
- **Description**: Overlay panel lacks `role="dialog"`, focus trap, and Escape key handler

Action plan:

- [ ] Convert to proper dialog with focus trap and Escape key handler

#### Status Dot Color-Only Indication

- **Location**: `index.html:107`, `src/style.css:723-735`
- **Description**: Connection status uses color-only (green/red/grey) — fails color-blind users
- **WCAG**: 1.4.1 Use of Color

Action plan:

- [ ] Add shape or icon differentiation alongside color

#### Skip Link Styling

- **Location**: `index.html:19`
- **Description**: Skip link exists but `.skip-link` CSS class not defined — may not be visible on focus
- **WCAG**: 2.4.1 Bypass Blocks

Action plan:

- [ ] Ensure skip link is visible on focus with proper styling

#### Animation with Reduced Motion

- **Location**: `src/style.css:2285-2291`
- **Description**: Spinner still runs at 0.01ms rather than being completely disabled

Action plan:

- [ ] Replace spinner with static "Loading..." text for reduced motion preference

### P4. UI Quality Audit — Low-Severity Issues

**Audit date: March 17, 2026**

#### Button Hover State Subtle

- **Location**: `src/style.css:653-655`
- **Description**: Button hover only changes opacity (0.9); weak feedback

Action plan:

- [ ] Enhance button hover states with background/border change

#### Toast Position May Overlap Actions Panel

- **Location**: `src/style.css:1037-1045`
- **Description**: Toasts at bottom-right may overlap actions panel on smaller screens

Action plan:

- [ ] Move toast container or adjust z-index for proper stacking

#### No Loading Skeleton States

- **Location**: `src/ui/emptyStates.js`, simulation job list
- **Description**: Loading shows spinner but no skeleton UI; causes layout shift

Action plan:

- [ ] Add skeleton placeholders for job list and results panels

#### Canvas Container No WebGL Fallback

- **Location**: `index.html:267-274`
- **Description**: 3D canvas has no fallback content for non-WebGL browsers

Action plan:

- [ ] Add fallback message inside canvas container for non-WebGL browsers

#### Section Collapse State

- **Location**: `src/style.css:376-444`
- **Description**: Collapsible sections use `<details>`; ensure `aria-expanded` synced if custom implementation used

Action plan:

- [ ] Verify native `<details>` accessibility; add `aria-expanded` if custom implementation

---

**Audit Summary (March 17, 2026):**

| Metric    | Count  |
| --------- | ------ |
| Critical  | 0      |
| High      | 5      |
| Medium    | 8      |
| Low       | 6      |
| **Total** | **19** |

**Overall Quality Score: A- (86/100)**

- Accessibility: B (75/100)
- Performance: A- (88/100)
- Theming: B+ (85/100)
- Responsive: B (80/100)
- Code Quality: A- (88/100)

**Positive Findings:**

- Excellent OKLCH design token organization
- Proper dark mode via `prefers-color-scheme`
- Reduced motion support present
- Semantic HTML with proper ARIA roles
- Skip link, accessible progress bar, toast notifications
- Three.js render optimization with `needsRender` flag

## Completed / Resolved

### P2. Restore Missing Load/Export Buttons in Job List — COMPLETE

**Resolved: March 17, 2026**

Restored the missing "Load" and "Export" buttons in the simulation job list that were accidentally removed during refactoring.

- File: `src/ui/simulation/jobActions.js` — `renderJobList()` function
- Event handlers in `events.js` (lines 90-96) were already wired
- Load button: `data-job-action="load-script"` (condition: `job.script` exists)
- Export button: `data-job-action="export"` (condition: `job.status === "complete"`)

### P1. Duplicate Function Definitions in scene.js — COMPLETE

**Resolved: March 17, 2026** (commit fa1c380)

Removed merge artifact duplicate code blocks from `src/app/scene.js`:

- Duplicate `zoom()`, `toggleCamera()`, `renderModel()` functions
- Duplicate event handlers and closing code
- 82 lines removed, file now 382 lines
- Skip-link CSS added for accessibility

### P1. Symmetry Performance — DISABLED (bempp-cl Limitation)

**Status: DISABLED — code preserved for future revisit**

Investigation complete (March 17, 2026). The image source method is blocked by bempp-cl 0.4.x singular quadrature limitation. When domain and test spaces live on different grids, `singular_assembler.py` returns a zero matrix for the singular part, causing ~8 dB SPL errors.

**What was implemented and preserved:**

- `_apply_symmetry_cut_yz()` in `waveguide_builder.py` — B-Rep geometry cut before tessellation
- `create_mirror_grid()` in `symmetry.py` — flip + reverse winding
- `_assemble_image_operators()` in `solve_optimized.py` — cross-grid operator assembly
- Observation frame projection for half models in `observation.py`
- Safety gate: `quadrants=1234` enforced in `simulation_runner.py`

**Performance potential (if upstream fixed):** 2.66x speedup, 2.96x DOF reduction at 700 elements (~4x at 4000 elements).

**Re-enable condition:** bempp-cl adds cross-grid singular quadrature support, or alternative BEM backend with half-space Green's function.

**Key diagnostic scripts:**

- `server/scripts/ab_test_symmetry.py`
- `server/scripts/diagnose_image_source.py`
- `server/scripts/benchmark_bem_symmetry.py`

### P2. Observation Distance Measurement Origin — COMPLETE

- `observation_origin` field in `PolarConfig` (values: `"mouth"` | `"throat"`)
- `infer_observation_frame()` selects origin based on parameter
- UI control in polar settings with tooltip

### P2. Solver Settings Audit — COMPLETE

- `enable_symmetry` toggle removed from UI
- All active settings audited end-to-end
- Tooltips added to all controls

### P2. Firefox Output Folder — COMPLETE

- `GET /api/workspace/path` endpoint
- `POST /api/workspace/open` endpoint (opens in OS file manager)
- Proper UI panel with path display and Finder button

### P2. OpenCL GPU Support — COMPLETE

- OS/arch fields in health endpoint
- Platform-specific setup instructions in Settings modal
- pocl devices correctly classified as CPU-only

### P2. Measurement Distance — COMPLETE

- Effective distance shown in View Results modal
- Clamping warning displayed when solver adjusts distance

### P2. Tessellation Architecture — COMPLETE

- Analysis documented: shared geometry representation not feasible
- Current architecture (duplicated but tested) is appropriate

### P3. Remove Simulation Jobs Refresh Button — COMPLETE

- Reduced to icon-only with lower visual weight

### P3. Symmetry Runtime Truth — COMPLETE

- ATH reference fixtures committed
- Regression coverage for symmetry eligibility
- Diagnostics lane for reference configs

### P4. Maintained Markdown Overhaul — COMPLETE

- All docs audited and rewritten for clarity
- Architecture parity verified
- TESTING.md test inventory updated

### P0. Enclosure BEM Simulation — RESOLVED (March 14, 2026)

`_build_enclosure_box` clamps `enc_depth` to at least `horn_length + 1mm`.

### P2. Safety Mechanisms in .msh Generation — RESOLVED

All three safety mechanisms audited and confirmed needed.

### Completed Items (March 11-15, 2026)

- P1. Remove Stale Local-Only Jobs From the Backend Feed
- P1. OCC Mesh Diagnostics Must Reflect The Backend Solve Mesh
- P1. Parameter Inventory, Naming, Hover Help, and Ordering
- P1. Settings Panel Completeness and Information Architecture
- P1. MSH File Import — Viewport Display and Simulation Workflow
- P1. Return to Parametric — Viewport Blank + MSH Import Naming
- P1. Enclosure Mesh Resolution — Edge Over-Refinement
- P2. Help Tooltip — Move from Button to Label Hover
- P2. Folder Workspace Discoverability and Export Routing
- P2. Geometry Diagnostics Instead of Numeric BEM Tag Diagnostics
- P2. Advanced Solver Controls and BEM Precision Scope
- P3. Directivity Map Section — Add Expand/Collapse
- P3. Pre-Existing Test Failures — 12 failures fixed
- P3. Simulation Job Feed Source-Badge Cleanup

Detailed history in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.

## Deferred Watchpoints

- **Cross-Platform Installation Hardening** — Large scope, NOT STARTED. Revisit when installer issues become blocking or CI coverage is needed. See archived plan for slicing strategy.
- The Gmsh export stack remains part of the active runtime until solve-mesh and export-artifact parity exists without it.
- Internal decomposition of `server/solver/solve_optimized.py` and `server/solver/waveguide_builder.py` stays deferred unless new feature work makes those files a delivery bottleneck.
- Internal decomposition of `server/services/job_runtime.py` stays deferred unless queueing, persistence, or multi-worker lifecycle requirements expand materially.

Re-open the backlog when:

- a new product or runtime requirement lands
- a deferred watchpoint becomes an active delivery bottleneck
- a regression or documentation drift needs tracked follow-through across multiple slices
