# Runs/Jobs export — research findings and recommended design

**Date:** 2026-08-09
**Status:** Research only. Nothing implemented, no files changed.
**Sources:** four independent research agents — two Opus (v2 audit, v1 inventory), two codex-sol-xhigh (backend architecture, frontend UX).
**Reviewed:** adversarially reviewed by a fifth agent (codex-sol-xhigh) on 2026-08-09; corrections applied and independently re-verified. See §11 for what changed.

> **Contract warning.** `docs/EXPORT-CONTRACTS.md:101` freezes the v1 format set
> (`mwg_config, step, png, csv, json, txt, polar_csv, impedance_csv, vacs, stl, fusion_csv`)
> and its sequential per-format-failure orchestration, and `docs/RESULT-CONTRACTS.md`
> specifies the result-file schemas including frequency alignment. Anything in this
> document that drops a format or changes a schema **must amend those contracts
> explicitly**, not silently supersede them.

---

## 0. Naming note

There is no panel called "Runs" in v2. The runs list is the **Jobs** panel,
`frontend/src/shell/JobsPanel.tsx`, registered as `jobs` in
`frontend/src/shell/Workspace.tsx:25` and titled "Jobs" at `Workspace.tsx:92`.
The copy inside it says "run" throughout. This document says *run* for the concept
and cites `JobsPanel.tsx` for the code.

---

## 1. Summary — what to build

Your instinct is right on both counts:

- **v1 really is a hover-reveal menu.** `src/style.css:1561` opens the list on
  `:hover`, `:focus-within` *and* a click-toggled `.is-open` class, with a 4 px
  invisible hover-bridge at `style.css:1511`. So "one button, options on hover" is
  the existing behaviour, not a new idea.
- **v1 really does have too many items.** 11 formats in the registry
  (`src/ui/settings/simulationManagementSettings.js:23-38`), 12 rows in the per-job
  menu, flat and ungrouped, with three different label vocabularies and two menus whose
  identically-named items read different data. Two of the eleven are near-duplicates of
  files the app writes on its own (see §3.2 — an earlier draft of this document claimed
  five, which was wrong).

The recommended v2 design is a **split button** — visually one control, two hit
targets — in the footer of the selected, completed run:

```
┌───────────────────────────────────────────────┐
│   Run again  │  Export         ▾  │   Log     │
└───────────────────────────────────────────────┘
                                 │
                  ┌──────────────┴───────────────┐
                  │  RESULTS                     │
                  │  On-axis response      .frd  │
                  │  Polar set (VituixCAD) .zip  │
                  │  Charts (FR + dir.)    .png  │
                  │  Frequency data        .csv  │
                  │  Full results         .json  │
                  ├──────────────────────────────┤
                  │  GEOMETRY & DESIGN           │
                  │  STEP solid           .step  │
                  │  STEP inner surface   .step  │
                  │  Parameter config      .txt  │
                  ├──────────────────────────────┤
                  │  Complete run archive  .zip  │  ← Phase 2 only
                  ├──────────────────────────────┤
                  │  Advanced              ▸     │
                  ├──────────────────────────────┤
                  │  Export settings…            │
                  └──────────────────────────────┘
```

The archive item **cannot exist in Phase 1** — there is no zip anywhere in v2. Phase 1
ships the menu without it. *Advanced* holds the formats that are defensible but not
everyday (see §4).

- Clicking **Export** runs your preferred formats immediately (a fast path v1 never had).
- Clicking **▾** opens and pins the menu.
- Hovering either half reveals the menu after a short intent delay.
- If no preferred formats are configured, clicking **Export** opens the menu instead
  of erroring — v1 shows `Select at least one export format in Export Settings`
  (`src/ui/simulation/exports.js:862`) and its default is an empty list, so out of the
  box v1's primary action does nothing.

**Two findings dominate everything else:**

1. **v2 already has most of the export machinery** — seven working, tested builders and
   a dispatcher. What is missing is a menu, not an export engine.
2. ~~**v2 ships v1's worst export bug today.**~~ **FIXED 2026-08-10.**
   `buildFrequencyCsv` emitted a frequency column that was only valid for SPL. It now
   joins all three series onto the union of their grids — see §2.4. This was the one
   blocker on exposing export more prominently; it is cleared.

The work is mostly UI, but it is not *only* UI, and it is not risk-free. See §6.3 for an
honest scope.

---

## 2. Where v2 stands today

### 2.1 It already works — it is just not reachable from the runs list

`frontend/src/results/exporters.ts` (301 lines) has seven format builders and a
single-format dispatcher:

| Function | Line | Emits |
|---|---|---|
| `buildFrequencyCsv` | 59 | `Frequency, SPL, DI, Z_re, Z_im` + optional `# Smoothing:` line |
| `buildFullResultsJson` | 68 | `{timestamp, smoothing, results}` |
| `buildSummaryText` | 78 | Text report: stats blocks + fixed-width table |
| `buildPolarCsv` | 99 | `Frequency_Hz, Plane, Theta_deg, SPL_norm_dB` |
| `buildImpedanceCsv` | 112 | `Freq_Hz, Z_Real, Z_Imag` |
| `buildVacs` | 119 | VACS spectrum text |
| `buildChartRenderPayload` | 213 | `/api/render-charts` request body |

Orchestrated by `runExportFormat(format, context)` at `exporters.ts:243` and
`runExportBundle(context, formats)` at `exporters.ts:285`. Downloads go through one
helper pair, `downloadBlob` / `downloadText` at `frontend/src/api/designIo.ts:255-266`,
and both are injectable via `ExportContext.saveBlob` / `saveText` (`exporters.ts:9-18`) —
which is the seam a zip or folder-save would hook.

Server side, `server/exports/api.py` (mounted at `server/app.py:260`) serves three
POST routes — `/api/export/step` (`?body=solid|surface`), `/api/export/stl`,
`/api/export/profiles` (`?kind=profiles|slices`) — all taking a `DesignConfig` body,
none taking a job id. Plus `GET /api/results/{id}`, `GET /api/mesh-artifact/{id}`,
`GET /api/jobs/{id}/log`, and `POST /api/render-charts`.

**The only reason this is not already a runs-tab feature** is that `runExportBundle`
has exactly two call sites: the Results toolbar `Export (N)` button
(`ResultsPanel.tsx:633-646, 670`) and the auto-export-on-complete path
(`JobsCoordinator.tsx:121-131`).

### 2.2 What a job card already holds

`JobCard` (`JobsPanel.tsx:78-141`) already has the whole `JobItem`, plus
`snapshot = hydrateJobDesign(job)` at `JobsPanel.tsx:88` — the design that produced
the run, hydrated on every render — plus an `onError` handler and a footer that
already renders buttons.

So the export call from a job card is character-for-character the auto-export call
that already exists:

```ts
runExportBundle({
  result: await fetchJobResults(job.id),
  design: hydrateJobDesign(job) ?? undefined,
  designRevision: job.design_revision,
  preferences: { ...preferences, outputName: job.label?.trim() || … },
}, formats)
```

Three gaps, all small:

1. `JobsPanel` never imports `fetchJobResults` — only `ResultsPanel.tsx:589` does.
   It is LRU-cached (15 entries) and coalesces in-flight requests
   (`api/results.ts:66-93`). **This is cheap only on a cache hit.** A miss downloads
   and `JSON.parse`s a payload the server itself describes as megabytes
   (`server/jobs/store.py:714`), and the cache then retains up to fifteen such objects.
   Exporting the already-selected run is free; exporting an arbitrary historical run
   is not.
2. Formats today come only from `preferences.exportFormats`, which **defaults to `[]`**
   (`prefs/preferences.ts:65`) and is edited only in the preferences popover. A per-item
   menu would call `runExportFormat` directly instead.
3. There is **no multi-select** in the runs list — `compareSelection` is one primary
   plus chart overlays (`api/results.ts:95-152`). "Export this run" needs no new state;
   "export selected runs" would.

Metadata write-back after export already exists:
`jobsSocket.patchMetadata(id, {exported_files: […]})` → `PATCH /api/jobs/{id}/metadata`
(`server/jobs/api.py:152-163`), pattern at `ResultsPanel.tsx:640`.

### 2.3 Gaps and dead ends found in v2

