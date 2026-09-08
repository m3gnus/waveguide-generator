# HornLab suite review — 2026-09-07

Last verified: 2026-09-08. Review window: **2026-08-24 through 2026-09-07**, inclusive, against the immutable snapshots below. Later release commits are outside this review; do not treat these findings as an audit of a newer trunk without rechecking the named code.

## Outcome and follow-up order

**24 confirmed public-code findings: 5 fixed in this review branch, 19 open.** Six independent reviewers covered CADlink, WGlink, frontend/results/persistence, numerical contracts, packaging/releases, and separately maintained components; the integration owner checked server persistence/archive behavior, consolidated the findings and implemented the bounded fixes. This is a broad targeted review, not exhaustive certification of every source line or native CAD/solver platform.

Start with the five open P1 findings: **CAD-1, WGL-1, WGL-2, WGL-4, NUM-1**. The first four concern project/settings ownership or stale CAD evidence. NUM-1 needs a caller/convention audit before changing the coupling API. Then address mutation ordering (**FE-2, FE-4, CAD-4**) and CAD rebuild/archive continuity (**CAD-3, ARCH-1**). The remaining numerical and release issues have concrete acceptance criteria below.

The larger fixes were retained as follow-up work because they require shared ownership/generation rules, retention design, public numerical contracts, or release-workflow changes. Do not fix those by weakening identity, integrity, cancellation, or publication guards.

### Finding index

References in detailed findings are relative to the named repository and use baseline line numbers. IDs in this index are the stable handoff identifiers. “Fixed” means implemented and checked on this topic branch, not landed on trunk or released.

| ID | Priority | Status | Repository | Finding |
| --- | --- | --- | --- | --- |
| CAD-1 | P1 | Open | WG | Project switch overwrites another project’s saved solve profile |
| CAD-2 | P2 | Fixed | WG | Late Onshape return supersedes a newer design |
| CAD-3 | P2 | Open | WG | Onshape model cannot use local rebuild controls |
| CAD-4 | P2 | Open | WG | Archived-model responses reverse selection intent |
| WGL-1 | P1 | Open | WG | Automatic project open discards edits made during its fetch |
| WGL-2 | P1 | Open | Fusion add-in | Cached heartbeat authorizes a stale guarded update/return |
| WGL-3 | P2 | Fixed | WG | Return request loses the selected repeated instance |
| WGL-4 | P1 | Open | WG | Late Fusion send assigns the previous design’s identity to the current document |
| FE-1 | P1 | Fixed | WG | Full browser storage uploads stale drafts or deletes them |
| FE-2 | P2 | Open | WG | Unload upload can let an old write overwrite a new draft |
| FE-3 | P2 | Open | WG | Off-axis SPL uses the wrong plane reference without a zero-angle sample |
| FE-4 | P2 | Open | WG | Crossover edit→revert diverges displayed and persisted results |
| FE-5 | P2 | Open | WG | Failed local deletion is resurrected after restart |
| NUM-1 | P1 | Open | hornlab-sim | Engineering source velocities cross the solver boundary unconjugated |
| NUM-2 | P2 | Open | hornlab-sim | One-frequency early stop broadcasts into a complete radiation matrix |
| NUM-3 | P2 | Open | hornlab-sim | Three-element mesh layout produces incorrect aperture areas |
| NUM-4 | P2 | Open | hornlab-plots | Signed-angle DI integration cancels its own weights |
| NUM-5 | P2 | Open | hornlab-plots | Partial spherical cap silently expands its coverage |
| REL-1 | P2 | Open | WG | Update failure relaunch lacks a fresh authorization grant |
| REL-2 | P2 | Fixed | WG | WGLink ZIP can escape staging through Windows drive components |
| REL-3 | P2 | Open | WG | Release publisher rejects supported prerelease versions |
| REL-4 | P2 | Open | WG | Trunk attribution gate inspects an empty commit range |
| ARCH-1 | P2 | Open | WG | New CAD capture removes a pending run’s model archive source |
| QA-1 | P2 | Fixed | WG | Count-retention test expires as wall-clock time advances |

### Implemented fixes

- **QA-1:** Keep the count-retention fixtures inside the age-retention window using dates one and two days before the test. This preserves the production retention checks and prevents the test from changing meaning as the calendar advances.

- **CAD-2:** Check the original Onshape ingest intent before selecting a returned bundle. Share request ownership with ordinary ingestion and guard completion/loading state. Regressions cover replacing the design or selecting a different Onshape instance while the response is pending.
- **WGL-3:** Forward `instance_id` to the live Fusion status classifier before issuing a return marker. Regression covers a valid selected instance among two copies of one design and refusal of a missing instance.
- **FE-1:** Track failed browser-cache writes per namespace and use their newer in-memory value for reads/uploads until a cache write succeeds. Regressions cover stale and absent cache values, failed deletion, and resuming external cache reads after recovery.
- **REL-2:** Apply the shared Windows-strict name validator to WGLink ZIP components, reject normalized aliases and case collisions, and check containment before writing. Ten hostile self-consistent archives are refused before runtime staging is created; existing valid installation tests remain green.

The WGL-2 cache finding concerns the reviewed add-in source tip; the WG snapshot’s managed-install pin is older and does not contain that cache change.

No module SHA pin, application version, workflow permission, release tag, or trunk was changed by this review. Packaging fixes still require the normal cross-platform landing checks.

## Source and recent-work inventory

The public snapshots were fetched before review. Several shared development clones and their virtual environments were stale, so inspection used isolated worktrees. Python verification uses a clean Python 3.13.1 environment installed from WG’s locked dependencies and exact six module pins. Node is 20.20.2.

| Repository | Snapshot SHA | Commits in window reachable from snapshot |
| --- | --- | ---: |
| waveguide-generator | `90dc07dd615828a20c263ae22fa77574706a4a03` | 379 |
| hornlab-fusion-addin | `7d67d8daa3799cba4a5d38fa77bd108831504466` | 36 |
| hornlab-waveguide-mesher | `c036c62237fa65d2a0520274d5e3e9f160c7c626` | 51 |
| hornlab-metal-bem | `e7e32d0530d41ae8482ea156f2d9aca8f10b8623` | 19 |
| hornlab-beat-bem | `a74ec610d925700760d1d9e33794a390d01da488` | 54 |
| hornlab-bempp-bem | `69f813bb25496cc1d01132d36bd1005c190d80c1` | 25 |
| hornlab-sim | `6e2c1dd5f74d5eb6509e1ab1371bfda215ae3d01` | 2 |
| hornlab-plots | `893149168cdd7aa0874c6a9ebe5cb32698530b3c` | 2 |

Total: **568 commits**, including merge commits. This inventory defines breadth; it is not a claim that each commit received an independent line-by-line audit. The detailed sections distinguish new regressions from older surviving defects and already-fixed history. WG’s installed pins are listed in the packaging section; module source review is explicitly distinguished from installed-pin testing.

## Validation ledger

Validation uses the baseline SHA plus the explicitly listed review changes. Focused reviewer counts are scoped to their own probes and must not be added to aggregate counts as if they were distinct product tests. Defect-demonstration probes intentionally assert broken baseline behavior; they are not acceptance tests.

- Baseline frontend: **2,014 passed / 163 files**. Typecheck and production build passed.
- Baseline Python aggregate: **3,387 passed, 44 skipped, 21 failed** under the filesystem/process sandbox. All **21 failed tests passed** when rerun outside the sandbox using the exact-pin interpreter. An earlier rerun accidentally resolved the venv interpreter symlink into the system interpreter; that run is excluded from validation evidence.
- Baseline shared frame codec: **44 passed**. Generated dependency/version checks and ruff passed.
- Focused fixes: CAD coordinator **47 passed**; Fusion status/return **22 passed**; settings persistence **19 passed**; WGLink package **20 passed**.
- Final integrated Python: **3,420 passed, 44 skipped**, exit 0, 623.86 seconds (`python -m pytest server/tests scripts/tests -v --durations=25`). Run on macOS with Python 3.13.1 and the exact six module pins recorded below, outside the process/filesystem sandbox. Log: `wg-python-final-complete.log` in the local review evidence folder.
- Final frontend: **2,019 passed / 163 files** (`npm test -- --run`); typecheck and production build passed (`npm run build`), using Node 20.20.2. Logs: `wg-frontend-final.log`, `wg-build-final.log`.
- The preceding integrated Python run found the date-expired retention fixture: **3,409 passed, 44 skipped, 1 failed**. QA-1 corrects only the fixture dates; the focused job-store suite then passed **51 tests**, followed by the clean final aggregate above.
- Evidence is keyed to WG base `90dc07dd615828a20c263ae22fa77574706a4a03` plus the nine source/test files implementing the five fixes in this branch. File hashes were frozen before the final aggregate and verified unchanged afterward. Documentation was assembled afterward; no dependency pins or product source changed during validation.

No native Fusion timeline edit, live Onshape cloud round trip, Windows installer/extraction, full installed-upgrade qualification, or broad CPU/GPU numerical parity was performed. The full server suite exercises some real local geometry and solver paths, but does not replace those platform qualifications. The mesher was not changed, so no new ATH parity qualification is claimed.

## Detailed findings

The following sections preserve the independent baseline analyses. Statements that the reviewer made no edits refer to that original read-only pass. The finding index and implemented-fix section above override baseline wording for the five resolved findings. Reproduction artifacts referenced by filename were retained in the local review evidence folder; the descriptions include the required setup and expected behavior so follow-up work does not depend on those local files.

## CADlink review

Reviewed `waveguide-generator` at `90dc07dd615828a20c263ae22fa77574706a4a03`, using the supplied snapshot. Four confirmed defects follow. The first is a regression in the requested recent-work window; the other three remain present but originated before that window. No production code was changed.

### CAD-1 — P1: Resolve the destination project before restoring or saving its solve profile

