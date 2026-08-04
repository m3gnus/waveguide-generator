# V1 → v2 traceability table

This is the Phase-1 living inventory. Each v1-behavior cell is backed by a v1 `file:line`; rows marked OPEN make no behavioral assertion beyond the named gap. “Required” means parity is not yet evidenced here, not that v2 lacks an implementation. Phase assignments follow the plan's P1 contracts, P2 core, P3 mesh/jobs/exports, P4 engines/results, P5 advanced-family UI, and P6 platform/cutover split.

Owners used here are target domains: `design-schema`, `design-ui`, `mesher`, `jobs`, `results`, `exports`, `viewer`, `workspace`, and `platform`. `TBD` is intentional when ownership is not obvious.

## Post-build reconciliation — 2026-08-04

The table above was written during Phase 1, before the build. It is a planning inventory, not a completion ledger: a `Required` cell states that parity is owed, never that v2 lacks the implementation. Completion evidence lives elsewhere — [V1-INPUTS-AUDIT.md](V1-INPUTS-AUDIT.md) for the input surface, [LUNA-TRIAGE.md](LUNA-TRIAGE.md) and [SOL-FINAL-REVIEW.md](SOL-FINAL-REVIEW.md) for the review rounds, and the suites themselves.

This pass re-checked only the twelve rows that asserted an actual gap (`OPEN`). Four are now resolved by implementation, one by evidence:

- **D027 / N001 — grouped-drag undo transactions.** Implemented; drag history is finalized before a family switch or document load, and a document replacement opens a new undo epoch (`frontend/src/stores/design.ts`, tested in `design.test.ts:59` and `:70`).
- **N003 — WebSocket job streaming.** Implemented over the snapshot+cursor protocol (`frontend/src/api/jobsSocket.ts`, `frontend/src/jobs/jobsSocket.test.ts`), with gap recovery by resume added in the Sol fix round.
- **N004 — persisted dockview layout.** Implemented; the layout is serialized to `localStorage` and reseeded on load, with a corrupt-payload reset (`frontend/src/shell/Workspace.tsx:116-128`, tested in `Workspace.test.tsx`).
- **P011 / Q007 — instance locking.** The v1-evidence question is settled negatively: v1 has no application-level single-instance lock (its only locks are the gmsh worker's and a matplotlib render lock), so there is no v1 contract to match. V2 implements one as new behavior in `server/platform/instance.py` (`fcntl`), tested in `server/tests/test_platform_luna.py`.

Still genuinely open, unchanged by the build:

- **V013 / Q006 — generic section-curve overlays.** No owning v1 contract was ever located. Do not claim parity.
- **N002 — autosave.** Not implemented in v2; no v1 contract either.
- **N006 / N007 / N008 — command palette, named snapshots, layout presets.** Accepted post-cutover deferrals.
- **T001 — the six-tab settings modal.** V2 has no consolidated settings surface; the equivalent controls live in panel-inline preference strips plus a viewer-preferences panel (`frontend/src/prefs/`, `frontend/src/viewerprefs/`). This is an architectural difference to confirm or close deliberately, not an accidental omission.

## §3 seed inventory

| ID | v1 behavior / route / control | Evidence (v1) | v2 owner | Phase | Test / manual script | Compat | Accepted deferral / workaround |
|---|---|---|---|---|---|---|---|
| D001 | R-OSSE family with its own profile parameter set | `src/ui/parameterInventory.js:17-20` | design-schema | P2 | schema + oracle payload fixture | Required | None |
| D002 | OSSE family with its own profile parameter set | `src/ui/parameterInventory.js:21-25` | design-schema | P2 | schema + oracle payload fixture | Required | None |
| D003 | ICW family, including coverage, termination, and depth controls | `src/ui/parameterInventory.js:27-42` | design-schema | P5 | schema + ICW mesh corpus | Required | None |
| D004 | FREEFORM has independent horizontal/vertical editable profile data | `src/ui/parameterInventory.js:45-65` | design-schema | P5 | schema + FREEFORM session E2E | Required | None |
| D005 | FREEFORM mounts an interactive H/V profile editor | `src/ui/freeformProfileEditor.js:153-225` | design-ui | P5 | create/edit/undo E2E | Required | None |
| D006 | FREEFORM supports cross-section stations and a sampled cross-section inset | `src/ui/freeformCrossSectionInset.js:245-282` | design-ui | P5 | station scrubber visual test | Required | None |
| D007 | FREEFORM point paste has preview, apply, keep-length, and cancel actions | `src/ui/paramPanel.js:921-979` | design-ui | P5 | paste parser/UI E2E | Required | None |
| D008 | Switching to FREEFORM offers blank or converted current design | `src/ui/feedback.js:198-318` | design-ui | P5 | conversion dialog E2E | Required | None |
| D009 | FREEFORM validation details map back to a targeted UI error | `src/ui/freeformErrorMapping.js:34-96` | design-ui | P5 | invalid corpus + target mapping | Required | None |
| D010 | Morph circular↔target controls include target, dimensions, corner, rate, fixed, and shrinkage | `src/ui/parameterInventory.js:83-102` | design-schema | P5 | morph payload + mesh fixtures | Required | None |
| D011 | ICW rollback exposes termination angle and target depth | `src/config/schema.js:300-325` | design-schema | P5 | rollback corpus incl. infeasible | Required | None |
| D012 | ICW coverage mode exposes angle and plateau start/end | `src/config/schema.js:249-280` | design-schema | P5 | coverage payload/geometry fixtures | Required | None |
| D013 | Source shape/radius/curvature/velocity are explicit controls | `src/ui/parameterInventory.js:202-213` | design-schema | P2 | source payload golden | Required | None |
| D014 | Source velocity enum maps 1 to normal velocity and 2 to axial rigid-piston velocity | `server/solver/mesher_adapter.py:88-100` | design-schema | P1 | science contract unit test | Required | None |
| D015 | Quadrant selection is part of solve/export mesh controls | `src/ui/parameterInventory.js:227-237` | design-schema | P2 | 1/2/4-quadrant payload fixtures | Required | None |
| D016 | Wall thickness and enclosure depth/edge/clearance are design controls | `src/ui/parameterInventory.js:104-127` | design-schema | P5 | enclosure topology matrix | Required | None |
| D017 | Infinite-baffle versus free-standing behavior is encoded by simulation type | `src/solver/index.js:62-62` | design-schema | P5 | IB/full-3D/CircSym matrix | Required | None |
| D018 | CircSym is an explicit/auto solver mode and requires Metal when explicit | `src/config/schema.js:976-991`; `server/api/routes_simulation.py:110-118` | results | P4 | eligibility/backend matrix | Required | None |
| D019 | Viewport sampling controls are distinct from solve-mesh controls | `src/ui/parameterInventory.js:162-179`; `src/ui/parameterInventory.js:215-243` | design-ui | P2 | control-to-payload unit tests | Required | None |
| D020 | ATH Z-map points are imported and preserved as a mesh sampling mode | `src/config/index.js:67-68`; `src/geometry/params.js:131-131` | design-schema | P1 | grammar round trip | Required | None |
| D021 | Solve mesh safeguards expose `maxTriangles` and `allowLargeMesh` | `src/ui/parameterInventory.js:227-237` | design-schema | P3 | limit/rejection tests | Required | None |
| D022 | Config import validates known FREEFORM keys/blocks | `src/config/index.js:214-225` | design-schema | P1 | real-library corpus | Required | None |
| D023 | Parameter panels are generated from a centralized section/group inventory | `src/ui/parameterInventory.js:1-246` | design-ui | P2/P5 | inventory completeness test | Required | None |
| D024 | Parameter sections carry descriptions/help and persist collapsed state | `src/ui/paramPanel.js:387-452` | design-ui | P2 | accessibility + persistence UI test | Required | None |
| D025 | Undo/redo keeps a maximum 50-state history and clears redo on a new action | `src/state.js:114-120`; `src/state.js:215-220` | design-schema | P2 | 51-edit history unit test | Required | None |
| D026 | Undo and redo restore full states, persist them, and emit contextual events | `src/state.js:223-252` | design-schema | P2 | state/event unit test | Required | None |
| D027 | Grouped drag undo transactions do not exist in v1 | RESOLVED 2026-08-04 — new-v2 behavior; no v1 contract to cite | design-schema | P2 | `frontend/src/stores/design.test.ts:59,70` | New | None |
| D028 | Config export preserves an expression only when state still contains its raw string; evaluated numeric state loses the original expression | `src/export/mwgConfig.js:34-44` | design-schema | P1 | raw-string/evaluated round-trip fixtures | Required | None |
| D029 | Source contours accept a file path or inline-script expression and serialize as `Source.Contours` | `src/config/schema.js:929-934`; `src/export/mwgConfig.js:257-265` | design-schema | P1/P2 | source payload/config fixture | Required | None |
| D030 | Enclosure geometry includes separately sampled front and back roundover rings | `src/geometry/engine/mesh/enclosure.js:579-624` | mesher | P3/P5 | roundover topology fixtures | Required | None |
| D031 | Empty-state helpers cover no results/jobs/data, disconnected solver, export, timeout, server, and validation states with live-region markup | `src/ui/emptyStates.js:6-67`; `src/ui/emptyStates.js:75-101` | design-ui | P2–P5 | accessibility/content UI tests | Required | None |
| M001 | Full mesh build returns tagged Gmsh text, stats, and optional STL with progress handled by job/build orchestration | `server/api/routes_mesh.py:37-104` | mesher | P3 | full-build integration | Required | None |
| M002 | Viewport route returns point grids/rings without running Gmsh | `server/api/routes_mesh.py:184-222` | mesher | P0/P2 | latency + concurrency test | Required | None |
| M003 | Full-build stats are returned by the authoritative mesher | `server/api/routes_mesh.py:97-104` | mesher | P3 | stats golden fixture | Required | None |
| M004 | Metal open-edge guard policy depends on mesh topology | `server/solver/metal_solver.py:232-248` | mesher | P3 | bare/reduced/closed topology matrix | Required | None |
| M005 | Near-degenerate enclosure seams have regression coverage | `server/tests/test_enclosure_mesh_closure.py:71-79` | mesher | P0/P3 | port sliver fixture | Required | None |
| J001 | Solver backend selection accepts auto, Metal, and BEMPP and reports capability errors | `server/api/routes_simulation.py:94-178` | jobs | P4 | backend availability matrix | Required | None |
| J002 | CircSym reduces eligible circular solves and is rejected for BEMPP | `server/solver/axisymmetry.py:41-132` | jobs | P3/P4 | eligibility matrix | Required | None |
| J003 | Spherical sampling is optional and defaults off | `server/contracts/__init__.py:101-168` | results | P4 | default request fixture | Required | None |
| J004 | Job queue supports submit, status, list, stop, result, metadata, delete, and clear-failed operations | `server/api/routes_simulation.py:69-297` | jobs | P3 | API lifecycle E2E | Required | None |
| J005 | Ratings are persisted as task metadata | `server/api/routes_simulation.py:253-277` | jobs | P3 | metadata persistence E2E | Required | None |
| J006 | Load-job-script restores saved parameters and reframes the viewer | `src/ui/simulation/jobActions.js:615-635` | jobs | P3 | load snapshot E2E | Required | None |
| J007 | Rerun loads the script, best-effort deletes old job, then starts a new solve | `src/ui/simulation/jobActions.js:646-664` | jobs | P3 | rerun failed/cancelled E2E | Required | None |
| J008 | Task sorting supports newest, highest-rated, and label A-Z; rating filter supports 0–5 | `src/ui/settings/modal.js:1156-1183` | jobs | P3 | sort/filter unit + UI | Required | None |
| J009 | Auto export runs on completion and records a completion marker | `src/ui/simulation/polling.js:183-227` | exports | P3 | reload/idempotency/crash E2E | Required | None |
| J010 | Automatic mesh download guards once per in-memory job and retries after failure | `src/ui/simulation/polling.js:111-123` | exports | P3 | polling retry test | Required | None |
| J011 | Export prefix/counter persist and counter advances on the first pending parameter change | `src/ui/fileOps.js:60-106`; `src/ui/fileOps.js:210-243` | exports | P3 | persistence/change grouping test | Required | None |
| J012 | Job storage supports startup-visible persisted rows and status filtering | `server/services/job_runtime.py:600-671` | jobs | P3 | restart recovery E2E | Required | None |
| R001 | Frequency-response plot includes on-axis SPL and optional phase overlay | `server/solver/charts.py:187-275` | results | P4 | chart golden image/data | Required | None |
| R002 | Directivity maps support H/V display variants | `src/ui/simulation/chartRequests.js:9-32` | results | P4 | H/V map fixtures | Required | None |
| R003 | Polar controls configure requested planes and diagonal angle | `src/ui/simulation/polarSettings.js:575-631` | results | P4 | polar request UI test | Required | None |
| R004 | Beamwidth is derived at the first -6 dB crossing | `server/solver/beam_shape.py:27-33`; `server/solver/beam_shape.py:64-85` | results | P4 | analytic pattern fixture | Required | None |
| R005 | Impedance is normalized complex specific impedance | `server/solver/result_mapping.py:185-200` | results | P4 | complex golden fixture | Required | None |
| R006 | Solver logs become structured warnings/partial-success diagnostics | `server/solver/result_mapping.py:202-249` | results | P4 | nonconvergence fixture | Required | None |
| R007 | Smoothing offers none plus ten non-none modes, including psychoacoustic and ERB | `src/ui/simulation/viewResults.js:64-84`; `src/results/smoothing.js:315-351` | results | P4 | golden arrays per mode | Required | None |
| R008 | Result metadata carries observation origin/distance and phase convention inputs | `server/solver/result_mapping.py:98-130`; `src/results/conventions.js:1-69` | results | P1/P4 | convention fixtures | Required | None |
| R009 | Partial success and per-frequency failures have dedicated diagnostic display | `src/ui/simulation/results.js:262-410` | results | P4 | partial sweep UI fixture | Required | None |
| R010 | Balloon has disabled, requested-missing, backend-unsupported, and available states | `server/solver/result_mapping.py:357-391` | results | P1/P4 | four-state mapper/UI test | Required | None |
| R011 | Balloon panel is 3-D and has a frequency slider | `src/ui/results/balloonPanel.js:190-203`; `src/ui/results/balloonPanel.js:286-300` | results | P4 | interaction/visual test | Required | None |
| R012 | Beam-shape derives a superellipse, beamwidths, aspect, and spherical DI | `server/solver/beam_shape.py:88-121`; `server/solver/beam_shape.py:236-312` | results | P4 | analytic balloon fixture | Required | None |
| R013 | Compare overlays keep active and reference frequency arrays separate | `src/ui/simulation/chartRequests.js:130-213` | results | P4 | mismatched-grid comparison | Required | None |
| R014 | Results support classic/split dock, panel count, arrangement, and remembered chart slots | `src/ui/settings/layoutSettings.js:5-46` | results | P4 | layout persistence E2E | Required | None |
| R015 | Server renders charts/directivity via hornlab-plots-compatible themes | `server/api/routes_misc.py:139-209` | results | P4 | theme/montage golden | Required | None |
| F001 | ATH-style config is parsed by named flat keys and blocks | `src/config/index.js:214-225` | design-schema | P1 | grammar corpus | Required | None |
| F002 | Config export writes ATH/MWG-style text as `.txt` | `src/modules/export/index.js:463-492` | exports | P3 | round-trip corpus | Required | None |
| F003 | Legacy job snapshot filename is `script.snapshot.mwg` | `src/ui/workspace/generationArtifacts.js:1-14` | workspace | P1/P3 | legacy fixture load | Required | None |
| F004 | STEP exports a full-domain acoustic inner surface only | `server/api/routes_mesh.py:107-181` | exports | P3 | CAD topology fixture | Required | None |
| F005 | STL is browser-side, bare horn, densified, and axis-rotated | `src/modules/export/index.js:358-382` | exports | P3 | STL semantic golden | Required | None |
| F006 | Profile/Fusion export writes two semicolon CSV files in centimetres | `src/export/profiles.js:1-51`; `src/modules/export/index.js:436-457` | exports | P3 | byte golden | Required | None |
| F007 | Stored `.msh` retrieval returns original text/plain artifact | `server/api/routes_simulation.py:230-240` | jobs | P3 | byte passthrough test | Required | None |
| F008 | Task folders and `waveguide.project.v1.json` are deterministic | `src/ui/workspace/generationArtifacts.js:1-14`; `src/ui/workspace/generationArtifacts.js:135-205` | workspace | P3 | manifest/folder fixture | Required | None |
| F009 | Workspace-write failure falls back to browser picker/download | `src/ui/fileOps.js:268-335` | exports | P3 | injected network/permission test | Required | None |
| F010 | Config import reads selected files as text, parses them, updates design state, and derives export fields from filename | `src/app/configImport.js:4-39` | design-schema | P1/P2 | `.cfg`/`.txt`/`.mwg` corpus E2E | Required | None |
| F011 | Browser mesh import parses text MSH, enters imported-mesh state, retains tags/names, and can return to parametric state | `src/app/events.js:180-235` | viewer | P3 | MSH import/return E2E | Required | None |
| V001 | Viewer has clay display mode | `src/viewer/index.js:31-39` | viewer | P2 | display-mode visual test | Required | None |
| V002 | Viewer has solid+wire display mode | `src/viewer/index.js:31-39` | viewer | P2 | display-mode visual test | Required | None |
| V003 | Viewer has shaded+edges display mode | `src/viewer/index.js:31-39` | viewer | P2 | display-mode visual test | Required | None |
| V004 | Viewer has wireframe display mode | `src/viewer/index.js:31-39` | viewer | P2 | display-mode visual test | Required | None |
| V005 | Viewer has x-ray display mode | `src/viewer/index.js:31-39` | viewer | P2 | display-mode visual test | Required | None |
| V006 | Viewer has zebra display mode | `src/viewer/index.js:31-39` | viewer | P2 | display-mode visual test | Required | None |
| V007 | Viewer has curvature display mode | `src/viewer/index.js:31-39` | viewer | P2 | display-mode visual test | Required | None |
| V008 | Startup camera supports perspective and orthographic modes | `src/ui/settings/modal.js:770-820` | viewer | P2 | restart/startup-mode E2E | Required | None |
| V009 | Perspective camera uses FOV 25, near .1, far 10000, position (600,600,600) | `src/viewer/index.js:98-103` | viewer | P2 | camera snapshot test | Required | None |
| V010 | Orthographic camera uses size 300, near .1, far 10000 | `src/viewer/index.js:105-107` | viewer | P2 | camera snapshot test | Required | None |
| V011 | FREEFORM profile editor renders inflection overlays | `src/ui/freeformProfileEditor.js:617-617` | viewer | P5 | overlay visual test | Required | None |
| V012 | Viewport shows “Stale — fix errors to rebuild” when geometry cannot refresh | `src/app/scene.js:67-72` | viewer | P2 | invalid-edit E2E | Required | None |
| V013 | Generic section-curve overlay contract is not verified in bounded v1 sources | OPEN — need owning renderer and toggle evidence | viewer | TBD | inventory follow-up | OPEN | Do not claim parity until located |
| P001 | Workspace current path can be read | `server/api/routes_misc.py:395-399` | workspace | P1/P6 | API test | Required | None |
| P002 | Native workspace folder selection supports macOS, Windows, Linux fallbacks | `server/api/routes_misc.py:402-484` | workspace | P6 | packaged OS matrix | Required | None |
| P003 | Workspace can be opened in the OS file manager | `server/api/routes_misc.py:487-508` | workspace | P6 | packaged OS smoke | Required | None |
| P004 | Health response exposes solver, mesher, dependency-doctor, and capability status | `server/api/routes_misc.py:92-128` | platform | P1/P6 | health schema fixture | Required | None |
| P005 | Update checker is exposed by backend and system settings | `server/api/routes_misc.py:131-136`; `src/ui/settings/modal.js:1480-1496` | platform | P6 | mocked remote status test | Required | None |
| P006 | Backend startup reports blocked features while frontend remains usable | `scripts/start-all.js:108-132` | platform | P1/P6 | missing-backend launcher test | Required | None |
| P007 | Start-all probes backend health and records startup status | `scripts/start-all.js:60-88` | platform | P1/P6 | launcher lifecycle test | Required | None |
| P008 | Runtime doctor reports Metal/BEMPP and dependency readiness | `server/services/runtime_preflight.py:67-125` | platform | P1/P6 | doctor golden JSON | Required | None |
| P009 | Logs can be filtered by agent/category/event/time/session and exported | `src/logging/queries.js:3-32`; `src/logging/queries.js:92-93` | platform | P6 | query/export unit test | Required | None |
| P010 | Port-collision startup failure receives tailored guidance | `scripts/backend-startup-status.js:53-83` | platform | P1/P6 | occupied-port launcher test | Required | None |
| P011 | Instance locking behavior is not verified in bounded v1 sources | RESOLVED 2026-08-04 — v1 has no app-level instance lock (gmsh-worker and matplotlib locks only); v2 adds one in `server/platform/instance.py` | platform | P1/P6 | `server/tests/test_platform_luna.py` | New | None |
| P012 | Start-all forwards termination to backend/frontend processes | `scripts/start-all.js:95-96`; `scripts/start-all.js:142-168` | platform | P1/P6 | signal/shutdown test | Required | None |
| P013 | Installers and launchers select a backend interpreter through shared priority rules | `server/README.md:35-48`; `server/start.sh:73-116` | platform | P1/P6 | clean install matrix | Required | None |
| P014 | Runtime capability helper maps Python, Gmsh, mesher, Metal, and BEMPP readiness to feature-specific guidance | `src/ui/runtimeCapabilities.js:110-175` | platform | P6 | degraded-capability UI matrix | Required | None |
| N001 | Grouped-drag undo transactions | RESOLVED 2026-08-04 — new-v2 behavior; no v1 contract | design-schema | P2 | `frontend/src/stores/design.test.ts:59,70` | New | None |
| N002 | Autosave | OPEN — not implemented in v2; no v1 contract either | workspace | TBD | crash/restart E2E | New | None |
| N003 | WebSocket job streaming | RESOLVED 2026-08-04 — snapshot+cursor protocol with resume-based gap recovery | jobs | P1/P3 | `frontend/src/jobs/jobsSocket.test.ts` | New | None |
| N004 | Persisted dockview layout | RESOLVED 2026-08-04 — `localStorage` serialize/reseed with corrupt-payload reset (`frontend/src/shell/Workspace.tsx:116-128`); v1 stored a simpler layout object (`src/ui/settings/layoutSettings.js:31-46`) | results | P4 | `frontend/src/shell/Workspace.test.tsx` | New | None |
| N005 | Scene colors follow `prefers-color-scheme`; an explicit dark/light v2 theme choice is new | `src/viewer/index.js:47-58` | viewer | P6 | theme persistence/visual test | New | None |
| N006 | Command palette | OPEN — post-cutover behavior; no v1 contract | TBD | Post | acceptance test TBD | Deferred | No v1 workaround required |
| N007 | Named snapshots | OPEN — post-cutover behavior; v1 has job script snapshots (`src/ui/workspace/generationArtifacts.js:1-14`) | workspace | Post | acceptance test TBD | Deferred | Retain the v1-compatible job snapshot path |
| N008 | Layout presets | OPEN — post-cutover behavior; v1 persists individual layout choices (`src/ui/settings/layoutSettings.js:31-46`) | results | Post | acceptance test TBD | Deferred | Retain individual layout persistence |

## Parameter-control inventory

Each row below freezes control presence and grouping; value/default/validation details remain schema tests. The v1 inventory says viewport controls affect responsiveness rather than solve mesh (`src/ui/parameterInventory.js:162-179`) and solve/export controls feed backend mesh behavior (`src/ui/parameterInventory.js:215-243`).

| ID | v1 behavior / route / control | Evidence (v1) | v2 owner | Phase | Test / manual script | Compat | Accepted deferral / workaround |
|---|---|---|---|---|---|---|---|
| C001 | R-OSSE `scale` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C002 | R-OSSE `R` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C003 | R-OSSE `a` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C004 | R-OSSE `a0` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C005 | R-OSSE `r0` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C006 | R-OSSE `k` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C007 | R-OSSE `m` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C008 | R-OSSE `b` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C009 | R-OSSE `r` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C010 | R-OSSE `q` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C011 | R-OSSE `tmax` control | `src/ui/parameterInventory.js:17-20` | design-ui | P2 | generated inventory test | Required | None |
| C012 | OSSE `scale` control | `src/ui/parameterInventory.js:21-25` | design-ui | P2 | generated inventory test | Required | None |
| C013 | OSSE `L` control | `src/ui/parameterInventory.js:21-25` | design-ui | P2 | generated inventory test | Required | None |
| C014 | OSSE `a` control | `src/ui/parameterInventory.js:21-25` | design-ui | P2 | generated inventory test | Required | None |
| C015 | OSSE `a0` control | `src/ui/parameterInventory.js:21-25` | design-ui | P2 | generated inventory test | Required | None |
| C016 | OSSE `r0` control | `src/ui/parameterInventory.js:21-25` | design-ui | P2 | generated inventory test | Required | None |
| C017 | OSSE `k` control | `src/ui/parameterInventory.js:21-25` | design-ui | P2 | generated inventory test | Required | None |
| C018 | OSSE `s` control | `src/ui/parameterInventory.js:21-25` | design-ui | P2 | generated inventory test | Required | None |
| C019 | OSSE `n` control | `src/ui/parameterInventory.js:21-25` | design-ui | P2 | generated inventory test | Required | None |
| C020 | OSSE `q` control | `src/ui/parameterInventory.js:21-25` | design-ui | P2 | generated inventory test | Required | None |
| C021 | OSSE `h` control | `src/ui/parameterInventory.js:21-25` | design-ui | P2 | generated inventory test | Required | None |
| C022 | ICW `scale` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C023 | ICW `r0` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C024 | ICW `a0` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C025 | ICW `L` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C026 | ICW `R` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C027 | ICW `coverage_angle` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C028 | ICW `hold_start` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C029 | ICW `hold_end` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C030 | ICW `n_coeff` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C031 | ICW `termination` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C032 | ICW `theta1_deg` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C033 | ICW `depth` control | `src/ui/parameterInventory.js:27-42` | design-ui | P5 | generated inventory test | Required | None |
| C034 | FREEFORM `scale` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C035 | FREEFORM `length` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C036 | FREEFORM `throatRadius` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C037 | FREEFORM `throatAngle` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C038 | FREEFORM `mouthRadiusH` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C039 | FREEFORM `mouthAngleH` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C040 | FREEFORM `interiorH` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C041 | FREEFORM `throatTangentScaleH` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C042 | FREEFORM `mouthTangentScaleH` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C043 | FREEFORM `mouthRadiusV` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C044 | FREEFORM `mouthAngleV` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C045 | FREEFORM `interiorV` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C046 | FREEFORM `throatTangentScaleV` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C047 | FREEFORM `mouthTangentScaleV` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C048 | FREEFORM `crossSections` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C049 | FREEFORM `overshootPolicy` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C050 | FREEFORM `inflectionPolicy` control | `src/ui/parameterInventory.js:45-65` | design-ui | P5 | generated inventory test | Required | None |
| C051 | `throatExtAngle` control for OSSE families | `src/ui/parameterInventory.js:69-81` | design-ui | P2 | generated inventory test | Required | None |
| C052 | `throatExtLength` control for OSSE families | `src/ui/parameterInventory.js:69-81` | design-ui | P2 | generated inventory test | Required | None |
| C053 | `slotLength` control for OSSE families | `src/ui/parameterInventory.js:69-81` | design-ui | P2 | generated inventory test | Required | None |
| C054 | `morphTarget` control | `src/ui/parameterInventory.js:83-102` | design-ui | P5 | generated inventory test | Required | None |
| C055 | `morphWidth` control | `src/ui/parameterInventory.js:83-102` | design-ui | P5 | generated inventory test | Required | None |
| C056 | `morphHeight` control | `src/ui/parameterInventory.js:83-102` | design-ui | P5 | generated inventory test | Required | None |
| C057 | `morphCorner` control | `src/ui/parameterInventory.js:83-102` | design-ui | P5 | generated inventory test | Required | None |
| C058 | `morphRate` control | `src/ui/parameterInventory.js:83-102` | design-ui | P5 | generated inventory test | Required | None |
| C059 | `morphFixed` control | `src/ui/parameterInventory.js:83-102` | design-ui | P5 | generated inventory test | Required | None |
| C060 | `morphAllowShrinkage` control | `src/ui/parameterInventory.js:83-102` | design-ui | P5 | generated inventory test | Required | None |
| C061 | `wallThickness` control | `src/ui/parameterInventory.js:104-127` | design-ui | P5 | generated inventory test | Required | None |
| C062 | `encDepth` control | `src/ui/parameterInventory.js:104-127` | design-ui | P5 | generated inventory test | Required | None |
| C063 | `encEdge` control | `src/ui/parameterInventory.js:104-127` | design-ui | P5 | generated inventory test | Required | None |
| C064 | `encEdgeType` control | `src/ui/parameterInventory.js:104-127` | design-ui | P5 | generated inventory test | Required | None |
| C065 | `encSpaceL` control | `src/ui/parameterInventory.js:104-127` | design-ui | P5 | generated inventory test | Required | None |
| C066 | `encSpaceT` control | `src/ui/parameterInventory.js:104-127` | design-ui | P5 | generated inventory test | Required | None |
| C067 | `encSpaceR` control | `src/ui/parameterInventory.js:104-127` | design-ui | P5 | generated inventory test | Required | None |
| C068 | `encSpaceB` control | `src/ui/parameterInventory.js:104-127` | design-ui | P5 | generated inventory test | Required | None |
| C069 | OSSE guiding-curve group has throat profile and rotation controls | `src/ui/parameterInventory.js:129-159` | design-ui | P5 | generated inventory test | Required | None |
| C070 | OSSE guiding-curve group has type/distance/width/aspect controls | `src/ui/parameterInventory.js:139-146` | design-ui | P5 | generated inventory test | Required | None |
| C071 | OSSE guiding-curve group has superellipse coefficient controls | `src/ui/parameterInventory.js:146-155` | design-ui | P5 | generated inventory test | Required | None |
| C072 | OSSE guiding-curve group has rotation and circular-arc controls | `src/ui/parameterInventory.js:155-158` | design-ui | P5 | generated inventory test | Required | None |
| C073 | `angularSegments` viewport control | `src/ui/parameterInventory.js:162-179` | design-ui | P2 | generated inventory test | Required | None |
| C074 | `lengthSegments` viewport control | `src/ui/parameterInventory.js:162-179` | design-ui | P2 | generated inventory test | Required | None |
| C075 | `cornerSegments` viewport control | `src/ui/parameterInventory.js:162-179` | design-ui | P2 | generated inventory test | Required | None |
| C076 | `throatSegments` viewport control | `src/ui/parameterInventory.js:162-179` | design-ui | P2 | generated inventory test | Required | None |
| C077 | `throatSliceDensity` viewport control | `src/ui/parameterInventory.js:162-179` | design-ui | P2 | generated inventory test | Required | None |
| C078 | `freqStart` sweep control | `src/ui/parameterInventory.js:182-194` | design-ui | P3 | generated inventory test | Required | None |
| C079 | `freqEnd` sweep control | `src/ui/parameterInventory.js:182-194` | design-ui | P3 | generated inventory test | Required | None |
| C080 | `numFreqs` sweep control | `src/ui/parameterInventory.js:182-194` | design-ui | P3 | generated inventory test | Required | None |
| C081 | Directivity-map control section is owned by polar settings | `src/ui/parameterInventory.js:196-201` | results | P4 | generated inventory test | Required | None |
| C082 | `sourceShape` control | `src/ui/parameterInventory.js:202-213` | design-ui | P2 | generated inventory test | Required | None |
| C083 | `sourceRadius` control | `src/ui/parameterInventory.js:202-213` | design-ui | P2 | generated inventory test | Required | None |
| C084 | `sourceCurv` control | `src/ui/parameterInventory.js:202-213` | design-ui | P2 | generated inventory test | Required | None |
| C085 | `sourceVelocity` control | `src/ui/parameterInventory.js:202-213` | design-ui | P2 | generated inventory test | Required | None |
| C086 | `simType` control | `src/ui/parameterInventory.js:215-225` | design-ui | P3 | generated inventory test | Required | None |
| C087 | `solverMode` control | `src/ui/parameterInventory.js:215-225` | design-ui | P3 | generated inventory test | Required | None |
| C088 | `throatResolution` solve-mesh control | `src/ui/parameterInventory.js:227-237` | design-ui | P3 | generated inventory test | Required | None |
| C089 | `mouthResolution` solve-mesh control | `src/ui/parameterInventory.js:227-237` | design-ui | P3 | generated inventory test | Required | None |
| C090 | `rearResolution` solve-mesh control | `src/ui/parameterInventory.js:227-237` | design-ui | P3 | generated inventory test | Required | None |
| C091 | `apertureResolutionScale` solve-mesh control | `src/ui/parameterInventory.js:227-237` | design-ui | P3 | generated inventory test | Required | None |
| C092 | `maxTriangles` solve-mesh control | `src/ui/parameterInventory.js:227-237` | design-ui | P3 | generated inventory test | Required | None |
| C093 | `allowLargeMesh` solve-mesh control | `src/ui/parameterInventory.js:227-237` | design-ui | P3 | generated inventory test | Required | None |
| C094 | `verticalOffset` export-coordinate control | `src/ui/parameterInventory.js:227-237` | design-ui | P3 | generated inventory test | Required | None |
| C095 | `quadrants` solve-mesh control | `src/ui/parameterInventory.js:227-237` | design-ui | P3 | generated inventory test | Required | None |
| C096 | `encFrontResolution` enclosure-mesh control | `src/ui/parameterInventory.js:239-242` | design-ui | P3 | generated inventory test | Required | None |
| C097 | `encBackResolution` enclosure-mesh control | `src/ui/parameterInventory.js:239-242` | design-ui | P3 | generated inventory test | Required | None |

## HTTP route inventory (`server/api/*.py`)

The inventory contains every decorated HTTP route in the three v1 route modules; method and literal path are the row key.

| ID | v1 behavior / route / control | Evidence (v1) | v2 owner | Phase | Test / manual script | Compat | Accepted deferral / workaround |
|---|---|---|---|---|---|---|---|
| A001 | `GET /` returns backend name, version, running state, and solver availability | `server/api/routes_misc.py:81-89` | platform | P1 | response-schema test | Required | None |
| A002 | `GET /health` returns solver/mesher/dependency/capability readiness | `server/api/routes_misc.py:92-128` | platform | P1 | ready/degraded schema test | Required | None |
| A003 | `POST /api/updates/check` performs update status lookup off the event loop | `server/api/routes_misc.py:131-136` | platform | P6 | mocked update test | Required | None |
| A004 | `GET /api/themes` lists render themes and backend default | `server/api/routes_misc.py:139-148` | results | P4 | registry schema test | Required | None |
| A005 | `GET /api/theme-preview` returns cached base64 montage or typed error | `server/api/routes_misc.py:151-174` | results | P4 | image/schema/error test | Required | None |
| A006 | `POST /api/render-charts` renders requested result charts as PNG payloads | `server/api/routes_misc.py:177-207` | results | P4 | chart golden test | Required | None |
| A007 | `POST /api/render-directivity` renders a directivity plot | `server/api/routes_misc.py:209-254` | results | P4 | polar golden test | Required | None |
| A008 | `POST /api/export-file` writes a validated workspace-relative upload | `server/api/routes_misc.py:256-319` | workspace | P3 | traversal/size/write tests | Required | None |
| A009 | `GET /api/workspace/path` returns current absolute output folder | `server/api/routes_misc.py:395-399` | workspace | P1 | response test | Required | None |
| A010 | `POST /api/workspace/select` opens native picker and persists valid selection | `server/api/routes_misc.py:470-484` | workspace | P6 | packaged OS test | Required | None |
| A011 | `POST /api/workspace/open` opens current folder in platform file manager | `server/api/routes_misc.py:487-508` | workspace | P6 | packaged OS test | Required | None |
| A012 | `POST /api/mesh/build` validates dependency/version/family and builds on dedicated Gmsh worker | `server/api/routes_mesh.py:37-104` | mesher | P3 | API + worker-affinity test | Required | None |
| A013 | `POST /api/mesh/step` builds full-domain acoustic inner-surface STEP | `server/api/routes_mesh.py:107-181` | exports | P3 | CAD golden test | Required | None |
| A014 | `POST /api/mesh/viewport` builds full-domain point grids/rings in a normal worker thread | `server/api/routes_mesh.py:184-230` | mesher | P0/P2 | latency/concurrency test | Required | None |
| A015 | `POST /api/solve` validates, resolves engine/mode, checks capability, and creates job | `server/api/routes_simulation.py:69-182` | jobs | P3/P4 | submission matrix | Required | None |
| A016 | `POST /api/stop/{job_id}` requests stop with 404/400 mappings | `server/api/routes_simulation.py:185-196` | jobs | P3 | state transition API test | Required | None |
| A017 | `GET /api/status/{job_id}` returns status, progress, stage, message, and mesh stats | `server/api/routes_simulation.py:199-214` | jobs | P3 | response-schema test | Required | None |
| A018 | `GET /api/results/{job_id}` returns completed results and maps conflicts/missing resources | `server/api/routes_simulation.py:217-227` | jobs | P3/P4 | lifecycle/error test | Required | None |
| A019 | `GET /api/mesh-artifact/{job_id}` returns stored MSH text as `text/plain` | `server/api/routes_simulation.py:230-240` | jobs | P3 | byte passthrough test | Required | None |
| A020 | `GET /api/jobs` supports status, limit 1–200, and offset pagination | `server/api/routes_simulation.py:243-250` | jobs | P3 | pagination/filter test | Required | None |
| A021 | `PATCH /api/jobs/{job_id}/metadata` updates label, snapshot, rating, export, raw-result, and mesh metadata | `server/api/routes_simulation.py:253-277` | jobs | P3 | partial patch persistence | Required | None |
| A022 | `DELETE /api/jobs/clear-failed` removes failed rows and returns exact IDs/count | `server/api/routes_simulation.py:280-287` | jobs | P3 | mixed-status deletion test | Required | None |
| A023 | `DELETE /api/jobs/{job_id}` rejects active conflicts and deletes inactive job | `server/api/routes_simulation.py:290-297` | jobs | P3 | active/inactive deletion test | Required | None |

## Package-script and operational workflow inventory

| ID | v1 behavior / route / control | Evidence (v1) | v2 owner | Phase | Test / manual script | Compat | Accepted deferral / workaround |
|---|---|---|---|---|---|---|---|
| S001 | `npm start` runs combined frontend/backend launcher | `package.json:9-9` | platform | P1/P6 | clean-launch smoke | Required | None |
| S002 | `npm run start:frontend` runs development server only | `package.json:10-10` | platform | P1 | frontend smoke | Required | None |
| S003 | `npm run start:backend` runs selected Python against `server/app.py` | `package.json:11-11` | platform | P1 | backend-only smoke | Required | None |
| S004 | `npm run dev` aliases development server | `package.json:12-12` | platform | P1 | command smoke | Required | None |
| S005 | `npm run build` invokes production webpack | `package.json:13-13` | platform | P1 | CI build | Required | None |
| S006 | `npm test` runs Node tests under `tests/**/*.test.js` | `package.json:14-14` | platform | P1 | CI test command | Required | None |
| S007 | `npm run test:server` runs Python unittest discovery through selected interpreter | `package.json:15-15` | platform | P1 | CI server test command | Required | None |
| S008 | `npm run lint` lints `src/` with repository ESLint config | `package.json:16-16` | platform | P1 | CI lint | Required | None |
| S009 | `npm run format:check` checks `src/` with repository Prettier config | `package.json:17-17` | platform | P1 | CI format | Required | None |
| S010 | `npm run bundle:check` runs bundle-size guard | `package.json:18-18` | platform | P1 | CI size gate | Required | None |
| S011 | `npm run diag:payload` builds canonical reference payload | `package.json:19-19` | mesher | P1/P3 | qualification workflow | Required | None |
| S012 | `npm run diag:geometry` checks reference geometry artifacts | `package.json:20-20` | mesher | P1/P3 | qualification workflow | Required | None |
| S013 | `npm run diag:mesher:reference-horn` builds OCC reference-horn mesh | `package.json:21-21` | mesher | P3 | qualification workflow | Required | None |
| S014 | `npm run diag:mesher:closed` checks OCC closed mesh | `package.json:22-22` | mesher | P3 | qualification workflow | Required | None |
| S015 | `npm run bench:cpubem` runs CPU-BEM benchmark | `package.json:23-23` | results | P4/P6 | benchmark threshold | Required | None |
| S016 | `npm run build:metal-helper` builds release Metal native helper | `package.json:24-24` | platform | P6 | macOS build smoke | Required | None |
| S017 | `npm run deps:bump-pins` rewrites module pins | `package.json:25-25` | platform | P1 | dry-run/temp fixture | Required | None |
| S018 | `npm run deps:check-pins` checks module pins without rewriting | `package.json:26-26` | platform | P1 | CI pin gate | Required | None |
| S019 | `npm run preflight:backend` runs backend runtime preflight | `package.json:27-27` | platform | P1/P6 | clean/degraded runtime matrix | Required | None |
| S020 | `npm run preflight:backend:strict` runs strict runtime preflight | `package.json:28-28` | platform | P1/P6 | strict exit-code matrix | Required | None |
| S021 | `npm run check:solver` verifies at least one usable solver engine | `package.json:29-29`; `server/scripts/check_solver_engine.py:14-18` | platform | P4/P6 | backend availability matrix | Required | None |
| S022 | `npm run doctor:backend` prints backend doctor report | `package.json:30-30` | platform | P1/P6 | report golden | Required | None |
| S023 | `npm run doctor:backend:json` emits JSON doctor report | `package.json:31-31` | platform | P1/P6 | JSON-schema golden | Required | None |
| S024 | `npm run doctor:backend:strict` applies strict doctor policy | `package.json:32-32` | platform | P1/P6 | strict exit-code matrix | Required | None |

## Settings-surface inventory (`src/ui/settings/`)

| ID | v1 behavior / route / control | Evidence (v1) | v2 owner | Phase | Test / manual script | Compat | Accepted deferral / workaround |
|---|---|---|---|---|---|---|---|
| T001 | Settings modal tabs are Viewer, Appearance, Simulation, Export Settings, Workspace, and System | `src/ui/settings/modal.js:134-141` | TBD | P2–P6 | tab/accessibility UI test | Required | None |
| T002 | Appearance persists chart theme, defaulting to `hornlab` | `src/ui/settings/appearanceSettings.js:6-21`; `src/ui/settings/appearanceSettings.js:35-98` | results | P4 | persistence/migration unit test | Required | None |
| T003 | Appearance loads backend theme registry and previews montage cards | `src/ui/settings/modal.js:363-550` | results | P4 | mocked backend UI test | Required | None |
| T004 | Layout supports classic and split results layouts | `src/ui/settings/layoutSettings.js:5-46` | results | P4 | persistence/layout E2E | Required | None |
| T005 | Layout panel mode supports auto and one through six panels | `src/ui/settings/modal.js:573-644` | results | P4 | layout E2E | Required | None |
| T006 | Layout arrangement supports auto, columns, rows, and grid | `src/ui/settings/modal.js:573-644` | results | P4 | layout E2E | Required | None |
| T007 | Layout persists split fraction and six remembered chart slots | `src/ui/settings/layoutSettings.js:31-46` | results | P4 | persistence unit test | Required | None |
| T008 | Viewer live-update toggle is represented in modal-local state | `src/ui/settings/modal.js:565-571` | viewer | P2 | interaction UI test | Required | None |
| T009 | Viewer rotate speed defaults to 1 | `src/ui/settings/viewerSettings.js:13-22` | viewer | P2 | control application test | Required | None |
| T010 | Viewer zoom speed defaults to 1 | `src/ui/settings/viewerSettings.js:13-22` | viewer | P2 | control application test | Required | None |
| T011 | Viewer pan speed defaults to 1 | `src/ui/settings/viewerSettings.js:13-22` | viewer | P2 | control application test | Required | None |
| T012 | Viewer damping defaults enabled with factor .05 | `src/ui/settings/viewerSettings.js:13-22` | viewer | P2 | control application test | Required | None |
| T013 | Viewer can invert scroll zoom | `src/ui/settings/modal.js:822-832`; `src/ui/settings/viewerSettings.js:152-189` | viewer | P2 | wheel event test | Required | None |
| T014 | Viewer can enable keyboard pan shortcuts | `src/ui/settings/modal.js:834-840`; `src/ui/settings/viewerSettings.js:13-22` | viewer | P2 | keyboard interaction test | Required | None |
| T015 | Startup camera mode defaults to perspective and takes effect next launch | `src/ui/settings/viewerSettings.js:13-22`; `src/ui/settings/modal.js:793-820` | viewer | P2 | restart E2E | Required | None |
| T016 | Viewer settings apply rotate/zoom/pan/damping to orbit controls | `src/ui/settings/viewerSettings.js:136-150` | viewer | P2 | control adapter unit test | Required | None |
| T017 | Orbit, camera, and input subsections reset independently | `src/ui/settings/viewerSettings.js:25-32`; `src/ui/settings/viewerSettings.js:191-220` | viewer | P2 | reset-scope unit test | Required | None |
| T018 | Simulation mesh-validation policy supports warn, strict, and off | `src/ui/settings/modal.js:1062-1074` | jobs | P3 | request persistence test | Required | None |
| T019 | Simulation sweep spacing supports logarithmic and linear | `src/ui/settings/modal.js:1077-1089` | jobs | P3 | request persistence test | Required | None |
| T020 | Simulation setting toggles verbose backend logging | `src/ui/settings/modal.js:1091-1099` | jobs | P3 | request persistence test | Required | None |
| T021 | Advanced solver backend supports auto, Metal, and BEMPP only | `src/ui/settings/simAdvancedSettings.js:3-17`; `src/ui/settings/modal.js:1120-1133` | jobs | P4 | validation/persistence test | Required | None |
| T022 | Export settings default task sort supports newest/rating/label | `src/ui/settings/modal.js:1156-1165` | jobs | P3 | persistence/UI test | Required | None |
| T023 | Export settings minimum-rating filter supports all through five-stars-only | `src/ui/settings/modal.js:1167-1183` | jobs | P3 | persistence/UI test | Required | None |
| T024 | Export settings toggles automatic solve-mesh download | `src/ui/settings/modal.js:1185-1208` | exports | P3 | persistence/polling test | Required | None |
| T025 | Management defaults are auto-export off, mesh download off, no formats, newest sort, rating 0 | `src/ui/settings/simulationManagementSettings.js:42-48` | exports | P3 | defaults/migration test | Required | None |
| T026 | Management settings recognize 11 result/geometry export format IDs | `src/ui/settings/simulationManagementSettings.js:24-40` | exports | P3 | enum completeness test | Required | None |
| T027 | Management settings use schema-versioned local-storage persistence | `src/ui/settings/simulationManagementSettings.js:100-162` | exports | P3 | corrupt/legacy storage test | Required | None |
| T028 | Auto-export selection UI lives in popup with toggle and format choices | `src/ui/simulation/autoExportPopup.js:17-88` | exports | P3 | popup UI/persistence test | Required | None |
| T029 | Modal contains an export-format row builder that is not invoked by its active Export Settings section | `src/ui/settings/modal.js:1138-1210`; `src/ui/settings/modal.js:1438-1469` | exports | P3 | dead-surface audit | Required | Decide whether to remove or wire explicitly |
| T030 | Workspace section displays selected path and exposes open/select actions | `src/ui/settings/modal.js:1213-1335` | workspace | P1/P6 | mocked API UI test | Required | None |
| T031 | System section exposes update check | `src/ui/settings/modal.js:1471-1496` | platform | P6 | mocked API UI test | Required | None |
| T032 | System reset restores viewer settings only, leaving simulation/export/workspace preferences | `src/ui/settings/modal.js:1498-1522` | viewer | P2 | reset isolation test | Required | None |
| T033 | Runtime capability refresh logic is intentionally absent from active settings UI | `src/ui/settings/modal.js:1527-1532` | platform | P6 | capability-surface decision test | Required | Restore elsewhere or accept explicit deferral |

## Completeness checks and open inventory work

| ID | v1 behavior / route / control | Evidence (v1) | v2 owner | Phase | Test / manual script | Compat | Accepted deferral / workaround |
|---|---|---|---|---|---|---|---|
| Q001 | HTTP route count represented above: 23 decorated routes across mesh, misc, and simulation modules | `server/api/routes_mesh.py:37-230`; `server/api/routes_misc.py:81-508`; `server/api/routes_simulation.py:69-297` | platform | P1 | compare `rg '@router\.'` to A rows | Inventoried | None |
| Q002 | Package script count represented above: 24 | `package.json:8-32` | platform | P1 | compare package keys to S rows | Inventoried | None |
| Q003 | Seven viewer display modes represented individually | `src/viewer/index.js:31-39` | viewer | P2 | compare mode keys to V rows | Inventoried | None |
| Q004 | Perspective and orthographic camera modes represented | `src/ui/settings/modal.js:770-820` | viewer | P2 | compare camera options to V/T rows | Inventoried | None |
| Q005 | All 110 named parameter keys in the centralized inventory are represented above; closely related guiding-curve keys are grouped into four rows | `src/ui/parameterInventory.js:17-243` | design-ui | P1 | parse inventory and compare C rows | Inventoried | None |
| Q006 | Generic section-curve overlays remain OPEN because bounded mining did not locate an owning contract | OPEN — evidence needed from renderer/control owner | viewer | TBD | targeted source audit | OPEN | Do not count as compatible |
| Q007 | Instance locking/duplicate-instance behavior — RESOLVED 2026-08-04: v1 has no app-level lock, so nothing to match; v2 adds one as new behavior | `scripts/backend-startup-status.js:53-102` (startup diagnostics only) | platform | P1/P6 | `server/tests/test_platform_luna.py` | New | None |