- **`/api/export/step?body=surface` is unreachable from the results path.**
  `postGeometry`/`fetchGeometry` (`exporters.ts:152-166`) never send `body`, so
  `runExportFormat('step')` always gets the solid. Only `DesignFileMenu.tsx:149`
  reaches `surface`, via a *second, parallel* client path
  (`api/designIo.ts:270-294`) that duplicates the first.
- **`downloadMeshArtifact` (`exporters.ts:295`) has no manual affordance** — it works,
  and fires only from an automation preference. A "Mesh (.msh)" menu item is a one-line reuse.
- **The job log has no export**, only `window.open` (`JobsPanel.tsx:120, 125, 138`).
- **`buildVacs` is untested** — `exporters.test.ts:19-23` covers the other five builders.
- **No zip anywhere.** Grep for `zipfile|jszip|\.zip` across `server/`, `frontend/src/`,
  `scripts/`, `docs/` returns nothing. Multi-file export is currently N browser downloads,
  which is what `fusion_csv` and `png` already do.
- **`JobItem` is behind the server model.** `api/jobsSocket.ts:23-49` omits
  `design_availability`, `symmetry`, `solve_path`, `axisymmetric_eligibility_reasons`,
  `solve_wall_time_seconds`, all of which `server/jobs/models.py:331-344` sends.
  `design_availability` is bolted back on via `jobs/jobDesign.ts:21-23`.
- **No reusable menu primitive exists.** `DesignFileMenu.tsx:133-155` is the closest
  thing but is hardcoded and unparameterised, with its own CSS
  (`styles/app.css:63-100, 140-142`). `HelpTip.tsx` is **not** reusable here — it is
  `role="tooltip"`, `pointer-events: none`, and closes the moment the trigger is left
  (`HelpTip.tsx:102`, `app.css:186`). Its portal/viewport-edge maths is worth extracting,
  its behaviour is not.
- **Clipping hazard:** `DesignFileMenu` lives in the top bar. A menu inside a
  `.job-card` (`position: relative`) in the scrolling `.jobs-panel` will need portal
  rendering, which `DesignFileMenu` never had to do.

### 2.4 ~~Live defect~~ FIXED: v2's frequency CSV emitted a frequency column valid only for SPL

**This was not a v1 lesson — it was in v2**, and it was the single most important thing
in this document. **Resolved 2026-08-10**; the diagnosis below is kept as the rationale
for the join policy, and the resolution follows it.

`smoothedSeries` (`frontend/src/results/exporters.ts:43`) does the right thing — it
derives `impedanceFrequencies` and `diFrequencies` independently, falling back to the SPL
grid only when the result has no separate axis, and smooths each series against *its own*
grid:

```ts
const impedanceFrequencies = result.impedance?.frequencies?.length ? result.impedance.frequencies : frequencies;
const diFrequencies        = result.di?.frequencies?.length        ? result.di.frequencies        : frequencies;
```

`buildFrequencyCsv` (`exporters.ts:59`) then throws that away:

```ts
series.frequencies.forEach((frequency, index) => rows.push([
  frequency, csvCell(series.spl[index]), csvCell(series.di[index]),
  csvCell(series.impedanceReal[index]), csvCell(series.impedanceImaginary[index]),
].join(',')));
```

Every column is indexed by the **SPL** row index and only the SPL frequency is emitted.
Whenever DI or impedance is returned on a different grid, the CSV silently mislabels those
columns, and nothing downstream can detect it. This is the identical positional-zip error
found in v1 at `src/ui/simulation/exports.js:433-441`.

**Resolution (2026-08-10).** Join policy decided: **exact-key union join**. `joinSeries`
in `frontend/src/results/exporters.ts` builds rows from the sorted union of the SPL, DI,
and impedance grids and fills a cell only where that series has a sample at that exact
frequency — empty otherwise, never interpolated. `buildFrequencyCsv` and `buildSummaryText`
both consume it, so the two exports cannot disagree. Because the union collapses to the
SPL grid whenever the grids match, today's output is byte-identical and the frozen header
is unchanged; both properties are pinned by tests in
`frontend/src/results/exporters.test.ts`. Contracts amended in `RESULT-CONTRACTS.md`
("Frequency alignment and comparison", "Export preservation", decision 5) and
`EXPORT-CONTRACTS.md` ("Result-export bundle").

Consequence for §4: **impedance CSV is no longer the only export whose frequency axis is
unambiguous** — that was the reason it could not be dropped, and it no longer holds. If it
is removed, remove it for being redundant, not to preserve a correct axis.

The rejected alternatives, for the record: explicit `f_spl`/`f_di`/`f_z` columns break the
frozen header for every consumer even when the grids agree; interpolation onto one grid
fabricates samples the solver never produced and would drop DI or impedance points lying
outside the SPL range, contradicting `RESULT-CONTRACTS.md`'s "emit `null`; do not replace
with 0".

---

## 3. v1 — full inventory and what is wrong with it

### 3.1 The eleven formats

Registry at `src/ui/settings/simulationManagementSettings.js:23-38`, dispatch at
`src/ui/simulation/exports.js:758-819`.

| # | ID | Label | Output | Side |
|---|---|---|---|---|
| 1 | `mwg_config` | Parameter Config (.txt) | `<base>.txt` | client |
| 2 | `step` | Waveguide STEP | `<base>.step` — **inner surface only**, forces `encDepth=0`, `wallThickness=0` (`src/modules/export/index.js:292-302`) | server |
| 3 | `stl` | Waveguide STL | `<base>.stl` binary, skin only | client |
| 4 | `fusion_csv` | Fusion 360 CSV Curves | **two** files, `X;Y;Z` in **cm** | client |
| 5 | `png` | Chart Images | `<base>_<chart>.png` ×4, matplotlib | server |
| 6 | `csv` | Frequency Data CSV | `<base>_results.csv` | client |
| 7 | `json` | Full Results JSON | `<base>_results.json` | client |
| 8 | `txt` | Summary Text Report | `<base>_report.txt` | client |
| 9 | `polar_csv` | Polar Directivity CSV | `<base>_polar.csv` | client |
| 10 | `impedance_csv` | Impedance CSV | `<base>_impedance.csv` | client |
| 11 | `vacs` | ABEC Spectrum (VACS) | `<base>_spectrum.txt` | client |

Plus a 12th job-menu pseudo-item, `selected` → "Selected Formats"
(`src/ui/simulation/jobActions.js:76`).

### 3.2 The invisible extra artifacts

v1 also writes files nobody asked for, **but the earlier claim that five are written
unconditionally on every completed job was wrong.** Corrected:

| Artifact | When |
|---|---|
| `task.manifest.json`, `waveguide.project.v1.json` | attempted when a job is **queued**, and on later syncs — not on completion (`src/ui/workspace/taskManifest.js:281-289`) |
| `script.snapshot.mwg` | same path, and **conditional on a valid snapshot** (`taskManifest.js:104`) |
| `<base>_raw.results.json` | only on the `justCompleted` success path (`src/ui/simulation/workspaceTasks.js:88-100`) |
| `<base>_solver.mesh.msh` | conditional on artifact availability **and** fetch/write success (`workspaceTasks.js:103-115`, `src/ui/simulation/polling.js:125, 183`) |
| `simulation_mesh_<jobId>.msh` | optional browser download, off by default (`src/ui/simulation/meshDownload.js:19`) |

**Two genuine near-duplicates remain**, and they are still the strongest argument for a
leaner menu: `script.snapshot.mwg` uses the *same builder* as menu format #1, and
`<base>_raw.results.json` carries the same result data as menu format #7 — though **not
the same bytes**: the menu format wraps it as `{timestamp, smoothing, results}`
(`exports.js:454`) while the automatic file writes `results` directly
(`workspaceTasks.js:90`). Ticking those two boxes produces files a user will struggle to
tell apart from ones already on disk. The other automatic artifacts are manifests and the
solver mesh — not duplicate menu formats.

### 3.3 Concrete defects (do not port these)

**Correctness**

- **Silent index misalignment.** `readResultSeries` deliberately returns three separate
  frequency grids — `frequencies`, `diFrequencies`, `impedanceFrequencies`
  (`exports.js:300-311`) — but `buildCsvFile` zips all of them against `frequencies[i]`
  and emits one frequency column (`exports.js:433-441`). `buildTextFile` repeats the
  mistake (`:535-541`). If the backend ever returns a different DI or impedance grid,
  the CSV is wrong and unnoticeable. **`impedance_csv` exists mainly to paper over this**
  — it emits its own frequency column.