- **Repository / primary location:** `waveguide-generator`, `frontend/src/stores/cadReturn.ts:636–638`.
- **Related path:** `frontend/src/shell/CadLinkCoordinator.tsx:1032`, `frontend/src/shell/CadLinkCoordinator.tsx:1230–1233`, `frontend/src/stores/cadReturn.ts:933–938`, `frontend/src/stores/cadReturn.ts:1063–1089`.
- **Trigger:** Work on CAD project A, then open WG-originated CAD project B through the project switcher. Have saved solve settings for B. The load handler calls `selectBundle(null)` without a lineage, and the subsequent matching-return selection also omits the lineage. `resolvedProjectLineage()` therefore keeps A's lineage even though the working design and selected return now belong to B.
- **Actual behavior / impact:** Settings are restored under A's owner. For matching source inventories, A's voltage, drivers, crossover, and other settings become B's current settings. The ingestion then correctly identifies B, but `applyIngest()` merely changes the owner and calls `saveSolveProfile()`; it does not restore B's settings. This overwrites B's durable saved profile with the wrong setup. Different inventories can instead overwrite B's profile with defaults. The geometry/project identity gates do not prevent this: the geometry legitimately belongs to B, while its solve settings belong to A. A subsequent solve can produce plausible results with the wrong drivers or voltage.
- **Reproduction:** Executed the actual store actions in the order used by the coordinator. Saved B at 8 V and A at 3 V, ran the null selection, changed the document identity to B, selected B's return without an explicit lineage, and applied B's ingest record. The selected lineage was still A before ingestion. After a store reset and explicit reopening of B, its persisted voltage was **3 V**, not its saved **8 V**. Assertions of all three observations passed.
- **Fix:** Resolve and pass the target lineage before `restoreSolveProfile()` runs. Clear the previous owner on project replacement; propagate the registry project's lineage through the WG-originated switch path as is already done for CAD-only selection. Automatic arrivals also need a positively resolved owner. If ingestion discovers a different owner, load that owner's compatible profile instead of saving the prior owner's settings over it. Preserve same-project iteration continuity only after ownership is established.
- **Verification to add:** A real project-switcher/coordinator test with distinct saved A/B voltage and driver presets; re-ingest B, reset stores, and reopen B to verify persistence. Also cover initial/reload selection and different source inventories.
- **History:** Ownership fallback introduced by `fe765402b` on 2026-08-24. The 2026-09-07 geometry identity fixes do not repair profile ownership. The existing isolation test supplies an explicit destination lineage and therefore misses the actual WG-originated switch path.

### CAD-2 — P2: Reject a superseded Onshape return before selecting its bundle

**Resolution: fixed in this review branch.** The description below records the baseline defect; see the implemented fixes and validation ledger above.

- **Repository / primary location:** `waveguide-generator`, `frontend/src/shell/CadLinkCoordinator.tsx:1658–1662`.
- **Related path:** `frontend/src/shell/CadLinkCoordinator.tsx:1632–1638`, `frontend/src/shell/CadLinkCoordinator.tsx:1021–1049`.
- **Trigger:** Start returning design A from Onshape. Before the translation/ingestion response arrives, open design B, select another CAD model, or start a newer return. Let A's response complete last.
- **Actual behavior / impact:** The operation captures an ingest generation before awaiting the server, but never checks it on success. Instead it selects the old bundle and creates a *new* generation after the response. The ensuing `applyIngest()` check is guaranteed to accept that generation. This reactivates the obsolete return, marks it ready, and switches back into CAD mode while the document identity can still be B. It can also overwrite newer CAD settings. The viewport's earlier generation may refuse the stale display, leaving conflicting mesh/selection state rather than rescuing the overwritten CAD store.
- **Reproduction:** Mounted the actual coordinator with a deferred Onshape response. Started A's return, replaced the design and assigned B's identity, and verified Parametric mode. Completing A then left B's document identity intact but selected A's return with `needsIngest === false` and changed the workspace back to CAD. The reproduction test passed.
- **Fix:** Check the original `ingestGeneration` and the relevant design/instance identity immediately after the await and before any selection, state, feedback, or mode mutation. Do not obtain a fresh token to legitimize an old response. Give completion/error/loading feedback the same request ownership guard as the Fusion ingest path.
- **Verification to add:** Deferred-response tests for A→B design replacement, two Onshape returns completing in reverse order, and switching to another imported model during the request. The latest user action must retain its store, viewport, mode, and settings.
- **History:** The success-path token reset dates to 2026-08-13/14; this is a surviving defect, not a newly introduced September regression.

### CAD-3 — P2: Provide a valid re-ingestion route for Onshape bundles

- **Repository / primary location:** `waveguide-generator`, `frontend/src/shell/CadLinkCoordinator.tsx:1520–1521`.
- **Related contract locations:** `server/cadlink/onshape/return_leg.py:227–230`, `server/cadlink/onshape/api.py:649`, `server/cadlink/api.py:902–914`, `frontend/src/design/ParamPanel.tsx:1025`.
- **Trigger:** Return a model from Onshape successfully, then change a source/rigid mesh size or Force full domain and press **Rebuild mesh**.
- **Actual behavior / impact:** The Onshape endpoint returns an absolute bundle path under its internal data area. The coordinator retains that value and sends it unchanged to the common `/api/cadlink/ingest` endpoint. That endpoint requires a configured WGLink folder and a *relative* path under its `wgreturn/` directory. An Onshape-only setup receives HTTP 409 asking for a WGLink folder; even with that folder configured, it receives HTTP 422, `bundlePath must be a relative path`. Thus a valid Onshape import cannot use the displayed local mesh-refinement controls. The sizing change marks the record stale, so solving remains blocked. Returning from Onshape again is not an equivalent workaround: `write_and_ingest_return()` rebuilds with exported default sizing and no requested preparation options.
- **Reproduction:** Executed the real `post_ingest()` handler with the absolute-path shape constructed by `RETURN_SUBDIRECTORY`. With no selected workspace it returned 409; with a selected workspace it returned 422 before ingestion. Both assertions passed. This probe used snapshot server source with Python 3.13.1 and the isolated exact-WG-pin environment, not the shared development venv; it required no CAD cloud request or mesher execution.
- **Fix:** Add a server-owned bundle/ingestion identifier route that resolves authorized Onshape artifacts internally and accepts mesh/preparation options, or a dedicated Onshape re-ingestion route. Dispatch by bundle origin in the coordinator. Do not relax the existing endpoint into accepting arbitrary absolute filesystem paths.
- **Verification to add:** Return an Onshape model, change mesh size, rebuild without configuring a Fusion workspace, and verify that the new record contains the requested sizes. Exercise Force full domain and prove no second cloud translation is required.
- **History:** Absolute internal Onshape paths and the return flow predate 2026-08-24; the incompatibility remains at the reviewed tip.

### CAD-4 — P2: Establish archive-selection intent before fetching the ingestion record

- **Repository / primary location:** `waveguide-generator`, `frontend/src/shell/CadLinkCoordinator.tsx:789–807`.
- **Related callers:** `frontend/src/shell/JobsPanel.tsx:135–139`, `frontend/src/jobs/showJobModel.ts:12`; subsequent viewport intent at `frontend/src/shell/CadLinkCoordinator.tsx:844`.
- **Trigger:** Select archived CAD run A, then run B, while A's ingestion-record request is slow. Complete B first, then A.
- **Actual behavior / impact:** `showCadJobModel()` awaits the record before advancing ingest intent, and advances viewport intent still later. Therefore A's stale completion becomes the newest intent, overwrites B's mesh/channel/driver/sweep state, and displays A's mesh. The results selection remains B because `selectJob()` sets it synchronously on click and does not undo it when the old request finishes. Users can inspect B's plots alongside A's geometry, or submit a new solve using A's restored inputs despite having selected B.
- **Reproduction:** Executed two calls to the actual `showCadJobModel()` with deferred A and immediate B record responses and valid miniature MSH responses. After B finished the active ingestion was B. After releasing A, both the CAD store's ingestion and the displayed mesh's ingest ID were A. All assertions passed. No guard exists in the `selectJob()` → `showJobModel()` caller path.
- **Fix:** Capture shared model-selection and viewport intent before the first await; verify it after every asynchronous step and before restoring stores or feedback. A later parametric selection, CAD selection, or project open must invalidate the same selection intent. Do not give an old response a new viewport generation after driver refresh.
- **Verification to add:** Two archived CAD runs resolving in reverse order, plus archived CAD→parametric selection while the fetch is pending. Assert selected results, CAD store, viewport, and solve inputs stay on the latest choice.
- **History:** The late-generation structure dates to 2026-08-20. Recent project-driver restoration increased the state restored by the same unguarded path, but did not introduce the race itself.

### Evidence and boundaries

Three targeted frontend reproduction tests passed under Node 20.20.2, using a disposable copy of the snapshot frontend with only reproduction tests/configuration added. The tests assert the observed faulty behavior; they are evidence of defects, not regression acceptance tests. Command from that copy's `frontend` directory: `node node_modules/vitest/vitest.mjs run --config review.config.ts src/stores/cadReturn.test.ts src/shell/CadLinkCoordinator.test.tsx -t REVIEW`. Result: **3 passed**, 101 unrelated tests deliberately unselected. An initial harness dependency-resolution error was corrected before this successful run. The Python endpoint probe passed both configured/unconfigured-workspace cases. No aggregate-suite or live Fusion/Onshape qualification is claimed; the integration owner runs the aggregates.

Additional code paths inspected included project registry/archive addressing, bundle/project and instance gates, imported mesh/source sizing and tag allocation, declared throat-disc reductions, post-mesh symmetry verification, imported artifact integrity and domain-plane mapping, and CAD export/handoff references. No additional numerical defect was confirmed from those paths. In particular, `server/solver/imported.py:62` now resolves declared and newly cut domain planes together; the older already-cut-half-as-full behavior is fixed history. `server/cadlink/ingest.py:744` now refuses a named return without the matching expected design; that earlier unlinked-project acceptance is also fixed history. The findings above concern distinct surviving state/transport paths.

Meshing/solver numerical reproduction and live CAD-application export behavior remain outside the evidence established by this fragment. The associated Fusion/WGLink implementation is owned by the separate reviewer.

### CAD-2 fix follow-up review

Independently inspected the integration owner's working diff against the original reviewed base. The added original-generation check immediately after `returnOnshapeToWg()` correctly rejects design replacement before `selectArrivedBundle()` can mint a fresh intent. The shared request counter also prevents a superseded operation from clearing the newer operation's busy indicator; the mounted guard covers coordinator unmount. Both current UI callers swallow rejected promises, so the guarded catch's rethrow does not presently reintroduce stale banners through those callers.

**Intermediate finding, now fixed by the final instance-selection change (historical evidence):** `frontend/src/shell/CadLinkCoordinator.tsx:1126–1129`, `selectOnshapeInstance()`, changes the target instance and resets status but does not invalidate the pending ingestion. The managed-link select at `frontend/src/shell/CadLinkPanel.tsx:582–589` remains enabled during ingestion. Start returning instance A, change to instance B before the response, then finish A: the patched success guard still accepts A and enters CAD mode. The callback's dependency list does not cancel the already-running closure. Invalidate ingestion intent when the instance selection actually changes, or compare a live instance-selection generation captured at request start before publishing. Merely comparing against the closure's captured selected instance would not fix it.

Verification: copied the patched coordinator and acceptance test into the disposable frontend review copy. Under Node 20.20.2, the parent's design-switch acceptance test passed, and a second probe asserting the still-broken instance-switch behavior also passed (**2 tests passed; 45 unrelated tests unselected**). Test filter: `superseded by a newer design|REVIEW CAD2`. This second probe is defect evidence and should be inverted for acceptance after invalidation is added.

