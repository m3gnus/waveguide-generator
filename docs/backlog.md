# Backlog

Last updated: March 18, 2026

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
- All 267 JS tests pass. Python tests pass.
- **Symmetry optimization DISABLED** — image source method blocked by bempp-cl 0.4.x singular quadrature limitation. Code preserved for future revisit if bempp-cl adds cross-grid singular quadrature support.
- **UI Quality Audit complete** — all P2/P3/P4 items resolved (March 17, 2026).

## Active Backlog

### P2. Design Quality — Distinctive Visual Identity & Hierarchy

The interface functions but lacks visual distinction and hierarchy. Current state: system fonts with no personality, flat action hierarchy (all buttons similar weight), 3D canvas feels like an afterthought, weak status indicators, and passive empty states. The design says "functional prototype" not "professional acoustic engineering tool." This undermines user confidence in a precision engineering domain.

**Implementation notes**:

- Files: `src/style.css`, `index.html`, `src/ui/*.js`
- Related: completed P2 UI Quality Audit (accessibility/theming) — this extends into visual design quality
- Skills available: `/normalize`, `/distill`, `/bolder`, `/clarify`, `/onboard`

Action plan:

- [x] Typography: Replace system fonts with distinctive UI font (consider IBM Plex Sans, Space Grotesk, or DM Sans); pair with non-default monospace (JetBrains Mono, Fira Code)
- [x] Action hierarchy: Create primary/secondary/tertiary button styles; make "Start BEM Simulation" visually dominant
- [x] Canvas presence: Add subtle framing (vignette, intentional grid styling, integrated controls)
- [ ] Status indicators: Make connection/simulation status more prominent with bolder color, subtle glow/pulse
- [ ] Empty states: Rewrite to explain what will appear and why it matters, guide users toward action

**Research findings**:

- Anti-patterns check: PASS — not typical AI-generated slop (warm palette, no glassmorphism/neon, light mode default)
- Current aesthetic: competent but forgettable; rounded corners + subtle shadows everywhere
- Opportunity: commit to a distinctive direction matching the domain (precision engineering, acoustic science)
- Question to resolve: What does "precision" look like for this audience? (Swiss clinical, industrial brutalist, refined luxury engineering?)

---

Re-open when:

- A new product or runtime requirement lands
- A deferred watchpoint becomes an active delivery bottleneck
- A regression or documentation drift needs tracked follow-through

## Deferred Watchpoints

### Internationalization (i18n) Infrastructure

**Status:** DEFERRED — large scope, not blocking current release

- **Location:** Entire frontend codebase
- **Severity:** High (when needed)
- **Description:** All UI strings hard-coded in English; no i18n library present
- **Impact:** Cannot localize for non-English users

Action plan (when activated):

- [ ] Decide on i18n approach (library vs. message file extraction)
- [ ] Extract UI strings to messages file
- [ ] Implement `Intl.MessageFormat` or similar

### Cross-Platform Installation Hardening

**Status:** NOT STARTED — revisit when installer issues become blocking or CI coverage needed

See archived plan for slicing strategy.

### Other Deferred Items

- The Gmsh export stack remains part of the active runtime until solve-mesh and export-artifact parity exists without it.
- Internal decomposition of `server/solver/solve_optimized.py` and `server/solver/waveguide_builder.py` stays deferred unless new feature work makes those files a delivery bottleneck.
- Internal decomposition of `server/services/job_runtime.py` stays deferred unless queueing, persistence, or multi-worker lifecycle requirements expand materially.

## Completed / Resolved

### P2. UI Quality Audit — Accessibility & Theming (March 17, 2026)

#### Touch Targets Below Minimum Size — COMPLETE

- **Location:** `src/style.css:1060-1070`
- **Fix:** Increased viewer control buttons to 44×44px minimum

#### Low Contrast Muted Text — COMPLETE

- **Location:** `src/style.css:16, 83`
- **Fix:** Darkened `--text-muted` to `oklch(48%)` (light) / `oklch(50%)` (dark)

#### Hard-coded Scene Colors in JavaScript — COMPLETE

- **Location:** `src/viewer/index.js:63-82`, `src/app/scene.js:62-69`
- **Fix:** Read CSS custom properties at runtime via `getComputedStyle()`

### P3. UI Quality Audit — Medium-Severity Issues (March 17, 2026)

#### Viewer Controls Missing aria-label — COMPLETE

- **Location:** `index.html:265-282`
- **Fix:** Added `aria-label` attributes to all viewer control buttons

#### Form Field Error Recovery — COMPLETE

