# Claude-session request audit — 2026-08-04

This audit re-read the user-authored messages in the HornLab Claude sessions
from August 1–4 and checked the resulting repositories rather than relying on
the sessions' closing summaries. Agent-proposed ideas are kept separate from
requests Magnus actually made.

## Requested outcomes and current evidence

| Request group | Current disposition | Evidence |
|---|---|---|
| Keep Metal as the preferred backend; compare Metal and Bempp honestly | Closed | Capability-based `auto` selection; canonical `exp(+ikr)` phase metadata and Metal/Bempp parity tests in v1, v2, and `hornlab-plots` |
| Arbitrary H/V spline profiles, controllable cross-section transition, and prevention of accidental S/fold behavior | Closed | FREEFORM H/V profiles and stations; solved tangent speed; physical overshoot tolerance; legacy migration; normalized axial `t`; editable/paste/convert UI; mesher and UI parity tests |
| Make FREEFORM less complex and non-destructive when length changes | Closed | Removed tangent scales, strength, and overshoot policy; length is a multiplier rather than an anchor-deleting edit |
| Modern responsive WG v2, `.cfg`/`.txt`, low-latency authoritative geometry, light and dark themes | Closed | React/Dockview v2, binary preview protocol, one HornLab mesher path, config corpus, theme persistence, cold-start and browser tests |
| Retire the duplicate geometry implementation and obsolete Bianco-era bridges | Closed | Legacy geometry and both retired bridges removed in their owning repositories; constellation records the reviewed state |
| Carry the useful v1 inputs into v2 and verify them in the browser | Closed | `V1-INPUTS-AUDIT.md` remediation, parameter inventory tests, solve/polar/results/export/viewer preference surfaces, browser walkthrough |
| Fix designs not refreshing, implicit morph extents, missing wall/body choice, the axis gizmo, and disorganized geometry/simulation controls | Closed | Preview revision/error handling, implicit extents, explicit outer-body control, interactive orientation gizmo, compact Geometry/Simulation dock tabs |
| Resolve R-OSSE + enclosure behavior from evidence | Closed | Deliberately supported by the authoritative HornLab mesher; the obsolete v1-JS-engine restriction was not copied |
| Fix the invisible rounded mouth rim, clarify surface colors, and provide normals/curvature inspection | Closed | Profile-exit rim orientation, semantic surface roles, normals mode, analytic curvature transport and rendering |
| Make directivity visible, remove dead default result cards, and allow 1/2/3/4/6 layouts with per-card close controls | Closed | Directivity regression test, populated defaults, persisted chart selection, count layouts, close/add controls |
| Default to automatic symmetry and make CircSym a Metal fast path rather than a backend | Closed | Geometry-derived symmetry, `axisymmetric-meridian` solve path, rejection metadata, native auto-vs-full-3D qualification |
| Fix morph/stale UX, dark-text contrast, panel chrome, the decorative viewport-edge glitch, and top search/Cmd-K | Closed | Refresh/Retry and explanatory status, morph config fix, contrast tokens, compact dock tabs, ellipse removal, searchable command palette with focus restoration |
| Restore the high-quality HornLab plot appearance in WG v2 | Closed by this audit | Live result cards now consume the pinned `hornlab-plots` PNG renderers for response, DI, impedance, beam shape, and directivity. ECharts remains only as a resilient loading/error fallback. |

## Explicitly not outstanding

- Windows packaging was explicitly deferred by Magnus during the v2 build; it
  is not counted as an unfinished implementation request.
- Magnus explicitly said not to continue work in the retiring v1 UI. V1 was
  touched only where a shared solver/phase contract required compatibility.
- Quarter symmetry was described as preferred but not to be forced. Automatic
  geometry-derived symmetry implements that instruction.
- Autosave, named manual snapshots, generic section-curve overlays, and layout
  preset packs appear in early planning inventories, but were not direct user
  requests in the audited sessions. Job design snapshots and persisted/resettable
  Dockview layouts remain implemented.
- WG v2 and `hornlab-constellation` have no Git remotes. Their verified commits
  are therefore local by repository configuration, not uncommitted work.

## Plot-pipeline correction

Before this audit, the v2 results panel used a separate ECharts visual design
while PNG export used `hornlab-plots`. That satisfied data and export contracts
but not the requested visual parity. The live panel now calls the same pinned
canonical renderer used for export. This restores its interpolation, contours,
color maps, axes, typography, spacing, themes, and comparison styling without
maintaining a second approximation in TypeScript.