Additional targeted stale-path coverage recommended:

1. Two overlapping Onshape returns: complete the older one while the newer remains pending; assert `ingesting` remains true, no old error/status/selection publishes, and only the newer result eventually applies. Cover both old success and HTTP failure.
2. Fusion ingest followed by Onshape return: verify the prior abort signal is fired, and an abort-ignoring old Fusion response cannot publish or clear the newer busy state. Also test the reverse ordering because both operations now share the counter.
3. Unmount with a pending Onshape return: late success and rejection must leave global CAD state and viewport unchanged.
4. Supersession after the Onshape ingest applies but while its viewport fetch is pending: select another CAD model and ensure the old mesh cannot take over. This verifies the separate viewport token beyond the newly added first-await guard.

CAD-1, CAD-3, and CAD-4 remain documented findings; this follow-up does not claim fixes for them or a full aggregate run.


## WGlink product behavior review

### Scope and evidence

Reviewed snapshot bases only:

- `hornlab-fusion-addin`: `7d67d8daa3799cba4a5d38fa77bd108831504466`.
- `waveguide-generator`: `90dc07dd615828a20c263ae22fa77574706a4a03`.

Read workspace policy versions AGENTS `2026-09-07.1` and GIT-WORKFLOW `2026-09-07.2`, using the parent's established freshness, and the WG repository instructions. Read-only product review; no production edits, commits, pushes, or other tasks. Investigated runtime dispatch, heartbeat/cache, handoff/return guards, watcher ownership, core update, return-state provenance, WG status/return endpoints, project opening, ingestion intent, and send completion. Inspected recent history and the two specified tip changes. Packaging/installer trust and the parent's CAD archive race are excluded.

**Four confirmed actionable findings follow.** Confirmation means executing snapshot control flow against deterministic boundary stubs, as described below; it does not imply live Fusion qualification.

Validation performed:

- Exact-pin review Python environment: Python 3.13.1 with the six WG dependency pins supplied by the parent. Probes load the add-in **snapshot source**, including its existing lifecycle Fusion stubs, and WG **snapshot source**, rather than substituting stale root clones. `wglink-probes.py` exercises the cache, both command handlers, the complete status classifier, and the exact return endpoint function extracted via Python AST.
- Node 20.20.2: `wglink-open-race.cjs` and `wglink-send-race.cjs` execute exact snapshot callback/function bodies with TypeScript annotations removed and IO/store boundaries stubbed. They are control-flow reproductions, not full React integration tests. The initial attempt to use the installed TypeScript package as a transpiler failed because it exposes no require-able transpiler API; the final probes need only Node and source files.
- Focused existing add-in source tests: `python -m pytest -p no:cacheprovider tests/test_wglink_addin_lifecycle.py tests/test_wglink_watch.py -q`: **89 passed in 1.19s**, using the exact-pin review interpreter and `PYTHONDONTWRITEBYTECODE=1`. The parent owns aggregate WG test evidence; those suites were not duplicated.
- Probe files are adjacent to this fragment in the review evidence directory. Run them from the workspace root using the review interpreter or Node 20 respectively. They use relative snapshot paths and temporary synthetic data, not a live CAD workspace.

### WGL-1 — P1: Revalidate automatic project opening immediately before replacing the document

**Repository:** `waveguide-generator`

**Primary location:** `frontend/src/shell/CadLinkCoordinator.tsx:1782` (guard and subsequent open at 1786).

**Supporting locations:** `frontend/src/design/openCadProject.ts:62`–65; `frontend/src/design/openCadProject.ts:38`–46.

**History:** introduced into automatic Fusion solve handling by `90dc07dd`.

**Trigger:** A clean design A is open when a Fusion solve command names project B. `unsavedChangesNow()` returns false. While the registry fetch or `/api/design/open` request is pending, the user edits A, changes its settings/name, or opens another design. When the request finishes, `openCadLinkedProject()` applies B unconditionally.

**Defect and impact:** The unsaved-work check precedes two asynchronous operations, with no document intent/generation or dirty-state revalidation at the eventual mutation. `applyOpenedDesign()` replaces the design and settings, assigns B's identity, then calls `markSaved`. Thus edits made during the wait disappear without confirmation, and newer document-opening intent is also superseded. The coordinator's revision subscription runs after replacement; it cannot preserve the overwritten work. This violates the new commit's explicit guarantee against automatic discard.

**Verification:** `wglink-open-race.cjs` executes the snapshot `openProjectForReturn` callback and `openCadLinkedProject` function. It suspends `getCadLinkedDesign`, changes A to an unsaved value of 999, then releases the fetch. Result: `opened`, B's value 2 replaces 999, and the applied document is clean. IO and the final store-application boundary are stubbed; the actual store application was independently traced at the supporting lines.

**Fix:** Separate fetch/parse from application. Capture a document-load generation and the relevant current state before starting, then immediately before `applyOpenedDesign`, synchronously verify the same document intent still owns the operation and no unsaved work has appeared. Refuse/park the Fusion command if superseded. A second check before calling the current asynchronous helper is insufficient; the check belongs after its final await. Use an explicit document generation rather than only design identity, because unrelated unlinked documents can both have null identities.

**Regression verification:** Add deferred-fetch coordinator tests for geometry edits, settings/name edits, and opening C while B is loading. Assert the newer document remains untouched, B is neither applied nor marked saved, and the command reports a retryable refusal. Retain the existing clean open-and-prepare success test.

### WGL-2 — P1: A cached heartbeat must not authorize a geometry-changing handoff

**Repository:** `hornlab-fusion-addin`

**Primary location:** `fusion-addins/WGLink/WGLink.py:1312`–1319.

**Supporting locations:** `fusion-addins/WGLink/WGLink.py:1371`; `fusion-addins/WGLink/WGLink.py:1627`–1634; `fusion-addins/WGLink/WGLink.py:1779`–1791.

**History:** regression introduced by geometry caching in `7d67d8d`.

**Trigger:** WG receives heartbeat hash H. Fusion geometry changes before a pending automatic update/return is serviced, while the cache is still valid or inside its duty-cycle delay. For example, a measurement costing one second postpones a changed-key measurement for twelve seconds; the next four-second tick still returns H despite a changed body `revisionId`. Unmanaged-body changes missed by the cheap key can retain H for the normal age window as well.

**Defect and impact:** Both `_apply_pending_handoff` and `_apply_pending_return_request` treat `snapshot['links']` as current state and compare the expected hash against its cached H. Consequently **stale expected H equals stale cached H and passes**, even though a fresh measurement would differ. The commit description's claim that a stale token merely causes a refusal is therefore not true for these consumers. The handoff proceeds to `wglink_core.update`, which rebuilds sketches/parameters; its live local-body check is diagnostic and does not implement this optimistic-concurrency refusal. Changes made after WG displayed/confirmed the sync state can be overwritten or processed without requiring a new sync decision. The return path likewise accepts a request whose stated current-state guard has become obsolete.

**Verification:** `wglink-probes.py` uses the real snapshot lifecycle harness and `_document_links`. Seed a one-second previous measurement, advance four seconds, and change the body revision. The cache returns the previous hash without calling the instrumented fresh-state function. Passing that snapshot and expected old hash to the actual return and handoff handlers calls both stubbed `send` and `update`, with zero fresh-state measurements. This proves guard bypass; native Fusion geometry destruction was not performed.

**Related cache ownership issue:** The same condition `unchanged or age < wait` also returns the previous document's state after switching documents, because the duty-cycle branch does not require the document key to match. The probe passes a different document ID and still receives the previous document signature with verdict `deferred`. `_document_links` then combines it with the new document's current identity. Keep this within the same cache fix: never publish another document's measured state as evidence for the active one. The advertised sixty-second age ceiling can also be exceeded when the duty-cycle wait reaches its separate 120-second cap.

**Fix:** Retain caching for advisory display, but perform a fresh, exact-anchor `return_state` validation immediately before an explicit guarded update/return and reject a mismatch or unavailable measurement. An explicit operation can pay this cost once; idle ticks should not. Alternatively defer the operation until fresh evidence exists, without consuming its one-shot attempt marker. Partition the cache by document or return unavailable evidence after a document switch; do not reuse the old document's values simply to satisfy the global duty cycle.

**Regression verification:** Run handlers with a cached old token, changed managed revision, changed untracked assembly geometry, and a document switch. Assert guarded operations refuse or wait and do not call mutation/export, while an unchanged exact-target operation succeeds and idle heartbeats remain cached.

### WGL-3 — P2: Forward the selected instance when requesting a Fusion return

**Resolution: fixed in this review branch.** The description below records the baseline defect; see the implemented fixes and validation ledger above.

**Repository:** `waveguide-generator`

**Primary location:** `server/cadlink/api.py:737`–744.

**Supporting locations:** `server/cadlink/api.py:751`–760; `server/cadlink/fusion_status.py:348`–360; `server/cadlink/api.py:704`–710.

**History:** present at the reviewed tip; not attributed to `90dc07dd`.

**Trigger:** A Fusion document contains two valid managed instances of the same WG design. The user selects instance B in WG and requests “Return from Fusion” or the composed pull-and-solve action.

**Defect and impact:** The ordinary fusion-status endpoint forwards `payload.instance_id` to `read_fusion_status`. In contrast, `request_fusion_return` passes the design ID but omits the supplied instance ID. The classifier therefore deliberately returns `instance_selection_required` with `link=None`. The endpoint converts that into HTTP 409 “The active Fusion document changed,” even though the document is unchanged and B is a valid explicit selection. Refreshing or selecting again cannot fix the refusal. Every repeated-instance assembly is blocked on this WG-initiated return path.

**Verification:** `wglink-probes.py` creates a synthetic fresh heartbeat with instances A and B for one design. The full snapshot classifier with `instance_id='b'` returns B. The actual endpoint function, executed with that same request and heartbeat, returns HTTP 409 before publishing any marker.

**Fix:** Pass `instance_id=payload.instance_id` into this `read_fusion_status` call, preserving the exact document/design/instance checks afterward.

**Regression verification:** Endpoint test with two same-design instances: valid selected B publishes a request targeting B; an absent, stale, or duplicate instance ID remains refused. Exercise the composed pull-and-solve UI path as well as the status selector.

### WGL-4 — P1: Do not apply a completed Fusion send to a different open document

**Repository:** `waveguide-generator`

**Primary location:** `frontend/src/shell/CadLinkCoordinator.tsx:1377`–1379.

**Supporting locations:** `frontend/src/shell/CadLinkCoordinator.tsx:994`–1001 and 1021–1053; `frontend/src/stores/document.ts:93`; `frontend/src/stores/design.ts:768`–774; `frontend/src/api/designIo.ts:157`–188.

**History:** present at the reviewed tip and predates `90dc07dd`; the new automatic project switch provides another way to encounter it.