- **Clamped, unvalidated frequency indices.** `frequencies[Math.min(fi, len-1)]` in
  `buildPolarCsvFile` (`:565`) and `buildVacSpectrumFile` (`:701`) — extra directivity
  frames silently repeat the last frequency.
- **VACS fabricates phase in the polar block.** Declares `Data_Format=Complex` (`:678`)
  then emits every **polar-pressure sample** as magnitude plus phase `0` (`:711-718`),
  exports only **one** plane (`resolveVacReferencePlane`, `:186-197`), and emits
  `SourceDesc=` twice with different values (`:616`, `:619`). The *impedance* block is
  fine — it preserves real and imaginary parts (`:651-659`). An earlier draft said "every
  point", which overstated it; the polar defect alone is disqualifying.
- **Exports that "succeed" while empty.** Polar CSV with no directivity emits a header
  line only; VACS with no data emits just its preamble (~17 lines, `:609-626`). Both are
  recorded as successfully exported files (`exports.js:890`). `writeExportFile` returns
  the filename unconditionally (`exports.js:91-103`). Note `saveFile` returns `undefined`
  on **every** path including success (`fileOps.js:268-341`), so the return value proves
  nothing either way; the demonstrable false-success paths are **invalid filename and
  user-cancelled save picker**, not every caught error — most backend errors do fall back
  to a picker or anchor download. The defect is real but narrower than first stated.
- **Silent overwrite.** `server/api/routes_misc.py:296-298` opens `"wb"` with no
  collision handling, and `finalizeExportCounter` (`fileOps.js:232-238`) does not
  increment the counter — it bumps on the *next parameter edit*. Exporting twice in a
  row overwrites with no warning.

**Consistency**

- **Three label vocabularies for the same four formats**, hand-maintained in three
  places: `index.html:232`, `simulationManagementSettings.js:24`, `jobActions.js:77`.
- **Two menus with different item sets and different data scopes.** The design menu
  (`index.html:220-243`) has 4 items and reads **live editor state**
  (`src/app/exports.js:26-30`); the job menu (`jobActions.js:75-88`) has 12 and reads
  the **stored job snapshot** (`exports.js:80-89`). Same labels, different meaning.
- **Capability gating is wrong.** `getExportCapability('ICW','mwg_config')` returns
  AVAILABLE (`src/modules/export/capabilities.js:26`) but `runConfigExportTask` **throws**
  for ICW/LOOKUP (`index.js:465-472`) — the item renders enabled and fails on click.
  Disabled items stuff their reason paragraph *inside* the button
  (`src/app/events.js:52-60`), forcing multi-line wrap and making the menu jump height
  (`style.css:1585-1590`).
- **Settings live in two places.** `autoExportPopup.js` is a whole modal existing only
  to hold checkboxes for the same 11 IDs, while the Settings modal's own "Export Settings"
  tab claims "manual export formats all live together here" (`modal.js:1146-1149`) and
  then renders only sort/rating/mesh-download — **because its formats row is dead code**
  (`modal.js:1438`, defined and never called).

**Performance**

- **Blocking, unprogressed STEP.** 120 s timeout (`index.js:27`), and the bundle path
  calls `buildStepExportFiles` without `onStatus` (`exports.js:774-777`) — a bundle can
  stall two minutes with no feedback.
- **Synchronous STL.** `runStlExportTask` (`index.js:358-403`) rebuilds geometry and
  writes per-triangle DataViews on the main thread. No worker, no yield.
- **Strictly sequential bundle writes.** One HTTP upload at a time
  (`exports.js:106-112`) — 11 formats ≈ 16 files ≈ 16 serial round-trips.

**UX**

- **Hover-open and click-toggle fight.** After clicking an item, `closeExportMenus`
  strips `.is-open` (`app/events.js:249-255`) but `:hover` keeps the menu visible until
  the pointer leaves. The 4 px `::before` bridge has **no `pointer-events: none`** and
  overlays whatever sits below the trigger (`style.css:1511-1519`).
- **ARIA disagrees with the visuals.** CSS `:hover`/`:focus-within` can visibly open the
  menu while `aria-expanded` stays `false` — that attribute is only touched by the click
  handler (`events.js:256`).
- **No keyboard menu navigation.** `role="menu"` and `role="menuitem"` are set
  (`jobActions.js:285`) but there is no arrow-key, Home/End, or roving-tabindex support.
- **No meaningful plain-click default** — clicking Export only toggles the menu.
- **One long, ungrouped list** of 12 items with no headings.
- **The primary item is opaque.** "Selected Formats" does not say which or how many.
- **Busy state vanishes** — the open class is removed *before* awaiting
  (`src/ui/simulation/events.js:111`).
- **The job list re-renders on every poll**, which is why
  `captureJobListInteractionState`/`restoreJobListInteractionState`
  (`jobActions.js:367-419`) exists purely to keep an open menu open.

**Dead code (do not port)**

`runHornlabMesherMeshExportTask` (~95 lines, `src/modules/export/index.js:169-263`) and
`prepareExportArtifacts` — referenced only by tests. `exportSTLAscii`
(`src/export/stl.browser.js:108-152`) — never imported. `_buildSimulationExportFormatsRow`
(`modal.js:1438-1468`) — never called. The `vertices` argument threaded
`App.js:222` → `useCases.js:128` → `index.js:526-531` and **never read**;
`onMissingMesh` explicitly discarded (`useCases.js:150`).

---

## 4. Recommended menu contents

The two agents that proposed lists disagreed slightly. Merged recommendation, with the
disagreement noted:

The principle is **demote, don't delete.** An earlier draft proposed deleting four
formats; review showed three of those justifications were factually wrong. Removing a
frozen format (`docs/EXPORT-CONTRACTS.md:101`) is a user-visible regression and needs
evidence, not tidiness. A two-tier menu gets the simplicity you want without breaking
anyone.

### Top level (7 file actions + archive)

Revised 2026-08-10 on the owner's direction: measurement-tool interoperability is the
point of the numeric exports, STEP is the geometry format that matters, and both chart
kinds must be exportable as images.

| Item | Ext | Why |
|---|---|---|
| **On-axis response (FRD)** | `.frd` | **New, and the headline numeric export.** `Freq, SPL, Phase` — directly loadable by REW and VituixCAD. See §4.3. |
| **Polar set for VituixCAD** | `.zip` of `.frd` | **New.** One FRD per angle, named to VituixCAD's convention. The only way its directivity tools ingest this data. See §4.3. |
| **Frequency data** | `.csv` | Keep for Excel/scripting, **not** as the interop path. **Blocked on the §2.4 grid fix.** |
| **Charts** | `.png` | Frequency response **and** directivity — see §4.2. Two endpoints, both required. |
| **Full results** | `.json` | Presentation export: `{timestamp, smoothing, results}`. Distinct from the archive's byte-exact raw results — label them differently. |
| **STEP solid** | `.step` | The primary geometry export: exact B-rep, not tessellated. |
| **STEP inner surface** | `.step` | Promoted from Advanced — the acoustic surface for users who thicken or loft it themselves. |
| **Parameter config** | `.txt` | Round-trippable design; "Load Config" consumes it. **Suffix is `.txt` today**, see §4.1. |
| **Complete run archive** | `.zip` | Phase 2 only. See §6.4. |
| *Export settings…* | — | Footer command, visually separated from file actions. |

**STL is demoted to Advanced.** Owner's direction: tessellated geometry is not what this
tool should be handing to CAD, and STEP covers the need. It stays reachable for 3D
printing — it is a frozen v1 format (`EXPORT-CONTRACTS.md:101`) and deleting it is a
regression — but it should not sit next to STEP implying equivalence. It is also not
cheap in v2 (§4.1).

### Advanced submenu — keep, demote