- **Location:** `src/ui/inputValidation.js:280-306`
- **Fix:** Added `aria-describedby` linking inputs to their error messages

#### Modal Focus Management — COMPLETE

- **Location:** `src/ui/focusTrap.js`, `src/ui/feedback.js:246`
- **Fix:** Implemented focus trapping and initial focus for modals

#### Progress Bar Announcements — COMPLETE

- **Location:** `src/ui/simulation/progressUi.js:161-196`
- **Fix:** Throttled aria-live updates to 10% milestones

#### Formula Info Panel Not a Dialog — COMPLETE

- **Location:** `src/ui/paramPanel.js:377-460`
- **Fix:** Converted to proper dialog with `role="dialog"`, `aria-modal`, focus trap, and Escape key handler

#### Status Dot Color-Only Indication — COMPLETE

- **Location:** `src/style.css:750-778`
- **Fix:** Added symbol differentiation (✓ for connected, ✗ for disconnected) alongside color

#### Skip Link Styling — COMPLETE

- **Location:** `index.html:19`, `src/style.css:2355-2374`
- **Fix:** Skip link visible on focus with proper styling (done in commit fa1c380)

#### Animation with Reduced Motion — COMPLETE

- **Location:** `src/style.css:2328-2347`
- **Fix:** Replaced spinner with static "Loading..." text for reduced motion preference

### P4. UI Quality Audit — Low-Severity Issues (March 17, 2026)

#### Button Hover State Subtle — COMPLETE

- **Location:** `src/style.css:678-682`
- **Fix:** Enhanced with `transform`, `box-shadow`, and `brightness`

#### Toast Position May Overlap Actions Panel — COMPLETE

- **Location:** `src/style.css:1080-1088`
- **Fix:** Moved toast container to bottom-left

#### No Loading Skeleton States — COMPLETE

- **Location:** `src/ui/emptyStates.js:245-291`
- **Fix:** Added skeleton placeholders for job list and results panels

#### Canvas Container No WebGL Fallback — COMPLETE

- **Location:** `index.html:260-264`
- **Fix:** Added fallback message for non-WebGL browsers

#### Section Collapse State — VERIFIED COMPLETE

- **Location:** `index.html:96`
- **Fix:** Native `<details>` elements used with built-in accessibility

### P3. Replace Symmetry Policy with Solve Statistics in Results View — COMPLETE (March 17, 2026)

- Created `renderSolveStatsSummary()` function in `src/ui/simulation/results.js`
- Displays: solve time, frequency range/count, mesh complexity, measurement distance + origin

### P3. Remove Symmetry Text from Job List Entries — COMPLETE (March 17, 2026)

- Removed `getSymmetrySummaryLine()` function from `jobActions.js`
- Updated test assertions in `tests/simulation-flow.test.js`

### P2. Restore Missing Load/Export Buttons in Job List — COMPLETE (March 17, 2026)

- File: `src/ui/simulation/jobActions.js` — `renderJobList()` function
- Load button: `data-job-action="load-script"` (condition: `job.script` exists)
- Export button: `data-job-action="export"` (condition: `job.status === "complete"`)

### P1. Duplicate Function Definitions in scene.js — COMPLETE (March 17, 2026)

- Removed merge artifact duplicate code blocks from `src/app/scene.js`
- 82 lines removed, file now 382 lines

### P1. Symmetry Performance — DISABLED (bempp-cl Limitation)

**Status: DISABLED — code preserved for future revisit**

Investigation complete (March 17, 2026). The image source method is blocked by bempp-cl 0.4.x singular quadrature limitation.

**Performance potential (if upstream fixed):** 2.66x speedup, 2.96x DOF reduction at 700 elements (~4x at 4000 elements).

**Re-enable condition:** bempp-cl adds cross-grid singular quadrature support, or alternative BEM backend with half-space Green's function.

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

---

**Audit Summary (March 17, 2026):**

| Metric    | Count |
| --------- | ----- |
| Critical  | 0     |
| High      | 0     |
| Medium    | 0     |
| Low       | 0     |
| **Total** | **0** |

**Overall Quality Score: A (92/100)**

- Accessibility: A- (90/100)
- Performance: A- (88/100)
- Theming: A (92/100)
- Responsive: B+ (88/100)
- Code Quality: A (92/100)

**Positive Findings:**

- Excellent OKLCH design token organization
- Proper dark mode via `prefers-color-scheme`
- Reduced motion support present
- Semantic HTML with proper ARIA roles
- Skip link, accessible progress bar, toast notifications
- Three.js render optimization with `needsRender` flag