**Trigger:** Start Send to Fusion for design A. While export is pending, open or create design B (or let a Fusion solve command open B). The always-mounted coordinator stays mounted. A's export response then arrives.

**Defect and impact:** `fusionSendRequest` only changes on another send or coordinator unmount. A document replacement does neither. The response passes the request/mounted guard and calls `setCadLink(result.identity, 'current')` against the **current** document B. It also calls `recordCommittedAthPolars(polarConfig)`, which reads and modifies the current design store rather than A's captured snapshot. B acquires A's design ID/lineage/edit version and A's polar blocks. Subsequent saves/exports can incorrectly advance A's registry project using B's geometry, defeating project provenance and return ownership checks even though the export itself correctly represented A.

**Verification:** `wglink-send-race.cjs` executes the exact snapshot send callback with its network response deferred. After starting A, switch the mocked current document to B and resolve A's response. Result: B's name remains B while its identity becomes A and its polar blocks become A's. Actual setter implementations and the coordinator's document-load subscription were inspected: there is no hidden identity validation or send invalidation on load.

**Fix:** Bind response-side store writes to the originating document-load generation, independently of request ordering and mounted state. Ignore identity/polar application after a different document replaces it. Do not simply reject every geometry revision change: a newer edit to the same logical document may legitimately need to retain the registry identity created by the export. Distinguish same-document edits from replacement, and avoid overwriting newer polar settings on the same document as well.

**Regression verification:** Deferred export tests: open B or New before A responds and assert B's identity/settings stay unchanged; then test a same-document edit and define the intended identity reconciliation without losing newer settings. Include switching automatically via a foreign Fusion solve command.

### Already-fixed history and remaining limits

- `63d8601` separates handled and suppressed handoff states; current `_on_watch_tick` continues to the return/survey channels after a suppressed refusal. Do not re-report the historical starvation defect.
- `5660a2d` tracks export announcement state per instance while caching manifest reads per bundle. The historical shared-bundle announcement suppression is already addressed.
- `fe4c65b` routes heartbeat inventory through resolved link records, matching Audit. The old false “missing body” heartbeat report is already addressed; WGL-2 concerns the subsequent cache regression.
- `0266aea` binds resampler output identity to the validated bundle before mutation; current core update invokes that check before moving the timeline. No new resampler-identity defect established here.
- `63a7c08` shares occurrence placement resolution between return fingerprinting and manifest construction. No additional transform defect established in this review.
- Latest project auto-open preserves the *initial* unsaved-work refusal and missing-project refusal. WGL-1 concerns edits/new intent during its asynchronous interval, which the existing happy-path/initial-dirty tests do not cover.
- Native Fusion interaction, long-running surface evaluation, actual timeline rebuild side effects, and platform-specific event reentrancy were not executed. No extra threading or palette defect is claimed from static suspicion alone.
- The parent's archive-pruning/capture race is intentionally not duplicated here. No remedy for it was established within this product-link review.

### WGL-3 candidate fix — independent follow-up review

Reviewed the actual uncommitted candidate diff against original WG base `90dc07dd`, limited to `server/cadlink/api.py` and `server/tests/test_fusion_status.py`. Other concurrent changes were not included in this approval.

**Verdict: WGL-3 is addressed in the reviewed candidate; no actionable issue found in this fix.** The single production change forwards `instance_id=payload.instance_id` to the real classifier. Session/liveness, exact document, design, and instance checks remain unchanged before request publication. The classifier still rejects missing or duplicated exact-instance matches rather than selecting the first one.

The added parameterized regression test uses the real endpoint function, classifier, fresh on-disk heartbeat and request publisher; only the process-presence probe is mocked. Its valid B case checks the emitted target/request ID/hash, and its nonexistent-instance case checks HTTP 409 and absence of a marker. “Missing” here means a supplied nonexistent instance ID, not an omitted API field.

Independent verification using the exact-pin review Python environment and current snapshot source:

- `python -m pytest -p no:cacheprovider server/tests/test_fusion_status.py -k 'return_request_resolves_the_selected_same_design_instance' -q`: **2 passed, 20 deselected**.
- Adjacent evidence script `wglink-f3-fix-probe.py`: **7/7 cases passed** through the real endpoint/classifier. Valid B published exactly B's design/document/session/request/hash. Nonexistent instance, wrong document, wrong design, duplicate B, expired heartbeat and absent document each returned 409 and published no request marker.

This follow-up supersedes WGL-3's open-defect status for the reviewed working-tree candidate only. The original finding remains valid at `90dc07dd`. No claim is made here about integration, the full suite, or the other findings' fixes.


## Waveguide Generator frontend review

Reviewed repository: `waveguide-generator`, snapshot `90dc07dd615828a20c263ae22fa77574706a4a03`.
Review emphasis: 2026-08-24 through 2026-09-07. All findings below remain present in that snapshot. No production files were changed, and no commits, pushes, or other tasks were used.

Policy read: AGENTS 2026-09-07.1 and GIT-WORKFLOW 2026-09-07.2, with freshness established by the parent as instructed. Repository AGENTS was also read. This is a report fragment for integration, not a claim that the aggregate suite passed.

### FE-1 — [P1] A failed browser-cache write uploads stale data or deletes the durable draft

**Resolution: fixed in this review branch.** The description below records the baseline defect; see the implemented fixes and validation ledger above.

**Repository / location:** `waveguide-generator`, `frontend/src/stores/durableSettings.ts:96–100`, particularly line 98. Related write path: lines 182–187 and 239–251.

**Status:** Confirmed with the actual snapshot `DurableSettings` class under Node 20.20.2. This is an existing defect, not newly introduced during the focus window: the relevant read/fallback logic dates to `e63a6ae20` (2026-08-17).

**Trigger:** Browser storage remains readable but rejects `setItem`, as happens with an exhausted localStorage quota. Edit a design so `writeAutosave` publishes a newer `designDraft`; the same defect affects preferences and other namespaces.

**Mechanism:** `writeCache` puts the new value in `memory` and catches the failed browser write. However, `get` returns `storage.getItem(...)` whenever reading succeeds, even when that value is stale or null. The uploader then reads this older value instead of the new in-memory draft. A stale cache sends an old draft in a PUT; an absent cache sends DELETE. On a successful response it can also clear the newer-data marker. The supposed best-effort cache therefore controls, and can erase, the durable server copy.

**Impact:** Unsaved design edits are silently lost on restart; when no browser copy exists, attempting to save the first draft can delete an existing durable draft. This contradicts the persistence layer's explicit storage-full fallback and affects the application's crash-recovery data, not just cosmetic preferences.

**Verification:** A source-transpiled Node probe supplies Storage whose reads work and whose writes throw. With cached `old-draft`, `set('designDraft', 'new-draft')` sends JSON `"old-draft"`. With no cached value, `set('designDraft', 'first-draft')` sends DELETE. Both assertions passed. No browser or backend was needed for these deterministic request-body assertions.

**Suggested fix:** Track failed cache writes per namespace and prefer the in-memory value for that namespace until a successful cache write or reconciliation. Alternatively keep the authoritative pending value independently and upload it directly. Do not translate a failed cache write into namespace deletion. Add regressions for quota failure with an existing value, with no value, and on draft recovery after restart.

### FE-2 — [P2] Unload flush can overwrite newer settings with an older request and erase the retry marker

**Repository / location:** `waveguide-generator`, `frontend/src/stores/durableSettings.ts:235` and `frontend/src/stores/durableSettings.ts:250–251`. Backend counterpart: `server/settings/api.py:20–25`.

**Status:** Confirmed client ordering defect with controlled HTTP completion/commit order. Relevant bypass dates to `e63a6ae20` (2026-08-17); marker-clearing code to `68ce410fc` (2026-08-20). Still present, but outside the focus window's newly introduced changes.

**Trigger:** A normal upload containing settings A is already in flight. The user changes the same namespace to B, then closes or hides the page before its debounce fires. `flush()` sends B immediately with keepalive. If B reaches the server before A, A is the last persisted value.

**Mechanism:** The keepalive path deliberately bypasses the per-namespace promise chain. Reading at send time does not update the body of the already-sent A request. B's success clears `.local-newer`, then A commits last. The old response does not restore that marker. At next hydration the now-unmarked local B is replaced by remote A. The backend accepts an unversioned namespace value and has no sequence guard to reject A.

**Impact:** Closing the application can silently revert a draft or settings edited immediately before closing, including overwriting the surviving browser copy on the next startup.

**Verification:** The Node probe starts the ordinary A upload, schedules B, flushes it, completes B then A, and asserts: server=A, cache=B, `.local-newer` absent. A new `DurableSettings` instance hydrates that server response and ends with A. All assertions passed. The probe models reversed network arrival; it does not claim that this order occurs on every desktop close. The server's synchronous route does not eliminate pre-handler network arrival reordering.

**Suggested fix:** Give namespace writes a monotonic revision that the backend validates, or preserve durable evidence that an older outstanding write can still supersede the accepted latest value and force a reconciliation upload on restart. Merely aborting a fetch or reading the cache later cannot retract a request the server has already received. Add a regression for old/new writes committing in reverse order and subsequent hydration.

### FE-3 — [P2] Off-axis SPL uses the wrong plane's absolute reference when the grid omits zero

**Repository / location:** `waveguide-generator`, `frontend/src/results/measurementAngle.ts:145–149`. Related early return: line 127. Backend evidence: `server/solver/result_mapping.py:605–611` and `server/solver/result_mapping.py:1050–1061`.

**Status:** Confirmed using WG snapshot backend mapping and actual frontend projection. The frontend module was introduced in `149d9331` (2026-08-31), inside the focus window.

**Trigger:** Solve a non-axisymmetric model with multiple polar planes and a supported angle grid that omits 0°, e.g. 5° to 85° in 10° steps. On the SPL card choose a secondary plane (such as vertical when horizontal is first) and an additional angle. At the nearest sampled angle, pressure can differ between these planes.

**Mechanism:** The backend's `spl_on_axis` selects pressure from plane index 0 at the sample nearest zero. Each polar plane is independently normalized. The frontend reconstructs the requested plane's absolute SPL by adding the plane-0 absolute anchor to a relative level difference from the requested plane. At a true 0° axis those planes meet, so this works. At a nonzero sample they describe different observation points and need different absolute anchors. The early return for the reference angle also returns plane-0 phase/level when the selected plane is different.

**Concrete example:** At 5°/15°, horizontal levels are 100/98 dB and vertical levels are 90/85 dB. WG maps `spl_on_axis` to 100 dB and normalized vertical levels to 0/−5 dB. `withMeasurementAngle(result, 'vertical', 15)` returns 95 dB, although the actual vertical pressure is 85 dB. The error is +10 dB in this fixture and can vary with frequency.