| Item | Why it stays |
|---|---|
| **Impedance CSV** | **Do not drop this yet.** Per §2.4 it is currently the only export with an unambiguous frequency axis. It can go only *after* the frequency CSV schema is fixed, frozen, and documented — not in the same change. See §4.3 on why this is **not** a `.zma`. |
| **STL mesh** | Demoted, not deleted — tessellated, superseded by STEP for CAD, still wanted for 3D printing. v2 rebuilds a full solver mesh through Gmsh + `meshio` (`server/exports/core.py:336`) so it needs the same busy treatment as STEP. |
| **Fusion 360 CSV curves** | STEP is **not** a substitute: curve CSV drives editable sketch/loft workflows, an imported STEP is a finished B-rep. The earlier claim that these files are headerless was **wrong** — both carry `# x_cm;y_cm;z_cm` (`src/export/profiles.js:12`, and v2's `server/exports/core.py:381-406`). |
| **Summary text report** | The earlier justification was **wrong**. It is not the CSV with a header: it carries generation time, frequency range, point count, averages, ranges and SPL variation (`frontend/src/results/exporters.ts:78`). Whether anyone consumes it is unverifiable from the repo — demote, don't delete. |
| **STEP inner surface** | Already exposed in the design menu (`DesignFileMenu.tsx:149`) and valuable to users who thicken or loft the acoustic surface themselves. A run-bound menu must not silently narrow the geometry contract to solid-only. |
| **Solver mesh `.msh`** | Contradicting §2.3 to drop this would be perverse — it is a one-line reuse of `downloadMeshArtifact`, and solver users need the exact solved mesh for diagnosis and downstream BEM. Direct artifact action, not archive-only. |
| **Job log** | Currently only `window.open` (`JobsPanel.tsx:120`). Cheap to add. |

### Drop

| Drop | Why |
|---|---|
| **ABEC VACS** | Drop **entirely** — not "keep reachable in settings", which would still ship known-invalid data. And it cannot simply be fixed: stored directivity is engine-provided **dB, not complex pressure** (`docs/RESULT-CONTRACTS.md:29`), so correct phase cannot be reconstructed from what v2 persists. Reinstating it means first preserving complex polar pressure in the result contract. Needs an explicit compatibility note against `EXPORT-CONTRACTS.md`. |
| **"Selected Formats" pseudo-item** | An item meaning "whatever you configured in another dialog" is not a format. Replaced by the split button's primary action, which states its count. |
| **`hornlab-mesher-mesh` kind, `exportSTLAscii`, `_buildSimulationExportFormatsRow`** | Dead in v1; do not port. |

### 4.1 Two corrections that change the plan

**Parameter config is `.txt`, not `.cfg`.** `runExportFormat('mwg_config')` posts
`filename: ${baseName}.txt` to `/api/design/save` and falls back to `${baseName}.txt`
(`exporters.ts:247-253`). The design-file *Save* UI uses `.cfg`. So the two surfaces
already disagree, and unifying them is a **contract decision** — not the wiring exercise
an earlier draft implied. Decide deliberately and record it in `CFG-FORMAT.md`.

**STL is not cheap in v2.** v1's STL was synchronous local triangle serialisation. v2's
`build_stl` (`server/exports/core.py:336`) rebuilds a full densified solver mesh through
Gmsh, parses it with `meshio`, and filters to tag-1 triangles. It is plausibly one of the
*more* expensive menu actions and needs the same busy treatment as STEP.

Also corrected: STEP is **not** "the only geometry path that works for ICW/LOOKUP/FREEFORM".
`LOOKUP` is not a v2 formula family at all (`server/design/schema.py` declares OSSE,
R-OSSE, ICW, FREEFORM), and v2's STL and profile builders contain no formula-family
rejection (`core.py:336, 409`). That claim was inherited from v1's capability gating and
does not transfer.

### 4.3 Measurement-tool interoperability (REW / VituixCAD)

**Neither tool ingests v2's current CSV.** Both speak FRD/ZMA:

| Format | Columns | Consumed by |
|---|---|---|
| **FRD** | `Freq(Hz)  SPL(dB)  Phase(deg)` — tab/space/semicolon delimited `.frd` or `.txt` | REW ("generic comma, space or TAB-delimited"), VituixCAD |
| **ZMA** | `Freq(Hz)  \|Z\|(ohms)  Phase(deg)` | VituixCAD |

v2's frequency CSV is comma-delimited with five columns including DI and normalized
impedance. No amount of column-renaming makes it an FRD. **Add FRD as a first-class
export rather than bending the CSV** — the CSV stays useful for Excel and scripting.

**On-axis FRD is straightforward.** v2 persists on-axis phase in degrees from `angle(p)`
(`docs/RESULT-CONTRACTS.md`, "Phase"), and `smoothedSeries` already carries it
(`exporters.ts:47`). So `Freq, SPL, Phase` is directly emittable today.

**Directivity FRD hits a hard blocker.** VituixCAD's directivity tools take a *set* of
per-angle files (it also accepts VACS balloon data under a
`Phi[mmm]Theta[ppp].txt/frd` naming convention) — not one wide file. v2 can produce the
angles, but:

> **The stored directivity is engine-provided dB with no phase.**
> `RESULT-CONTRACTS.md`: *"Directivity polar | Engine-provided dB or `null` samples paired
> with angle degrees"*.

So per-angle FRD files can only be written with a zero or omitted phase column — exactly
the defect that disqualifies v1's VACS exporter (§3.3). VituixCAD can derive minimum
phase, but that is an assumption, not the simulated phase, and a waveguide's off-axis
phase is a large part of what makes the simulation worth running.

**This makes the result-contract change a priority, not a nice-to-have.** Persisting
complex polar pressure — flagged in §4 as the blocker for reinstating VACS — is the *same*
change that unlocks credible VituixCAD directivity export. It should move into Phase 0.

Interim options, in order of preference:

1. **Persist complex polar pressure**, then emit true per-angle FRD sets. Correct, and
   also revives VACS as a possibility.
2. **Ship magnitude-only FRD sets now**, with the phase column omitted (not zero-filled)
   and a header comment stating the data is magnitude-only. Honest and immediately useful.
3. Do nothing until (1) lands. Leaves the user's stated requirement unmet.

Recommend (2) now and (1) in Phase 0 — but the files must *say* they are magnitude-only.
Silently writing zero phase under an FRD extension repeats v1's VACS mistake.

**Do not ship a `.zma`.** v2's impedance is dimensionless normalized specific acoustic
impedance `Z/(rho*c)` (`RESULT-CONTRACTS.md`, "Normalized impedance"). VituixCAD's ZMA
expects a driver's **electrical** impedance in ohms. These are different physical
quantities, and a `.zma` built from the acoustic throat impedance would load without error
and be wrong. An electrical ZMA only becomes meaningful once a driver LEM is coupled to
the acoustic load. Keep the impedance export as CSV, labelled with its units.

### 4.2 Disagreement — chart PNGs

The v1 agent argues a server matplotlib round-trip that can 503 for a picture already
on screen is not worth the dependency, and that v2 should export the on-screen chart
client-side. The backend agent argues `hornlab-plots` is the *canonical* renderer, is
already lock- and cache-protected (`server/charts/api.py:124`), and should stay
authoritative — returning all images as **one zip** rather than four downloads.

**Recommendation: keep server-side rendering, return a zip.** The canonical-renderer
argument wins — v2's charts are ECharts and would not match published plots — but the
four-separate-downloads behaviour must go.

**Both chart kinds are required** (owner's direction: frequency response *and*
directivity as PNG). These are **two different endpoints** and a single menu item must
call both:

- `POST /api/render-charts` (`server/charts/api.py:259`) → `{kind: dataURI}` for the
  FR / DI / impedance charts.
- `POST /api/render-directivity` (`server/charts/api.py:277`) → the directivity map,
  a single image, rendered by `_render_directivity` (`charts/api.py:170`).

Until the Phase 2 zip exists, one "Charts" click therefore produces several downloads.
That is acceptable interim behaviour, but it is the clearest argument for pulling a
minimal zip endpoint forward into Phase 1.

---

## 5. Recommended UI

### 5.1 Hover mechanics

You asked for hover. Hover-only menus are hostile to touch, fire accidentally, and
close when the pointer strays — v1 demonstrates all three. The recommendation is
**hover as an accelerator, not the only way in**:

The timings below are **starting points to tune against the real control, not research
findings** — treat them as defaults, not requirements:

- Pointer entering the control starts a **~180 ms open delay**.
- Leaving both trigger and menu starts a **~300 ms close delay**.
- 6 px visual gap between trigger and menu, covered by the close delay.
- A **safe-pointer polygon** is worth having only if the close delay alone proves
  insufficient in practice. Safe triangles earn their keep on *lateral* submenus; for a
  vertically adjacent menu they can keep it alive while the user is deliberately moving
  away toward unrelated content. Ship the delay first, add the polygon only if testing
  shows it is needed. (v1's 4 px rectangular bridge protects nothing diagonal and has no
  `pointer-events: none` — `style.css:1511`.)
- **Mouse hover only.** Ignore touch hover emulation; pen/touch use the chevron.
- Hover-open **never moves focus** or changes screen-reader focus.
- Outside pointer-down, Escape, and scrolling the panel close immediately.
- Menu closes on success and stays open on failure. **Do not** branch this on whether the
  export was client- or server-generated (an earlier draft did) — that makes the
  interaction depend on an implementation tier the user cannot see. Branch on *outcome*.
- Respect `prefers-reduced-motion`.

### 5.2 Placement

**Two affordances, not one.** An earlier draft put the control *only* in the expanded
selected run's footer. That is too hard to reach: selecting a historical run to export it
also makes its results primary and may replace the viewport design (`JobsPanel.tsx:66`) —
a large side effect to pay for a file.

- **Collapsed completed rows:** a compact export/overflow affordance revealed on hover
  **and focus**, which exports **without selecting the run**. This keeps the quiet history
  list quiet (`app.css:434`) while making export reachable.
- **Expanded selected run:** the full split control in the footer, between "Run again" and
  "Log" (`JobsPanel.tsx:126`).
- **Do not** add a Jobs-toolbar export. There is no multi-selection to act on.
- Queued/running/failed runs: **do not render** the control at all. Do not fill the rail
  with disabled buttons.

**Lifecycle hazards.** Cards are keyed by `job.id` (`JobsPanel.tsx:213`), so ordinary
websocket re-renders preserve local menu state — the v1 `captureJobListInteractionState`
problem does not automatically recur. What *does* break:

- sorting/rating/status changes move the anchor while a portal sits at a stale rect;
- a completing run while "following latest" can change the selection, collapse the old
  card and unmount the control mid-hover;
- retention/deletion can remove the anchor outright;
- the one-second `now` tick re-renders every card even with no websocket traffic.

Consequently **an in-flight export must be owned above the menu and the card** — a store
or coordinator — so unmount, collapse or reselection neither loses its status nor
triggers a duplicate retry.
- Missing saved design: keep the control usable but disable only STEP/STL/config, showing
  the stored reason — v2 already supplies user-facing reasons at `jobs/jobDesign.ts:54`.
  A preferred bundle mixing result and geometry formats should run the available parts
  and report a **partial** export.

### 5.3 Components

- **`frontend/src/design/ActionMenu.tsx`** — a new reusable primitive owning positioning,
  **portal rendering**, hover intent, safe-pointer handling, outside dismissal, roving
  focus and keyboard. This interaction is too fiddly to duplicate; it can later replace
  `DesignFileMenu`'s hardcoded markup.
- **`frontend/src/jobs/RunExportControl.tsx`** — export-specific content and operations only.

```ts
interface RunExportControlProps {
  job: JobItem;
  result?: ResultPayload;        // optional: ResultsPanel has it; JobsPanel fetches
  placement?: 'auto' | 'above' | 'below';
  compact?: boolean;
  onOpenExportSettings(): void;
  onNotice(notice: ExportNotice): void;
}
```

State: `openMode: 'closed' | 'hover' | 'pinned'`, `activeIndex`, `busyAction`,
`itemError`, timer refs, trigger/menu/last-pointer refs.

Reuse the **visual language** of `.design-menu-popover` / `.design-menu-item` /
`.design-menu-divider` (`app.css:63`) rather than inventing a third floating surface.
Move `resultExportSnapshot` out of `ResultsPanel.tsx:35-43` into a shared module rather
than importing a shell component into `RunExportControl`.

Eventually render the same `RunExportControl` in the Results toolbar
(`ResultsPanel.tsx:658`) so both surfaces have identical menus and semantics — this is
the fix for v1's "two menus, two scopes" defect.

### 5.4 Feedback

- **Preparing:** keep the menu open, spinner on the chosen item, text `Preparing STEP…`,
  `aria-busy="true"` on the menu, other actions disabled, Escape still works.
- **Success:** close, announce `Downloaded frequency data: horn_v12.csv` or
  `Downloaded 4 files` via a transient `role="status"` for ~3 s. Record filenames in job
  metadata. **Do not say "Saved to Downloads"** — `downloadBlob` hands off to the browser
  (`designIo.ts:255`), which may prompt or use another folder.
- **Failure:** keep the menu open, restore focus to the failed item, show the error below
  it, plus a persistent `role="alert"` with Retry. `runExportBundle` already returns
  per-format failures rather than abandoning the bundle (`exporters.ts:285`).
- v2 has **no shared toast system** — status is inline at `ResultsPanel.tsx:674`, and
  `DesignFileMenu.tsx:154` has a fixed `role="status"`. Extract that into a small shared
  `AppNotice`; do not add a third mechanism.

### 5.5 Accessibility

Two real buttons inside one visual control. Main button named `Export preferred formats`;
chevron with `aria-label="More export options"`, `aria-haspopup="menu"`, `aria-expanded`,
`aria-controls`. Menu is `role="menu"` with `aria-label="Export <run name>"`; items are
native `<button role="menuitem">`; group headings via `role="group" aria-labelledby`.
Disabled items use native `disabled` with a visible non-interactive reason (not a tooltip).
Roving tabindex. `ArrowDown`/`ArrowUp`/`Alt+ArrowDown` open and focus first/last; arrows
wrap and skip disabled; Home/End; Enter/Space activate; Escape closes and returns focus
to the chevron; Tab closes without trapping focus.

---

## 6. Backend

### 6.1 The one real disagreement between agents

- The **v2-audit agent** found that a runs-tab export needs *almost no plumbing* — the
  client path already works from a `JobCard` with three small additions.
- The **backend agent** proposes a run-bound service at `/api/jobs/{id}/exports/…`,
  moving polar/balloon/VACS/results-JSON server-side, adding zip streaming, a store-level
  export snapshot, concurrency limits, and shared filename utilities — **≈1 engineering
  week** of backend work.

Both are correct about different scopes. **Recommendation: a contract step, then two
phases.**

### 6.2 Phase 0 — freeze the contracts first (do not skip)

Review found that starting with UI would force contract churn later. Decide and document
these *before* writing the menu, amending `EXPORT-CONTRACTS.md` and `RESULT-CONTRACTS.md`:

1. **The frequency-axis join policy** for the fixed frequency CSV (§2.4) — sparse
   `f_spl`/`f_di`/`f_z` columns, or interpolation onto one grid.
2. **Raw vs presentation JSON** — what the menu emits versus what the archive stores.
3. **`.cfg` vs `.txt`** for the parameter config (§4.1).
4. **Whether to persist complex polar pressure.** Per §4.3 this is now the gating decision
   for credible VituixCAD directivity export, not just for VACS. It is a result-contract
   and storage-size change, so it belongs here rather than being discovered mid-build.
5. **FRD emission rules**: delimiter, decimal separator, header comment convention, and —
   critically — how magnitude-only data is marked so it is never mistaken for measured
   phase.
6. **VACS**: dropped for now, with a compatibility note; revisit if (4) lands.
7. **Legacy v1-imported jobs**: what is guaranteed when the design is lossy or absent.
8. **One artifact catalog and capability model**, shared by the Results toolbar, the Jobs
   menu, auto-export, the future archive **and the `wglink` bundle** (§6.5) — so a format
   is defined once, not four times. This is the direct fix for v1's "three label
   vocabularies, two menus, two scopes" defect and the piece most likely to be regretted
   if skipped.
9. **One filename function** and one response/error contract.
10. **Which numbering scheme wins** — `export.sequence` from `CAD-LINK-PLAN.md`, the run
    ledger from the run-naming plan, or the interim short UUID (§7.2). Three plans
    currently propose three; pick one.

### 6.3 Phase 1 — the menu

Wire the existing client exporters to `RunExportControl`. No new server routes.
**This is real work, not a wiring exercise** — an earlier draft called it "days,
near-zero risk", which was wrong. It contains:

- A new portal-positioned menu primitive with hover intent, keyboard support and
  outside-dismissal.
- The result-fetch and memory path into `JobsPanel` (§2.2, gap 1).
- The frequency-CSV schema change from Phase 0.
- A **preference migration**: changing `defaults.exportFormats` does not migrate existing
  profiles, because `normalize` coerces an absent or non-array value to `[]`
  (`preferences.ts:90`). This needs a deliberate storage-version bump.
- **Separating manual preferred formats from auto-export formats.** One `exportFormats`
  list currently drives both; a sane manual default would silently become a surprising
  automatic bundle the moment a user enables auto-export.
- Consolidating the two geometry clients, which have *different shapes*:
  `exporters.ts` `fetchGeometry` returns blobs for aggregation, `designIo.ts`
  `downloadGeometryExport` downloads immediately and returns `void`.
- Partial-success semantics and `exported_files` bookkeeping. Note `exported_files` can
  only honestly mean **"download initiated"** — an injected `saveBlob` confirms the
  handoff, not that the browser wrote a file.

Phase 1 delivers the button and the menu. It does **not** deliver the archive, and it
does not achieve "one download per action" — the preferred-format bundle, chart PNGs and
Fusion curves all produce multiple downloads until Phase 2 zips them. Say so rather than
promising otherwise.

### 6.4 Phase 2 — run-bound routes and the archive

Principles: **one user action, one download**; small tabular transforms of loaded results
stay client-side; persisted artefacts, native geometry, canonical rendering, multi-file
outputs and archives go server-side.

```
GET  /api/jobs/{id}/exports/design | results | mesh | log
GET  /api/jobs/{id}/exports/data/polar | balloon | vacs
POST /api/jobs/{id}/exports/geometry/step-solid | step-surface | stl | fusion-curves
POST /api/jobs/{id}/exports/charts
POST /api/jobs/{id}/exports/bundle
```

`POST` for anything doing expensive native computation or building an archive. `charts`
and `fusion-curves` return zips so each click yields one download. Mount via
`create_job_exports_router(runtime)` from `mount_jobs()` (`server/jobs/api.py:203`) —
**do not** make the existing stateless `/api/export` router reach through
`application.state`; those routes stay as the editor/design contract
(`server/exports/api.py:19`).

**Archive layout:**

```
<archive-root>/
  manifest.json
  input/design.cfg
  input/solve-request.json
  results/results.json
  artifacts/solver-mesh.msh
  logs/run.log
```

`results.json` byte-for-byte from persistence. Deliberately **excludes** STEP, STL,
charts, Fusion curves, summary text and duplicated CSVs — those are regenerable views.

**But "regenerable" is only true if regeneration is reproducible**, so the manifest must
carry provenance, not just identity: schema version, app/build version, **the Hornlab
mesher pin from `requirements-pins.txt`**, solver backend version, plots renderer version
and theme, export-contract version, and platform where relevant — alongside run UUID,
label, timestamps, status, design availability, frequency range, engine, mesh stats and
SHA-256s. Without those, a STEP or chart regenerated in a year can differ from the same
design and results. If that provenance is not carried, **rename the action "Run data
archive"** rather than implying completeness. Distinguish three states per member —
`included`, `unavailable` (with reason), `not-part-of-archive-schema` — rather than
writing a prose reason for every deliberately excluded view.

Note also that `input/design.cfg` and `input/solve-request.json` likely both embed the
design; decide which is authoritative rather than shipping the redundancy the archive
elsewhere claims to avoid.

Build on disk, member by member, then `FileResponse` and delete via a background task.
**Do not build the zip in `BytesIO`.** `allowZip64=True`, `ZIP_DEFLATED`. Global semaphore
of two plus a per-run lock; duplicate requests get `429 EXPORT_BUSY`.

**Unresolved memory design.** Results and mesh are SQLite `TEXT` rows, not disk artefacts
(`server/jobs/store.py:120`). The instruction "never hold results, mesh and the finished
zip in memory at once" **contradicts** a `load_export_snapshot()` that atomically returns
both payloads — if it returns both strings, it materialises both. This needs an explicit
design decision: a retention lease or read transaction that pins the rows without copying
them, a direct SQLite-to-temp-file streaming copy, or a temporary database snapshot.
Whichever is chosen, release the store lock before formatting or geometry generation.

**Size cap.** The earlier 2 GiB figure was arbitrary and far above anything demonstrated.
Actual sizes: the solver mesh is hard-limited to 22,000 actual-domain triangles
(`server/mesh/builder.py:44`) — ordinarily a few MB of text; a default 401×37×72 balloon
is ~1.07 M values; the theoretical maximum 401×121×241 balloon is ~11.7 M values, plausibly
tens to low hundreds of MB as JSON. No production database or representative large result
is in the repo, so the true maximum is **unverified**. Derive the cap from measured member
sizes, free disk, expected compression and tested latency against a real large-solve
fixture — do not pick a round number.

**Headers on every response:** `Content-Disposition` with **both** ASCII `filename=` and
RFC 5987 `filename*=UTF-8''…` (the current helper at `server/exports/api.py:41` emits
only the ASCII form), `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
`X-Run-Id`, and `Content-Length` when the output is a completed temp file.

**Structured errors:** `404 JOB_NOT_FOUND` ("not found or removed by retention"),
`409 JOB_NOT_COMPLETE`, `409 JOB_HAS_NO_RESULTS`, `410 RESULT_MISSING`,
`404 MESH_NOT_AVAILABLE`, `410 MESH_MISSING`, `422 DESIGN_UNAVAILABLE`,
`422 DATASET_UNAVAILABLE`, `429 EXPORT_BUSY` (+`Retry-After`),
`503 EXPORT_DEPENDENCY_UNAVAILABLE`, `500 EXPORT_FAILED`.

**Reuse as-is:** `build_step`, `build_step_solid`, `build_stl`, `build_profiles`
(`server/exports/core.py`), `server.design.textcfg.serialize()`, existing result
retrieval, the frontend frequency/summary builders, chart payload validation.
**Small fix:** `build_step_solid` is imported by the API but missing from `core.py`'s
`__all__` (`core.py:418`).

### 6.5 Coordination with the Fusion 360 CAD link

`CAD-LINK-PLAN.md` (workspace root, Phase 0 closed, Phase 1 unwritten) plans an export
path that **overlaps this design in four places**. These must be reconciled before either
is built, or v2 re-creates v1's "two export surfaces, two vocabularies" defect at the
architecture level.

**1. It is already a zip, and this document says v2 has none.** The `wglink` contract
(`CAD-LINK-PLAN.md:910`) is a bundle directory zippable as `.wglink`:

```
MyHorn.wglink/
  wglink.json      # the contract; every other file checksummed from here
  waveguide.step   # mm, link-local frame
  preview.png      # optional
```

That is the same archive machinery §6.4 specifies — manifest, checksums, member layout,
streaming. **Build it once.** Whichever lands first should own the zip/streaming/manifest
utilities and the other should consume them.

**2. It plans `POST /api/export/wglink`** (`CAD-LINK-PLAN.md:1322`) in the *design*
export router, while §6.4 proposes `/api/jobs/{id}/exports/*` for run-bound exports. Both
are defensible — wglink is design-scoped and identity-bound; run exports are
snapshot-bound — but the split must be **deliberate and documented**, because a user
seeing "Export → STEP" in two places will reasonably expect the same file. The Phase 0
artifact catalog (§6.2, item 6) must cover both surfaces.

**3. It needs a fix in code this document proposes reusing.** `CAD-LINK-PLAN.md:1328`:
`server/exports/core.py:222` calls `write_step_from_config` and **discards the returned
`CadInfo`**, which the bundle needs — "fix this first". §6.4 lists `build_step_solid` as
reusable *as-is*. Both are true only if the `CadInfo` fix lands first; sequence it before
either consumer.

**4. It has its own export identity model** — `design_id`, `lineage_id`,
`branched_from_export_id`, and an atomically allocated `export.sequence` with an
idempotency key (`CAD-LINK-PLAN.md:905`). §7.2 recommends a short UUID *because v2 has no
durable sequence*. If `export.sequence` lands, it is a better filename component than a
truncated UUID. **Do not build a second numbering scheme.** The §7.2 recommendation stands
as the interim contract precisely because it can absorb a sequence later without breaking.

Note this is a *different* ledger from the run-number ledger in the run-naming plan, which
§7.2 says to decouple from. Three numbering schemes are now in play across three plans —
worth one decision rather than three.

**Practical consequence for this feature:** STEP is the shared interface to Fusion, which
reinforces promoting both STEP variants to the top level (§4) and demoting STL. It also
means the STEP export's frame, units and tagging conventions are no longer purely a
waveguide-export concern — `CAD-LINK-PLAN.md:111, 284` fix them as part of a round-trip
contract.

### 6.6 Disagreement — does polar CSV move server-side?

The backend agent wants polar and balloon CSV generated server-side on size grounds
(worst case ≈ 401×721×3 planar, 401×121×241 spherical per `server/jobs/models.py:32`).
The audit agent notes `buildPolarCsv` already exists, is tested, and works.

**Recommendation: keep polar client-side in Phase 1** — it ships today and typical grids
are far below worst case — and **measure before moving it**. Balloon has no exporter at
all today and is the better candidate if a large-dataset path is built. Do not refactor a
working, tested builder on a hypothetical.

---

## 7. File naming

### 7.1 What v1 does, and why it is poor

Base name is `${prefix}_${counter}` (default `horn_design`, `1`) from
`src/ui/fileOps.js:186-201`; job exports use the job label instead
(`YYMMDD_<name>_<counter>`, `src/modules/simulation/naming.js:46-52`). Folder is
`YYMMDD_<name>_<counter>`. Suffixes from
`src/ui/workspace/generationArtifacts.js:7-14, 53-93`.

Problems:

- **The date is on the folder, never on the file.** Pull `horn_design_1.step` out of its
  folder and it is unattributable.
- **Design and job exports disagree** — a design export writes `horn_design_1.step` into
  `260809_horn_design_1/`; a job export writes `260809_horn_design_1.step` into the same
  folder. Two conventions, one directory, and they can collide.
- **No frequency range, formula type, or run id in any filename.** The job id appears
  only in `simulation_mesh_<jobId>.msh` — the one place it is least useful.
- **Three files end in `.txt` with three meanings**: parameter config, summary report,
  VACS spectrum. Indistinguishable in a folder without opening them.
- **The counter does not protect against overwrite** — it advances on the next parameter
  edit, and the server overwrites silently.

### 7.2 Recommendation

Put the identity **in the filename**:

```
{YYMMDD}_{design-slug}_{run-short-id}__{artifact}.{ext}
```

Examples:

```
260809_tritonia_550e8400e29b__frequency-response.csv
260809_tritonia_550e8400e29b__results.json
260809_tritonia_550e8400e29b__solid.step
260809_tritonia_550e8400e29b__solver-mesh.msh
260809_tritonia_550e8400e29b__complete-run.zip
```

The backend agent proposed a longer template carrying a full UUID and frequency range
(`…__run-550e8400-e29b-41d4-a716-446655440000__f-200-20000Hz__…`, ~150 chars). That is
unambiguous but unusable in a downloads folder and risks path-length problems on Windows.
**Recommendation: a short id in the filename; full UUID inside `manifest.json` and in the
`X-Run-Id` header.** Frequency range belongs in the manifest, not the filename.

**Use 12 hex digits, not 8.** Eight hex is 32 bits: birthday collision probability is
~0.012% at 1,000 ids and ~1.16% at 10,000. The date and slug components shrink the
practical collision domain a lot, so 8 would probably be fine for filenames — but 12
makes it negligible at no real usability cost. Either way the short id is a **filename
convenience, not an identity**; never treat it as one.

**Do not sequence this with the run-number ledger.** An earlier draft suggested waiting so
as not to commit to a naming scheme twice — that puts an unrelated ledger project on this
feature's critical path. Ship the UUID-based scheme as the stable naming contract now; a
`run_number` can later be added as display metadata or an optional extra component without
breaking it.

Sanitisation rules that must apply either way — this app runs on macOS **and** Windows:
strip control characters, replace Windows-forbidden characters with `_`, collapse
separators, strip trailing dots and spaces, cap the slug (~48 chars), and guard the
Windows device names `CON`, `PRN`, `AUX`, `NUL`, `COM1`…. Note `DesignConfig` has **no
name field** (`server/design/schema.py:599`) — "design name" means `job.label`, falling
back to the formula name, then `waveguide`.

**Unicode policy — corrected.** An earlier draft called for both NFKD/ASCII
transliteration *and* an RFC 5987 `filename*`, which is self-defeating: if the name is
transliterated to ASCII anyway, `filename*` carries nothing extra. Instead: put the
**Unicode** name in `filename*=UTF-8''…`, a conservative ASCII fallback in `filename=`,
and apply collision-resistant suffixing **after** normalisation — NFKD and
transliteration can collapse two distinct design names into one, so normalisation must
itself be tested as a collision source.

---

## 8. Failure modes to design for

1. **Retention.** Terminal jobs beyond 30 days or 1000 rows are deleted
   (`server/jobs/store.py:830`) and FK cascades take results and mesh. Without tombstones
   the API cannot distinguish "never existed" from "pruned" — say "not found or removed
   by retention".
2. **Retention racing an export.** Capture state in one short store operation; never hold
   the store lock through zip compression or CAD generation.
3. **Running job.** `409`. Mesh persistence can happen *before* completion, so
   `has_mesh_artifact` alone is not sufficient evidence a run is done.
4. **Completed row with no result** — completion is normally atomic with result
   persistence (`store.py:671`), so this means corruption: `410`.
5. **Missing mesh** is normal — persistence is intentionally non-fatal
   (`server/jobs/runtime.py:609`). Omit from the archive and record why.
6. **Legacy runs with no recoverable design** — results and archive still work; `.cfg`,
   STEP, STL must fail with the stored reason (`server/jobs/models.py:196`).
7. **Huge polar/balloon.** Format off the event loop, write rows straight to a file. No
   giant Python list of lines, no giant browser `Blob`.
8. **Duplicate/concurrent exports.** Bound concurrency. Gmsh work is already serialised
   on its worker, but unlimited queued requests still consume memory and time.
9. **Temp disk exhaustion.** Preflight sizes and free space; clean partials in `finally`;
   refuse archives above a documented cap (~2 GiB uncompressed); sweep stale temporaries
   at startup.
10. **Client disconnect** — cancel where safe, close the zip, remove the temp.
11. **Browser download blocking** — one action, one file. Zip anything multi-file.
12. **Object URL lifetime** — revoke after the click, not synchronously before the browser
    consumes it.
13. **Unicode + Windows names** — reject path separators, traversal, reserved names,
    trailing periods/spaces, control characters.
14. **Overlong paths** — cap component names well under 255; do not nest the zip under a
    second copy of an already-long name.
15. **CSV injection** — quote commas/newlines/quotes and protect label cells beginning
    `=`, `+`, `-`, `@`.
16. **Partial archive** — build to a temp suffix, close and *validate*, then expose.
    Never stream while members are still being written; a late failure would hand the
    user a superficially successful corrupt zip.
17. **Never report a write as successful without confirming it** — v1's defect. Note the
    honest ceiling in a browser: `saveBlob` confirms *handoff*, not that a file was
    written. `exported_files` should mean "download initiated" unless a File System Access
    API or a server-side workspace write confirms persistence.
18. **v1-imported jobs.** Recovered designs may be lossy or absent, and the original
    stored request may not match the modern `SolveRequest` schema. The archive needs a
    legacy schema version and should preserve the **original stored request bytes** rather
    than pretending every imported job has a native v2 `input/solve-request.json`.
19. **Windows temp-file lifecycle.** Beyond filenames: open-handle deletion,
    `NamedTemporaryFile` reopening, cleanup on cancellation, and antivirus/file-lock
    interference. This repo already treats Windows store behaviour as a first-class
    constraint — archive tests need a real Windows lane, not just a naming unit test.
20. **In-flight export ownership.** Covered in §5.2: export work must outlive the menu and
    the card, or a re-render loses its status or fires a duplicate.
21. **String centralisation.** There is no i18n framework, so this is not a blocker, but
    the design introduces many status/error/menu strings and embeds run names in ARIA
    labels. Centralise them rather than entrenching more hardcoded copy.
22. **Mesher pin.** No bump appears necessary for the Phase 1 geometry operations —
    `build_step_solid` already uses the pinned mesher CAD API and has contract tests. The
    archive manifest should *record* the pin; a bump is needed only if the export contract
    comes to require new mesher behaviour.

---

## 9. Testing

**Frontend** — extend `frontend/src/results/exporters.test.ts`: differing frequency
grids, stable ordering, smoothing annotation, CSV quoting and formula-injection
protection, cache misses through `fetchJobResults`, **exactly one download per action**,
parsing `filename*` before `filename`, surfaced structured errors, URL revocation.
Add a Jobs-panel interaction test asserting unavailable options are disabled from
existing job state and that **hovering initiates no network or generation work**.
Add coverage for `buildVacs` if it survives.

**Server (Phase 2)** — new `server/tests/test_job_exports_api.py` (routes in OpenAPI;
status/media-type/disposition/UTF-8 filename/`X-Run-Id`; every error mapping; raw result
bytes unchanged; **run geometry uses the stored snapshot, never current editor state**)
and `server/tests/test_job_export_bundle.py` (exact internal layout, byte-exact members,
valid central directory, manifest schema and hashes, omission reasons, ZIP64, temp cleanup
after success/exception/cancellation, size and disk-space rejection, concurrency limits).
Extend `test_exports_contract.py` for the shared filename/header utilities and
`test_jobs_store.py` for atomic snapshot reads and retention racing an export.

---

## 10. Open decisions

1. **Fix the frequency CSV first?** §2.4 is a live data-correctness defect, independent of
   this feature. It could ship on its own, immediately, ahead of any menu work.
   *Recommendation: yes.*
2. **Scope** — Phase 0 + 1 (contracts and the menu), or all the way through Phase 2
   (run-bound routes and the archive)?
3. **Frequency-axis join policy** — sparse `f_spl`/`f_di`/`f_z` columns, or interpolate
   onto one grid? This gates dropping impedance CSV and needs a `RESULT-CONTRACTS.md`
   amendment.
4. **Parameter config suffix** — `.txt` (what the exporter does today) or `.cfg` (what the
   design Save UI does)? They currently disagree.
5. **VACS** — drop entirely, with an `EXPORT-CONTRACTS.md` compatibility note? Reinstating
   it correctly would first require persisting complex polar pressure, which the result
   contract does not do today.
6. **Two-tier menu** — is a top level of 7 + an *Advanced* submenu the right balance, or
   do you want the top level even shorter?
6a. **Complex polar pressure** — commit to persisting it (§4.3)? This gates credible
   VituixCAD directivity export and is the single biggest open question raised by the
   interop requirement. If yes, it lands in Phase 0 and changes result storage size.
6b. **Magnitude-only FRD in the interim** — ship per-angle FRD sets now with the phase
   column *omitted and labelled*, or wait for 6a?
6c. **Fusion/`wglink` sequencing** (§6.5) — which of the two zip-producing exports is
   built first, and does the `CadInfo` fix at `server/exports/core.py:222` land ahead of
   both?
7. **Placement** — add the hover affordance on collapsed rows, or accept that exporting a
   historical run means selecting it (and loading its design)?
8. **Chart PNGs** — keep as server-rendered, returned as one zip in Phase 2?
9. **Auto-export** — fold into `Export settings…` as a *separate* format list from the
   manual preferred formats, with a sane non-empty default?

---

## 11. Review record

This document was adversarially reviewed on 2026-08-09. Most citations verified; the
corrections below were applied, and the load-bearing ones were independently re-checked
against the code before editing.

**Factual errors corrected:**

| Claim as first written | Correct |
|---|---|
| The frequency-grid defect is a v1 lesson | **v2 ships the same defect today** (`exporters.ts:43, 59`) — now §2.4 |
| Every completed v1 job silently writes five extra files | Conditional, on different triggers; two genuine near-duplicates (§3.2) |
| The auto-written raw results are the *same payload* as menu JSON | Near-duplicate, not identical — the menu format adds a wrapper |
| Fusion CSV has no header | Both files carry `# x_cm;y_cm;z_cm` (`src/export/profiles.js:12`) |
| VACS writes every point with zero phase | Polar samples only; the impedance block keeps real/imaginary |
| VACS empty output is 15 lines | ~17 |
| `saveFile` returns `undefined` on error paths | On *every* path, including success; the false-success paths are invalid filename and cancelled picker |
| Parameter config is `.cfg` | The result exporter emits `.txt`; only the design Save UI uses `.cfg` |
| STEP is the only geometry path for ICW/LOOKUP/FREEFORM | `LOOKUP` is not a v2 family; STL and profiles have no family rejection |
| STL is cheap | v2 rebuilds a full solver mesh via Gmsh + `meshio` |
| `fetchJobResults` is nearly free | Cheap on a cache hit only; a miss parses megabytes |
| Phase 1 is "days, near-zero risk" | Rescoped honestly in §6.3 |

**Recommendations reversed after review:** drop impedance CSV → keep under Advanced until
the CSV schema is fixed; drop Fusion CSV and summary text → demote, the stated
justifications were wrong; keep VACS reachable via settings → drop entirely, since
"reachable" still ships invalid data; sequence filenames with the run-number ledger →
decouple, it was a blocking dependency; footer-only placement → add a collapsed-row
affordance; 8-hex short id → 12; 2 GiB archive cap → derive from measurement.

**Contradictions the review found and this revision resolves:** the Phase 1 menu
advertised an archive that only Phase 2 can build; "one download per action" conflicted
with Phase 1's multi-download bundle; `.msh` was called a one-line win and then dropped;
the memory rule "never hold results and mesh together" conflicted with the proposed
atomic snapshot (now flagged as an open design problem, §6.4); ASCII transliteration
made `filename*` pointless; the keep list's item count was wrong.

### Measured: what polar phase actually costs

Implemented 2026-08-10. Measured on a **real Metal solve** (the default backend),
24 frequencies × 3 planes × 37 angles:

| | Metal (measured) | BEMPP (measured) |
|---|---|---|
| Phase block | 75,683 B | 75,700 B |
| Base payload | 316,535 B | 100,773 B |
| Increase | **23.9%** | 75% |

The phase block is set by `frequencies × planes × angles` and is effectively
backend-independent. The percentage is not: BEMPP's much smaller base payload
made the same 76 kB look like a 75% increase. **Quote the absolute figure.**

Scaling: ~1.26 MB on a 400-frequency sweep, and retention holds 1000 terminal
runs — so the ceiling is on the order of a gigabyte if every retained run is a
full sweep. Phase is stored unconditionally by owner decision; revisit here if
that ceiling becomes a problem.

An earlier commit message (`a61f34e`) quotes the BEMPP-derived "near enough to
double" figure. That is wrong for the default backend; this table supersedes it.

### Revision 2 — 2026-08-10, owner direction

Four changes from Magnus, and what each turned out to require:

1. **"Numeric data needs to be compatible with REW and VituixCAD."** Neither tool ingests
   the current CSV; both speak FRD/ZMA. Added FRD as a first-class export (§4.3) and to
   the top-level menu. Surfaced a hard blocker: stored directivity is dB-only, so per-angle
   FRD sets cannot carry real phase until complex polar pressure is persisted — the same
   contract change VACS needed, now promoted into Phase 0. Also established that a `.zma`
   must **not** be shipped from `Z/(rho*c)`, which is a different physical quantity from
   the electrical impedance VituixCAD expects.
2. **"STL is tessellated, prefer STEP."** STL demoted to Advanced (not deleted — it is a
   frozen format and some users print). Both STEP variants promoted to the top level.
3. **"Need FR and directivity as PNG."** Confirmed keep, and noted these are two separate
   endpoints (`/api/render-charts`, `/api/render-directivity`) that one menu item must
   call — which strengthens the case for pulling a zip endpoint into Phase 1.
4. **"Keep the Fusion 360 integration in mind."** New §6.5. `CAD-LINK-PLAN.md` overlaps
   this design in four places: it already specifies a zip bundle, it plans
   `POST /api/export/wglink` in a different router, it requires a `CadInfo` fix in
   `core.py:222` that this document assumed reusable as-is, and it introduces a third
   competing numbering scheme.

**Still unverified** (stated as such rather than guessed): real maximum archive size —
no production database or large-result fixture exists in the repo; whether anyone
externally consumes the summary text report or Fusion curves; whether the hover timings
are right, since they are design defaults rather than findings.