**Impact:** The SPL chart presents incorrect absolute secondary-plane responses as measured results. Normalized directivity maps remain internally consistent, making this discrepancy difficult to spot. This does not affect ordinary grids containing 0°.

**Verification:** `measurement_fixture.py` uses the snapshot's real `spl_on_axis`, `phase_on_axis`, `directivity`, `directivity_phase`, and `_renormalize_directivity` functions on synthetic complex pressure. It ran in the isolated Python 3.13.1 exact-WG-pin environment, with WG source imported from the review snapshot. `measurement.cjs` then executes the source-transpiled actual `withMeasurementAngle` under Node 20.20.2 and asserts the incorrect 95 dB result against the known 85 dB pressure. This is a contract/projection reproduction, not a newly solved physical model.

**Suggested fix:** Retain an absolute reference per plane/frequency, or absolute sampled pressure/SPL, in the result contract and use that to reconstruct off-axis responses. For old payloads where the reference is nonzero and per-plane levels cannot be recovered, decline the secondary-plane absolute projection with an explanation rather than applying a mathematically invalid anchor. Add a two-plane, nonzero-reference regression including phase at the nearest-angle selection.

### FE-4 — [P2] Cross-area handoff: reverting a live crossover edit leaves persisted results different from the displayed settings

**Ownership:** Found while tracing general result-cache/recombination behavior. The originating control is `CadCrossover`; hand this to the dedicated CAD frontend reviewer for deduplication and ownership. No CAD transport or project changes are proposed here.

**Repository / location:** `waveguide-generator`, `frontend/src/design/CrossoverSection.tsx:199–205`. Related cache write: `frontend/src/api/results.ts:495–496`; server persistence: `server/jobs/runtime.py:2090–2093`.

**Status:** Confirmed with a real React component probe and deferred recombine response. Backend persistence and cache behavior verified by source tracing; the React test mocks the HTTP recombine function and therefore does not itself exercise database writes.

**Trigger:** A completed run is shown with LR4. Change its slope to LR2 and allow the debounce to send the request. Before the response returns, change back to LR4.

**Mechanism:** The second edit compares equal to the still-shown LR4 result and returns without sending a restoration request. It also increments the generation, so the completed LR2 response is ignored by `onApplied`. The server still persists the LR2 result, and the real API function updates its cache before that generation check. The rail and plotted snapshot remain LR4 while durable/cached results are LR2. No error or pending indication remains. The same lack of mutation ordering also merits testing with two different outstanding edits; ignoring a stale UI callback does not order server writes.

**Impact:** A later result reload, field evaluation, or export can use a different crossover from the one the user left selected and currently sees. The interface continues to say changes apply immediately.

**Verification:** One isolated Vitest/React probe passed under Node 20.20.2 with the snapshot's installed dependencies. It renders the actual component, changes slope 4→2, advances 450 ms, changes 2→4, resolves the pending request, and asserts exactly one POST (LR2), no applied callback, and LR4 still selected with the immediate-application message. This deterministic sequence does not require reverse server completion order.

**Suggested fix:** Serialize or version recombine mutations per job and keep track of the desired spec separately from the last displayed/confirmed spec. After any outstanding mutation settles, reconcile the latest desired spec—even if it originally matched the displayed result. Cache publication and displayed state should share that revision discipline. Add a regression for edit→revert before response, not only successive successful edits.

### Evidence, scope, and limits

Reproduction artifacts are preserved in the review directory's `frontend-probes/` folder:

- `persistence.cjs`: actual TypeScript source transpilation with deterministic Storage/fetch doubles; confirms FE-1 and FE-2.
- `measurement_fixture.py`, `measurement-fixture.json`, and `measurement.cjs`: backend mapping plus frontend projection for FE-3.
- `crossover.probe.tsx` and `vitest.config.mts`: isolated React reproduction for FE-4; **1 test passed**.

The `.cjs` probes accept the installed TypeScript compiler module through `TYPESCRIPT_LIB` and run with Node 20.20.2. They assert the defective behavior intentionally; passing means the reported reproduction was observed, not that the application is correct. The custom Vitest include isolates `.probe.tsx` files and does not add tests to the parent's aggregate run. The first custom-config attempts failed during module resolution, before tests ran; after correcting the probe config the single test passed. No production dependency or config was changed.

Reviewed actual call paths included `JobsCoordinator`, jobs WebSocket event/refresh handling, provisional/final result selection and caching, autosave/document state, durable settings, design-file operations, live recombination, field-plane request generations, measurement normalization and angle projection, recent group-delay controls, output/power display helpers, and export/archive construction. Recent history was inspected, with deeper reproduction concentrated on the findings above. This is not a claim that every frontend path has been exhaustively covered.

Already-fixed history was not promoted into findings: the current design-file open path rechecks intervening edits before replacement; result selection uses keyed snapshots and cancellation of obsolete fetch callbacks; result claims are deliberately preserved before jobs appear in the list; profile-pair exports are combined into one write; recent group-delay controls read the shared preference. The existing phase-analysis comments describe sampling limitations, which were not recast as newly confirmed defects.

The parent owns the aggregate Python/frontend results and integration. Those suites were not duplicated here. No live desktop/browser quota exhaustion, full physical BEM solve, or real-network shutdown race was performed. FE-1/FE-2 are deterministic client reproductions with controlled infrastructure; FE-3 exercises real source mapping with synthetic physical quantities; FE-4 is a React reproduction with a mocked recombine response and separately traced persistence contract.

At final verification HEAD still named the original snapshot. Concurrent changes were present only in `frontend/src/shell/CadLinkCoordinator.tsx` and its test; neither was edited by this reviewer or used as evidence for these findings. Finding source files remained unchanged.

### Independent review of the FE-1 fix

Reviewed the actual working diff against the original snapshot in `frontend/src/stores/durableSettings.ts` and `frontend/src/stores/settingsPersistence.test.ts` after the parent added `failedCacheWrites`. This addendum supersedes FE-1's original status for this candidate only; the original finding remains valid against the original snapshot. FE-2, FE-3, and FE-4 remain unresolved.

**Verdict:** The original FE-1 write/upload defect is fixed. The per-namespace override returns the latest memory value after a failed cache write, including an intentional null, and a successful cache write clears the override. Ordinary cache reads continue to observe external changes. No additional defect was found in that implementation for failed non-null writes.

**Independent verification:** Ran `node node_modules/vitest/vitest.mjs run src/stores/settingsPersistence.test.ts` from the frontend directory under Node 20.20.2: **19 tests passed**. `git diff --check` for the two changed files passed. No aggregate suite was repeated.

**Test adequacy:** The new parameterized test meaningfully covers both original failure modes (stale existing cache and absent cache), checks the actual PUT payload, covers the DELETE method/body for null, and checks that recovery restores external shared-cache visibility. These checks are adequate for the original same-session FE-1 upload bug. The deletion case does not establish recovery across process restart: it performs a successful replacement write before checking recovery.

The independent review also reproduced a deletion/restart edge requiring a durable server tombstone. It is tracked once as **FE-5** in the persistence follow-up below; it is not part of the completed FE-1 same-session upload fix.


## Numerical and mesh contract review

### Scope and evidence

Read-only review against the original snapshots, with no production edits or native solves. Policy versions read: AGENTS 2026-09-07.1 and GIT-WORKFLOW 2026-09-07.2; freshness was supplied by the coordinating review. Repository instructions read for hornlab-sim, hornlab-beat-bem and waveguide-generator.

| Repository | Reviewed snapshot |
| --- | --- |
| hornlab-waveguide-mesher | c036c62237fa65d2a0520274d5e3e9f160c7c626 |
| hornlab-metal-bem | e7e32d0530d41ae8482ea156f2d9aca8f10b8623 |
| hornlab-bempp-bem | 69f813bb25496cc1d01132d36bd1005c190d80c1 |
| hornlab-beat-bem | a74ec610d925700760d1d9e33794a390d01da488 |
| hornlab-sim | 6e2c1dd5f74d5eb6509e1ab1371bfda215ae3d01 |
| hornlab-plots | 893149168cdd7aa0874c6a9ebe5cb32698530b3c |
| waveguide-generator | 90dc07dd615828a20c263ae22fa77574706a4a03 |

Review emphasis was the August 24–September 7 history and present numerical seams. The five findings below are live at these snapshots, but are **pre-existing defects, not established regressions introduced during that window**. Recent numerical changes were traced separately below. This was targeted code review and bounded fault injection, not a complete numerical qualification of every backend.

Local review evidence artifacts: `numerics-probes.py` and `numerics-probes.log`. Run with the review venv's Python; the script explicitly prepends the six module source snapshots to `sys.path`. Thus these are **snapshot-source probes**, using dependencies from the isolated exact-WG-pin environment, not assertions about whatever module happens to be installed elsewhere. Latest run exited 0 with all five defect-demonstration assertions passing. The two coupling probes replace native solving with small deterministic results; they verify wrapper behavior, not discretization accuracy. No Julia or GPU solve was started. No repository aggregate suite was duplicated.

### Confirmed actionable findings

#### NUM-1 — [P1] Convert engineering LEM velocities before applying them to solver-convention fields

- **Repository / location:** hornlab-sim, `hornlab_sim/methods/lem_to_bem.py:207`–`213`; sequential counterpart at lines 262–267.
- **Trigger:** Pass complex aperture volume velocities from this package's engineering-convention LEM/TMM methods to `lem_to_bem.solve`, as its API documents. The builder uses `U/A` unchanged in both the basis and sequential paths.
- **Defect:** The LEM methods use `s=+iω` (`methods/bandpass.py:126`, `:412`, and transfer-matrix off-diagonal terms at `methods/transfer_matrix.py:101`–`102`). Metal's outgoing field uses `exp(+ikr)` with `exp(-iωt)`. Consequently the engineering velocity must be conjugated at this boundary. The reverse boundary already does this explicitly in `methods/radiation_impedance.py`, `termination_load_from_solver_matrix`. WG also conjugates engineering channel weights in `server/solver/combine.py:329`. A common voltage reference does not remove the time-convention conversion.
- **Impact:** Complex source phase is reversed relative to the transfer field. Single-source magnitudes hide the defect; multi-aperture summation can change destructive interference into constructive interference, corrupting directivity and crossover behavior.
- **Verification:** A two-aperture linear fixture has solver transfer coefficients `[1, i]`, aperture areas `[0.5, 0.5]`, and engineering volume velocities `[0.5, -0.5i]`. Correct solver velocities are `[1, i]`, yielding zero pressure. The actual `solve` wrapper applies `[1, -i]` and returns `2+0i`. Probe output: `lem_phase_actual (2+0j) expected 0j`.
- **Fix:** Make the input convention explicit and convert engineering inputs once before either execution path. If existing callers intentionally supply solver-convention values, provide an explicit convention option and audit those callers rather than silently conjugating them a second time. Update the current unit test that merely pins unchanged `U/A`, and add phase-sensitive multi-source and delay tests for both basis and sequential execution.
- **Limit:** This finding concerns the documented direct use of engineering LEM outputs. A caller that already conjugates before calling this API compensates for it. No claim is made that the separate WG recombination path has this defect; the inspected WG path performs the conversion.

#### NUM-2 — [P2] Reject incomplete basis sweeps before building a radiation matrix

- **Repository / location:** hornlab-sim, `hornlab_sim/methods/radiation_impedance.py:177`–`191`, especially the assignment at line 191.
- **Trigger:** Supply a supported Metal `SolveConfig(on_frequency_result=...)` that returns `False` after the first frequency, while requesting multiple frequencies from `solve_aperture_matrix`.
- **Defect:** The wrapper retains the callback, allocates the matrix for the entire requested sweep, and never verifies the returned frequency axis or pressure row count. A single returned pressure row broadcasts into every requested frequency. `frequencies_hz=freqs` at line 203 then labels the fabricated rows as solved. The native single-source and multi-source callbacks really support this early stop (`hornlab_metal_bem/sweep.py:1286` and `:1692`–`1700`); it is not an invented malformed-backend condition.
- **Impact:** A cancelled/early-stopped matrix calculation can return a complete-looking, frequency-independent radiation load. Downstream FEM/LEM coupling can consume unsolved values without an exception. A longer partial sweep usually raises a broadcast error, making the one-row case particularly easy to miss.
- **Verification:** Fault-injected the native result shape produced by an early stop: one row at 100 Hz, average pressure `3-4i`, area `0.5`, and a request for `[100,200,400]` Hz. Actual result advertises all three frequencies and returns `[6-8i,6-8i,6-8i]`.
- **Fix:** Validate basis count, exact returned frequency axes, and every pressure-vector shape before filling any matrix. Reject incomplete sweeps or implement an explicitly partial result contract; do not infer completion from NumPy assignment. Test one-row early stop, multi-row partial results, reordered frequencies, and a missing basis column.

#### NUM-3 — [P2] Do not infer triangle-array orientation from a 3×3 shape

- **Repository / location:** hornlab-sim, `hornlab_sim/methods/lem_to_bem.py:317`–`326`, shared by radiation-impedance area calculations.
- **Trigger:** Supply a canonical Bempp-shaped loaded mesh with exactly three triangular elements. Its elements are `(3,3)`, with triangles in columns.
- **Defect:** The helper transposes `(3,N)` only when `N != 3`. It therefore treats columns as rows for exactly three elements. Vertices have the analogous ambiguity for a three-vertex mesh. Metal's `PureGrid` explicitly uses the Bempp layout (`hornlab_metal_bem/mesh.py:29`–`40`).
- **Impact:** Incorrect aperture area changes `U/A` and `p/Q`, or causes a valid aperture to be rejected as zero-area. This is an edge case for small/open or reduced meshes, not an explanation for large closed production meshes.
- **Verification:** Transposed the existing three-tag fixture into canonical column-major layout. Its three actual areas are `[0.5,0.5,0.5]`; the production helper returns `[0,0,0]`. The repro uses valid indices and nondegenerate triangles.
- **Fix:** Use the known grid type/layout contract or authoritative per-element areas, rather than shape guessing. If row-major convenience inputs remain supported, require an explicit layout for ambiguous shapes. Test three-element and three-vertex cases in addition to ordinary meshes.

#### NUM-4 — [P2] Use nonnegative solid-angle weights for signed polar cuts

- **Repository / location:** hornlab-plots, `hornlab_plots/complex_analysis.py:580`–`589`.
- **Trigger:** Load a supported complex directivity dataset with a signed angle axis such as `[-90,90]` and render `plot_di_coherent_vs_mag`. The loader accepts signed angles and the plot calls `_di_from_magnitude` directly.
- **Defect:** `sin(theta)` gives negative weights on the negative side of the cut. Integrating over a symmetric signed grid cancels the normalization; the `max(...,1e-30)` floor does not turn it into a valid angular measure. This is separate from the documented approximation of using a plane cut as an axisymmetric DI proxy.
- **Impact:** Even a uniform pressure field produces NaN or enormous spurious DI. The renderer filters nonfinite values, so the resulting curve can disappear instead of reporting bad input.
- **Verification:** Called the production helper with unit pressure at 37 equally spaced angles from −90° to +90°. Actual DI is `[nan]`; a normalized uniform-field proxy must be 0 dB. Existing tests only compare a 0°–180° grid with the old expression, so they do not exercise signed-angle support.
- **Fix:** Define how the two signed sides contribute to the proxy, fold/average their linear powers or use the corresponding nonnegative angular measure, and explicitly validate supported coverage. Add a constant-field invariant for signed and unsigned grids, plus an asymmetric signed pattern to prevent accidental cancellation.

#### NUM-5 — [P2] Refuse partial spherical caps instead of silently enlarging their coverage

- **Repository / location:** hornlab-plots, `hornlab_plots/derived.py:145`–`149`, in `_sphere_cell_weights_sr` used by `sphere_power_metrics`.
- **Trigger:** Pass a valid solver balloon ending at 60° (or another endpoint other than 90° or 180°). Metal explicitly supports `sphere_theta_max_deg` throughout `(0,180]`.
- **Defect:** The plot helper accepts the partial axis, classifies any endpoint at/below 90° as a hemisphere, and extends the final cell to 90°. An endpoint above 90° is similarly extended to 180°. It invents angular coverage outside the sampled cap. The declared counterpart in WG, `server/solver/directivity_index.py:101`–`109`, requires the actual endpoint to match its supported maximum; Metal's `hornlab_metal_bem/observation.py:349`–`381` clips cell weights to the actual requested endpoint.
- **Impact:** Incorrect integrated acoustic power and DI for partial balloons; plausible finite outputs conceal extrapolation into unmeasured directions.
- **Verification:** For a 7×12 constant-pressure cap covering 0°–60°, production plot weights sum to `6.2831853071795845` sr; Metal's actual-cap weights sum to `3.1415926535897922` sr. Power is therefore doubled (+3.0103 dB). This compares two actual implementation paths without running a solve.
- **Fix:** Match WG and reject endpoints other than 90°/180° if only hemisphere/full-sphere metrics are supported. Alternatively accept an explicit cap coverage, clip the final band to that endpoint, and distinguish measured-cap power from any full-sphere DI assumption. Add cross-package solid-angle and constant-pressure tests at 60°, 90°, 120°, and 180°.

### Recent changes inspected; not additional confirmed findings

- **Metal complex k / sound speed:** Traced `sweep._k_values_for_native`, CircSym's wavenumber, mesh-resolution diagnostics, sphere-power density/speed inputs, and the assembly-only helper's nonzero-imaginary-k acknowledgement. Configured speed reaches the principal inspected paths. The assembly-only Python call checks that the helper echoes the requested imaginary wavenumber. No new defect established; no native numerical parity run claimed.
- **Source motion:** Metal and Bempp axial projections both orient the axis once per source tag, preserving per-face relative signs. WG's Bempp adapter sets `source_motion` after config construction (`server/solver/bempp.py:933`–`936`), so its absence from the initial kwargs is not a missing-forwarding defect. BEAT forwards source motion at construction.
- **Ground planes:** Inspected the differing plane vocabularies, composition restrictions, image groups, placement, and surface-source multiplier. Metal counts a ground image as no extra physical radiator; Bempp combines distinct ground/symmetry planes; BEAT's image factor treats ground as one real source. These recent repairs must not be reported as still missing. Sampling below the ground is explicitly warned about, not silently asserted physical.
- **BEAT completion / cancellation:** Inspected result-count, frequency-echo and sphere-grid validation plus explicit terminal-event handling in the current sweep, and the fake-session contract tests. The older incomplete-sweep acceptance was fixed in BEAT; NUM-2 is in a different consumer wrapper. Worker lifecycle tests and native cancellation were not executed by this reviewer.
- **Mesher orientation:** Read the recent `e145f85` diff and current throat-collar/volume fallback path, following the preceding no-source abstention fix. The commit itself records remaining limits for very wide flares and side-mounted source patches. The current code still falls back to signed volume when the collar cannot judge (`hornlab_mesher/step_import.py:1010`–`1025`). These are known residual limits, not newly reproduced defects in this review; no CADlink/WGlink claim is added here.
- **WG error propagation:** Inspected adapter cleanup/final cancellation checks, source-motion forwarding, and raw-to-engineering result conversion. Bempp parallel cancellation latency is documented in the adapter; it is not evidence of swallowed cancellation. Full WG aggregate results belong to the coordinating review, not this fragment.

### Remaining uncertainty

Native CPU/GPU numerical agreement, complex-k discretization error, real ground-plane solves, and real process cancellation were outside these bounded probes. The low-level GMRES implementation has a branch that reports zero residual/convergence for a nonfinite preconditioned RHS norm; no reachable finite-input production trigger was established, so this is **not** promoted to an actionable finding. Likewise, convention behavior for callers that preconvert LEM inputs requires a caller audit before changing NUM-1. No clean bill of health for unexercised numerical paths is implied.


## Packaging, installation, update, security and release review

Reviewed Waveguide Generator at `90dc07dd615828a20c263ae22fa77574706a4a03` and the Fusion add-in at `7d67d8daa3799cba4a5d38fa77bd108831504466`, concentrating on work from 2026-08-24 through 2026-09-07. No production files, workflows, commits or refs were changed. References below are relative to the named repository. Local reproduction evidence was recorded as `release_probes.py` and `release-probes.json`; the mechanisms and results are documented below.

### Confirmed actionable findings

#### REL-1 — P2 — Failure recovery relaunches without authorization while retaining the update lock

- **Repository / location:** `waveguide-generator`, `launchers/apply_update.py:2462–2466` (`relaunch_current`); related lock scope at `launchers/apply_update.py:2853–2855` and startup rejection at `launchers/statusapp/updater.py:297–321`.
- **Trigger:** An update has stopped its parent application, then fails a pre-swap condition (for example its staged app directory is missing). The same helper is called after restoring a failed update. The updater CLI retains the installation claim across the entire transaction.
- **Defect:** `relaunch_current()` passes the original `environment` directly to the relauncher. Unlike the successful-update branch and the explicit rollback helper, it does not call `_authorized_relaunch_environment()`. The child cannot acquire the still-held lock and has no valid grant, so startup deliberately refuses it. After a failed new-app startup, the original success-path grant can also have been consumed already; reusing that environment does not authorize a second start.
- **Impact:** An update that safely cancels or restores its files nevertheless leaves the application closed. The user must reopen it after the helper exits. On macOS, observing successful exit of `open` is additionally insufficient to detect rejection inside the launched app; no native LaunchServices reproduction was performed here.
- **Verification:** The local review probe uses the real OS claim and launches a separate Python process calling the actual `recover_interrupted_bundle_update()` from the snapshot. With writable fixture cache storage, the missing-stage case returns updater exit 2, supplies **no grant**, and its child returns exit 4 with action `failed`: “nothing authorized this start.” A successful-update control on the same installation supplies a grant and its child returns exit 0/action `none`. Thus this failure is independent of sandbox cache denial. The pre-swap path is executed; the post-restore case is supported by its call into the same helper and was not separately fault-injected.
- **Suggested fix:** Authorize each safe relaunch after a refusal or completed restore, minting a fresh grant for that attempt, while retaining the lock across every remaining mutation. Do not authorize a layout with an unresolved transaction merely because its two directories exist. Reuse the common grant-aware relaunch machinery once the recovery state permits it.
- **Regression test:** Exercise the CLI-equivalent held-lock scope with a real child startup gate for a pre-swap refusal and for an injected repair/new-child failure followed by rollback. Assert the restored child actually starts and consumes its own new grant.
- **Recent provenance:** `43086bc7` added grants to the successful update and common explicit-rollback relaunch, but left this failure-path closure unchanged. Existing injected relaunchers generally record calls without executing the child startup gate.

#### REL-2 — P2 — WGLink ZIP validation permits Windows drive-relative extraction outside staging

**Resolution: fixed in this review branch.** The description below records the baseline defect; see the implemented fixes and validation ledger above.

**Follow-up status (2026-09-08): fixed in the parent's working-tree candidate and independently reviewed; see the REL-2 verification addendum below. The original finding remains below as baseline evidence. No commit or landing is claimed.**

- **Repository / location:** `waveguide-generator`, `scripts/install_wglink.py:103–115` (`_safe_member`) and `scripts/install_wglink.py:281–284` (materialization).
- **Trigger:** Install a locally supplied or cached WGLink archive containing a member such as `wglink/D:/outside-review-marker.txt`, with a matching entry in its internal provenance table, on a machine staging on C: with a writable D: drive.
- **Defect:** Validation checks only POSIX absoluteness, a literal `..`, backslashes and symlinks. It accepts the component `D:`. Materialization passes those components to platform `Path.joinpath`; Windows interprets that component as a drive switch and discards the C: staging prefix. There is no final containment check. `write_bytes()` can overwrite an existing file outside staging. Failure later in installation cleans only staging, leaving the external write intact.
- **Verification:** The real `verify_package()` accepted the crafted archive against the snapshot's actual expected version and WGLink source commit. Python 3.13's `PureWindowsPath('C:/safe/staging').joinpath(*PurePosixPath(member).parts)` produced `D:outside-review-marker.txt`; `is_relative_to(staging)` was false. **Confirmed validator/path-construction defect; no native Windows disk write was attempted.** The probe performed no external write.
- **Trust/impact limits:** This is not an unauthenticated HTTP endpoint exploit. It requires supplying the installer a crafted local/cache archive; the default clean Git-fetch/build path was not shown to produce one. The arbitrary write occurs during extraction, before Fusion loads the add-in, so it is distinct from deliberately running third-party add-in code.
- **Suggested fix:** Apply the shared Windows-strict component validation from `shared/safe_names.py`, reject normalized/case-colliding archive paths, and verify destination containment before writing. The main app updater already uses that stricter validator in `server/updates/bundle.py:460`.
- **Regression test:** Add drive-relative names, colon/ADS components, reserved Windows names, trailing-dot/space aliases and case collisions to WGLink package tests. A Windows test should assert that no sibling or alternate-drive fixture marker is created or overwritten when extraction is refused.

#### REL-3 — P2 — Release workflow cannot publish the prereleases supported by the updater

- **Repository / location:** `waveguide-generator`, `.github/workflows/release.yml:120–128`; related stable-only ordering at lines 129–143 and user-facing draft/publication steps later in the workflow.
- **Trigger:** Prepare a release commit declaring a valid prerelease such as `0.3.2-rc.1`, then dispatch the normal release workflow.
- **Defect:** The release guard splits the version into exactly three all-digit components. It rejects the prerelease before the build despite recent prerelease support in release asset names, native version fields and the update channel. The user-facing publication path also has no corresponding prerelease classification; `prerelease: true` applies only to the companion `-updates` release.
- **Impact:** The normal publisher cannot deliver an RC/beta through the beta channel. The separate RC artifact workflow rehearses installation but does not substitute for publishing a GitHub prerelease that installed clients can discover. Simply loosening the parser would leave channel classification and ordering incomplete.
- **Verification:** Executed the workflow's actual extracted Python guard with a synthetic existing `v0.3.1` tag inventory. `0.3.2` passed; `0.3.2-rc.1` raised `shared/version.json is not MAJOR.MINOR.PATCH`. No release was dispatched and no metadata was changed.
- **Suggested fix:** Use a shared SemVer parser/comparator for forward ordering, including prerelease precedence, and explicitly set the public release's prerelease flag from the declared version. Preserve immutable existing tags and the build-before-publication sequence.
- **Regression test:** Execute the guard against stable, beta, RC and stable-after-RC inventories, and assert the public release's classification separately from the companion release. Current workflow tests check the existence of `prerelease: true` anywhere, which only proves the companion is hidden.

#### REL-4 — P2 — The attribution CI gate checks an empty range on trunk pushes

- **Repository / location:** `waveguide-generator`, `.github/workflows/ci.yml:104–114`; range implementation in `scripts/check_no_ai_attribution.py:69–84`.
- **Trigger:** Push a batch to `main`, including a commit with an attribution line the checker is meant to reject.
- **Defect:** `github.base_ref` is empty for pushes, so the workflow chooses freshly fetched `origin/main` as the upstream. The checkout is the pushed main SHA; that upstream is the same commit or a later descendant. `git rev-list origin/main..HEAD` therefore contains none of the pushed commits. The script reports success for zero commits without examining their messages.
- **Impact:** This gate is ineffective on the primary landing path. A PR run may still inspect new messages, but trunk pushes are independently advertised and configured as gated.
- **Verification:** Direct source/call-path inspection: the workflow fetches main before selecting it as the comparison base, and `commits_in_range()` uses the two-dot range verbatim. This is a deterministic Git range error; no offending commit was created and no remote run was dispatched.
- **Suggested fix:** For push events, compare the event's before SHA with its after SHA, with a deliberate first-push policy; retain the PR-base comparison for PR events. Avoid using a moving remote-tracking ref as the event's historical base.
- **Regression test:** Test event-to-range selection for both push and PR payloads, then verify a multi-commit push includes every newly introduced commit rather than asserting workflow text alone.

### Diagnosis of the three reported baseline updater failures

The reported failures in `server/tests/test_apply_update.py` are environment-sensitive tests, not proof of a broken normal successful-update path:

1. `test_successful_swap_keeps_previous_layers_and_uses_injected_relauncher`
2. `test_windows_apply_skips_macos_repairs_and_injects_the_exe_relaunch`
3. `test_a_rollback_helper_waits_for_the_failed_application_before_moving_anything`

Each expects a `WG2_UPDATE_RELAUNCH_GRANT` entry, but the grant writer uses the real host's per-user cache directory. `_authorized_relaunch_environment()` catches a storage `OSError`, logs it and returns an environment without the key; the recorded baseline fails when its test indexes that missing key. The Windows-shaped tests still use the actual host for grant storage because no `system` override is passed to the lock module.

Using the supplied Python 3.13.1 exact-pin environment, **all three passed in 0.44 seconds** after patching only `launchers.update_lock.cache_root` in memory to return a writable temporary review directory. Assertions, updater logic and test bodies were unchanged. Pytest's cache provider was disabled and bytecode writing was disabled. No native code-signing or real desktop relaunch is performed by these tests.

**Final integration evidence supplied by the parent:** all **21 baseline failures passed** when rerun unsandboxed with the exact-pin environment. The named baseline update-grant failures are therefore sandbox effects, not outstanding product regressions. This is parent-supplied evidence, separate from this reviewer's three-test cache-isolation run. Tests should redirect their cache root and separately exercise write denial with the expected diagnostic. Do not remove the production grant requirement to make these tests pass.

**REL-1 remains independent:** its executable reproduction deliberately cancels an update before mutation, retains the real installation lock, uses writable cache storage, and checks an actual child startup. No grant is supplied on that path, and the child refuses to start. This was a **pre-swap cancellation reproduction**, not an executed failed-rollback reproduction. The post-restore/rollback implication comes from the same `relaunch_current()` call path; it still needs its own fault-injected regression test. The successful-update control passes in the same probe environment. The 21/21 baseline rerun does not exercise or invalidate this distinct trigger.

### npm audit triage

- `npm audit --json` on the snapshot's Node 20 dependency installation returned one high advisory for **nanoid 3.3.17**, fixed in 3.3.18: [GHSA-2v37-7h3g-55p8](https://github.com/advisories/GHSA-2v37-7h3g-55p8).
- The pinned entry is `frontend/package-lock.json:2354–2358`, marked `dev: true`; PostCSS depends on `^3.3.16`, so the fixed patch fits the declared range. The advisory concerns zero-size `customAlphabet`/`customRandom` generators. This is the advisory's severity, not an established severity for WG.
- The inspected consumer is PostCSS's `lib/input.js`: it imports `nanoid/non-secure` and calls **`nanoid(6)`**, not either affected custom-generator entry point. No WG application use of the affected functions was found. Therefore **no reachable production DoS was established**. Static packaged SPA delivery also does not run the Node build dependency server-side.
- Recommended maintenance: refresh the lock entry to the fixed compatible patch, then rerun npm audit and the existing frontend checks. Do not describe this as demonstrated remote code execution or a demonstrated app hang. The parent already owns the aggregate frontend test/build evidence; it was not duplicated here.

### Pin consistency and provenance

The isolated environment's installed `direct_url.json` commit IDs matched all six WG pins exactly:

| Dependency | Exact WG/installed commit |
| --- | --- |
| hornlab-beat-bem | `74da18cdbb12844729a154005a26110511864aa0` |
| hornlab-bempp-bem | `57b1260777c09097c879a3fe7ecb377c32f322cb` |
| hornlab-metal-bem | `e7e32d0530d41ae8482ea156f2d9aca8f10b8623` |
| hornlab-plots | `893149168cdd7aa0874c6a9ebe5cb32698530b3c` |
| hornlab-sim | `6e2c1dd5f74d5eb6509e1ab1371bfda215ae3d01` |
| hornlab-waveguide-mesher | `c036c62237fa65d2a0520274d5e3e9f160c7c626` |

The add-in pin is independently maintained in `integrations/wglink/source.json:4`: `f4e9800df78a06aee9ade4c0c56de8ed7d3d01fa`. Only `7d67d8d` lies between that pin and the reviewed Fusion snapshot for the packaged WGLink paths. Thus the most recent heartbeat/provenance improvement is not yet in a default managed WGLink install; this is a delivery distinction, not evidence that all recent correctness fixes are missing. Earlier changes advanced the pin several times, including `74452386` and `99fbbe5a`.

**Additional provenance limitation, confirmed but not counted as a separate remote vulnerability:** WGLink `verify_package()` compares the archive's stated source commit with `source.json`, then compares payload hashes with hashes from that same archive. A self-consistent substituted archive claiming the expected commit passes, as the local review probe demonstrates. The ordinary clean Git-fetch/build path supplies stronger provenance; the offline `--archive`/cache path alone does not authenticate the bytes to that commit. `scripts/build_wglink_package.py:141–161` also checks HEAD but reads working-tree files, so HEAD equality alone does not prove the packaged tree is clean. A robust offline pin guarantee requires a trusted payload digest/inventory, or verification against Git objects from the pinned source. At minimum keep the distinction between integrity and source authentication explicit.

### Known limitations and already-fixed history — not new findings

- **Recovery bootstrap delivery:** `docs/plans/STANDALONE-APP.md:373–374` explicitly records that in-app layer updates do not add or refresh the native `recovery/` layer, native launchers or Windows `._pth`. The probe confirms an app-only update preserves an old launcher and does not create `recovery/`. This is a documented installed-base limitation, not a newly discovered contradiction. Fresh-install qualification must not be presented as proof that older installations acquired the new missing-app recovery route.
- **WGLink install surface:** The managed WGLink installer is called by the source-install scripts. The standalone assembly/launch/update paths inspected do not register it into Fusion. Documentation must distinguish those routes when describing automatic WGLink setup. No native Fusion installation was exercised, so this review does not certify standalone-to-Fusion setup.
- **Update trust/file handling:** The main updater binds downloads to project release URLs, checks allowed redirects and checks digests before extraction. Its extractor rejects symlinks, unsafe Windows components and case-colliding entries and bounds expanded size. REL-2 concerns the separate WGLink extractor; it is not a claim that the app updater shares that weakness.
- The historical SPA double-extension error, one-cycle companion-release bridge, executable-bit handling and Windows Inno version-probe failures have subsequent fixes in the reviewed history. They were not re-reported as current defects.
- Full aggregate suites, native Windows extraction, macOS LaunchServices behavior, live release publication and full installed-candidate qualification were not rerun here. The independent evidence is the focused executable probes, the three targeted cache-isolated tests, installed-pin metadata inspection, npm audit and source/recent-diff tracing at the stated immutable bases.

### REL-2 fix — independent actual-diff verification, 2026-09-08

**Verdict: no new actionable findings in the reviewed REL-2 candidate.** The change closes the demonstrated Windows drive-relative escape, and the focused valid-package checks passed. REL-1, REL-3 and REL-4 remain documented and are not covered by this approval. No workflow changes were reviewed or made in this follow-up.

Reviewed the actual working-tree diff against WG base `90dc07dd615828a20c263ae22fa77574706a4a03` for only `scripts/install_wglink.py` and `server/tests/test_wglink_package.py`. Other integration edits were present and excluded from this verdict. The exact reviewed file hashes are recorded in the local evidence file `release-r2-review.json`:

- `scripts/install_wglink.py`: SHA-256 `6d0887fbd0dd64bba72a4afba28af177789283d689c404ef239a7263ee741dbf`
- `server/tests/test_wglink_package.py`: SHA-256 `983847e05fcfbd4afccf7d4fdce68b05cb0cb054725bfdc957b385ffabfd95d9`

The installer now validates every component through `shared.safe_names`, rejects names whose POSIX normalization changes their spelling, rejects complete member names that collide under case folding, and checks resolved containment immediately before writing. In particular, rejecting `:` blocks a drive switch before platform `Path.joinpath` can reinterpret it. Existing rejection of directory members and symlinks is retained. The canonical file names emitted by the package builder are compatible with these restrictions.

**Executed checks, using Python 3.13.1 with the supplied exact WG pins:**

1. `python -B -m pytest server/tests/test_wglink_package.py -q -p no:cacheprovider`, with bytecode writes disabled: **20 passed in 0.43 seconds**. This includes the ten new self-consistent malicious archives and existing deterministic-package, install, tamper repair, external-registration preservation and uninstall coverage.
2. Obtained the actual packaged source paths with read-only `git archive` from Fusion add-in commit `f4e9800df78a06aee9ade4c0c56de8ed7d3d01fa`. Built the package with the snapshot builder and actual WG source specification/version. The builder's explicit observed-commit input was the same commit used by `git archive`; no working-tree add-in files or newer add-in HEAD were substituted.
3. Verified the resulting **43-member** package, then installed and reinstalled it under a temporary path containing spaces into a scratch Fusion AddIns profile. Both calls returned `installed`. Every materialized package member matched its expected bytes, and the registration pointed to the selected interpreter. No real Fusion profile was touched and no add-in was executed.
4. Added the original `wglink/D:/outside-review-marker.txt` payload and updated its internal provenance hash, then attempted installation over the valid registration. The installer rejected it as an unsafe member, and the previously installed `WGLink.py` remained byte-identical.

These checks ran on macOS. They confirm rejection before extraction using platform-independent validation and compatibility with the actual pinned payload; they do not claim a native Windows installation run. The separate archive source-authentication limitation described above is unchanged by this path-safety fix and is not a regression introduced by it.


### ARCH-1 — P2 — A newer CAD return can erase the source of a pending run's model archive

Status: confirmed, not fixed. Repository: waveguide-generator, base 90dc07dd.

Locations: `server/workspace/archive.py:270` (`_prune_other_captured_documents` after each capture); `server/workspace/archive.py:309` (run placement reads only that pruned folder); `server/cadlink/api.py:974` (capture is asynchronous); `frontend/src/api/cadProjects.ts:224` (placement result ignored).

Trigger: ingest model A, submit a run from A, ingest changed model B before that run is archived, then archive the completed A run. Capturing B deletes A's project-level file. `place_run_cad_document` returns None for A even when its original return bundle still exists. The archive route answers HTTP 200 with placed=false and the frontend ignores that response, so the run is marked archived without the requested CAD model. A fast run can also race its own background capture.

Reproduction: call `archive_cad_document` for two different return-state digests in the same project, then `place_run_cad_document` for the first digest. Observed None; original bundle remains readable. This is an existing defect whose pruning change predates the review window (c8e7ebb3, 2026-08-23), verified on current source rather than misattributed to the last fortnight.

Repair: make run capture resolve an immutable input owned by the job/ingestion, or retain captured revisions until every referencing queued/running/unarchived run has released them. Coordinate capture completion with run placement and surface/retry a missing requested copy. Preserve the intended one-current-model project view without using it as the only archive source. Do not simply remove all pruning without a retention decision.

Acceptance tests: (1) queue A, capture B, complete/archive A and assert A bytes and matching sidecar; (2) delay background capture until after a fast solve, assert eventual copy; (3) archive retry is idempotent and never substitutes B for A.


## Persistence follow-up

### FE-5 — [P2] Failed local deletion is resurrected by the next hydration

**Location in the reviewed candidate:** `frontend/src/stores/durableSettings.ts:174–175`; related acknowledgment at lines 257–258. Test coverage gap: `frontend/src/stores/settingsPersistence.test.ts:354` and lines 379–384.

**Status:** Independently reproduced against the patched source. This is a remaining deletion/restart edge, not a reason to reject the FE-1 non-null write fix. It specifically requires readable storage whose `removeItem` fails, as modeled by the parent's third regression; ordinary quota exhaustion generally permits removal.

**Trigger and result:** Start with cached `old-draft`, reject browser writes/removals, and call `set('designDraft', null)`. The patched instance correctly sends DELETE and reads null from memory. Restart with the unchanged cache and a server response lacking `designDraft`. The new instance has no `failedCacheWrites` override; `apply()` treats the old cached draft as a never-migrated namespace and uploads it. The deleted draft is restored both locally and on the server. A persistence probe observed the exact sequence `DELETE,PUT`, with final value `old-draft`.

**Why:** An absent server namespace cannot distinguish intentional deletion from a namespace that has never been migrated. The new in-memory override solves the immediate request body but cannot survive a restart. Keeping only a `.local-newer` flag would not solve this, because that flag would still cause the stale browser value to be uploaded and may itself be unwritable.

**Suggested follow-up:** Represent deletion durably in the server settings contract (for example a recognized tombstone), or supply equivalent authoritative migration/deletion state, and make hydration suppress stale cache seeding for a deleted namespace. Add a regression that creates a new `DurableSettings` instance after successful DELETE while cache removal remains unavailable, hydrates an actual absent/tombstoned server namespace, and asserts that no stale PUT occurs. This can be tracked alongside the other contract-level persistence work rather than widening the narrow FE-1 fix.

**Evidence:** `frontend-probes/f1-followup.cjs` executes the actual patched class through TypeScript transpilation under Node 20.20.2 with controlled Storage/fetch implementations. Its assertions passed. It models restart by constructing a fresh settings instance and makes no claim to reproduce a particular browser's read-only-storage policy.


## Independent fix review

The original reviewers examined the actual implementation diffs, not only the integration summary:

- **WGL-3:** approved; two new endpoint cases and seven additional endpoint/classifier probes preserved exact-instance, design, document, live-session and duplicate-ID refusals.
- **FE-1:** approved for same-session write/upload behavior; 19 tests passed independently. The remaining deletion/restart case is separately tracked as FE-5.
- **REL-2:** approved; 20 package tests passed independently. Built the real pinned 43-member WGLink payload from Git objects, installed/reinstalled it into a scratch profile, verified every payload byte and confirmed the hostile archive leaves the existing registration unchanged. macOS host; no native Windows installation claimed.
- **CAD-2:** original design-switch guard reviewed; the reviewer additionally found instance selection did not invalidate intent. That follow-up was implemented and added to the parameterized acceptance test. Final independent verdict: accepted; both design/instance acceptance cases passed, with no remaining blocking finding in the bounded diff.


## QA-1 — P2 — Count-retention test expires with the calendar (fixed)

Repository: waveguide-generator. Location: `server/tests/test_jobs_store.py:1321`.

The count-pruning test used fixed completion dates 2026-08-08 and 2026-08-09 with the live clock and a 30-day retention policy. On 2026-09-08 both runs exceeded the age threshold, so production correctly discarded two result payloads while the test expected one. Independently reproduced: expected 1, observed 2. The preceding day the test passed, but its older run was already being removed by age, weakening its claimed coverage of the count limit.

Fixed by creating completion timestamps one and two days before the test. Both are safely inside the 30-day age window, so only the count limit can select the older result. The assertions preserving job identities, run numbers and the newer result/log are unchanged. No production retention setting or assertion was relaxed. Full job-store tests passed after correction; final aggregate evidence is above.
