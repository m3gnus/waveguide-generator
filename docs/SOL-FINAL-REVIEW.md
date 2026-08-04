## System verdict

The rebuild is architecturally coherent, but it is not ready for owner sign-off. No P0 defect was found, and the core geometry contracts do agree: server-generated `surfaceN.*` sections are consumed through header references ([core.py:208](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/preview/core.py:208), [frameScene.ts:50](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/viewport/frameScene.ts:50)); quadrants remain ATH digit-lists across UI, preview, and solve ([design.ts:525](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/stores/design.ts:525), [builder.py:103](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/mesh/builder.py:103)); scale is applied once across preview/STEP/STL ([test_scale_contract.py:56](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/tests/test_scale_contract.py:56)); and polar configuration reaches the solver context ([solveOptions.ts:51](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/stores/solveOptions.ts:51), [context.py:90](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/solver/context.py:90)). The remaining release risk is concentrated in cross-panel lifecycle ownership, job resynchronization/persistence, one expression round-trip, and fidelity aggregation. Five P1 findings should be fixed before shipping.

## P0

None found.

## P1

### P1-1 — CONFIRMED: a jobs event gap can permanently wedge live updates

On a cursor gap, the client retains its old cursor, records the future cursor as `gapTargetCursor`, and performs an HTTP list refresh ([jobsSocket.ts:256](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/api/jobsSocket.ts:256)). That refresh replaces jobs but cannot advance the cursor because `JobListResponse` contains no cursor ([models.py:177](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/jobs/models.py:177), [jobsSocket.ts:316](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/api/jobsSocket.ts:316)). Subsequent live events remain rejected. The existing test explicitly demonstrates this and recovers only by injecting a later snapshot that the production gap path never requests ([jobsSocket.test.ts:68](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/jobs/jobsSocket.test.ts:68)).

Concrete fix: on a gap, send the existing WS `resume` request using the last accepted cursor. Let the server replay retained events or return an authoritative snapshot. Alternatively add the snapshot cursor atomically to `/api/jobs` and set it after refetch. Add a regression where no unsolicited snapshot is injected.

### P1-2 — CONFIRMED: closing the Jobs dock panel disables global job functionality

Workspace panels are separate React roots that unmount when Dockview disposes them, and the reduced layout is persisted ([Workspace.tsx:27](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/shell/Workspace.tsx:27), [Workspace.tsx:108](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/shell/Workspace.tsx:108)). No custom tab is supplied, so Dockview uses its default close button ([dockviewPanelModel.js:62](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/node_modules/dockview-core/dist/esm/dockview/dockviewPanelModel.js:62), [defaultTab.js:26](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/node_modules/dockview-core/dist/esm/dockview/components/tab/defaultTab.js:26)).

`JobsPanel` alone owns:

- `jobsSocket.start()/stop()` ([JobsPanel.tsx:103](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/shell/JobsPanel.tsx:103))
- capability loading
- auto-export automation
- the global solve button and keyboard shortcut through `document.querySelector` ([JobsPanel.tsx:149](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/shell/JobsPanel.tsx:149), [JobsPanel.tsx:167](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/shell/JobsPanel.tsx:167))

Closing the tab therefore stops job events and automation and leaves the TopBar solve button inert; the saved layout preserves that state across restart.

Concrete fix: move jobs WS ownership, capabilities, solve command, and automation into an application-lifetime coordinator mounted under `Shell`. Make `JobsPanel` presentational, wire TopBar through React context/props, and provide reliable required-panel restoration or reopening.

### P1-3 — CONFIRMED: solve records are not atomically self-contained

The solve request sends only design and options ([actions.ts:62](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/jobs/actions.ts:62)). The server persists the validated request but creates the job without `script_snapshot` or design revision ([runtime.py:177](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/jobs/runtime.py:177)). After receiving the job ID, the client separately PATCHes the snapshot and label ([JobsPanel.tsx:136](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/shell/JobsPanel.tsx:136)).

A network failure after job creation leaves a real running job that cannot later load its design or perform geometry auto-exports. The server summary also omits design revision ([runtime.py:794](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/jobs/runtime.py:794)), so automation falls back to revision zero ([JobsPanel.tsx:149](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/shell/JobsPanel.tsx:149)).

Concrete fix: include label, design revision, and a versioned design snapshot in the solve request and persist them in the same transaction as job creation. Store one canonical schema-wire representation and hydrate it through `hydrateDesignDocument` when loading; do not cast server JSON directly to `DesignDocument`.

### P1-4 — CONFIRMED: constant OSSE `r0` expressions lose their source spelling in CFG output

`Expr` explicitly promises raw-source preservation and prefers `raw` during serialization ([schema.py:104](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/design/schema.py:104), [schema.py:153](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/design/schema.py:153)). UI hydration/serialization and mesher translation preserve that sidecar correctly ([designIo.ts:76](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/api/designIo.ts:76), [design.ts:484](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/stores/design.ts:484), [translate.py:24](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/preview/translate.py:24)).

The OSSE CFG writer is the exception: whenever `r0.value` is known, it constructs a new numeric diameter expression and discards `r0.raw` ([textcfg.py:721](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/design/textcfg.py:721)). Thus a constant spelling such as `6.35*2` becomes its evaluated numeric diameter after save/reopen.

Concrete fix: whenever `r0.raw` exists, emit `Throat.Diameter = 2*(<raw>)`; multiply the numeric value only when raw is absent. Add UI → schema → CFG → reopen → mesher tests for both constant and `p`-dependent expressions.

### P1-5 — CONFIRMED: aggregate frame fidelity can report measurements that were never achieved

The mesher correctly records incomplete chord measurements as `None`, with `measurement_complete=false` and an unmeasured interval count ([fidelity.py:270](/Users/magnus/Code/hornlab-workspace/hornlab-waveguide-mesher/hornlab_mesher/preview/fidelity.py:270), [api.py:1165](/Users/magnus/Code/hornlab-workspace/hornlab-waveguide-mesher/hornlab_mesher/preview/api.py:1165)). It also records `silhouette_segments_achieved` per emitted surface, including enclosure-plan surfaces ([api.py:1750](/Users/magnus/Code/hornlab-workspace/hornlab-waveguide-mesher/hornlab_mesher/preview/api.py:1750)).

The server aggregate drops nonnumeric chord measurements and defaults the result to `0.0`, which falsely means perfect measurement. It also ignores per-surface silhouette values and derives the aggregate solely from `actual_segment_counts.horn_phi` ([core.py:128](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/preview/core.py:128), [core.py:167](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/preview/core.py:167)).

Concrete fix: aggregate every emitted surface. Preserve “unmeasured” explicitly—prefer nullable achieved fields, or add mandatory completeness/unmeasured fields—and calculate minimum achieved silhouette from per-surface records. Add a cross-repository fixture containing an enclosure and a forced unmeasured interval.

## P2

### P2-1 — CONFIRMED: solve submission bypasses the capability cache and repeats native probes

`/api/capabilities` correctly runs `detect_engines` off-thread and caches it ([app.py:97](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/app.py:97)). `JobRuntime.submit`, however, calls AUTO resolution and engine construction synchronously ([runtime.py:149](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/jobs/runtime.py:149)); both independently call `detect_engines` ([registry.py:67](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/engines/registry.py:67), [registry.py:93](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/engines/registry.py:93)). Metal detection performs a real native smoke test and device probe ([metal.py:82](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/solver/metal.py:82)).

Concrete fix: inject one application-owned capability/engine registry into `JobRuntime`. Any TTL refresh must remain off the event loop.

### P2-2 — CONFIRMED: non-divisible polar angle steps are silently changed

The UI converts step to `floor(span/step)+1` samples and submits only the three-value range ([solveOptions.ts:51](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/stores/solveOptions.ts:51)). The server then derives the effective step from the endpoints and count ([models.py:67](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/jobs/models.py:67)). For 0–180° at 7°, for example, the solve uses 7.2° without telling the user.

Concrete fix: choose and enforce a policy: reject non-divisible spans, adjust/display the endpoint, or expose sample count rather than step. Add the resolved polar grid to job/result metadata and the UI card.

### P2-3 — CONFIRMED: reused viewport geometry can retain stale curvature colors

Curvature colors are optional per frame ([SurfaceMesh.tsx:17](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/viewport/SurfaceMesh.tsx:17)). When geometry byte lengths match, `SurfaceBufferManager` updates a supplied color attribute but never removes an existing one when the next frame has `colors=null` ([bufferManager.ts:20](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/viewport/bufferManager.ts:20), [bufferManager.ts:52](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/viewport/bufferManager.ts:52)). In curvature mode, that can render a new frame with the previous frame’s heatmap.

Concrete fix: delete/dispose the color attribute when colors become absent and force the required GPU buffer refresh. Test an equal-sized curvature → no-curvature update in a real WebGL browser.

### P2-4 — CONFIRMED: preview computation has no application lifecycle owner

Preview uses a module-global four-thread executor ([core.py:33](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/preview/core.py:33)). Cancelling the WebSocket worker cannot cancel an already-running executor function ([core.py:343](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/preview/core.py:343), [core.py:471](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/preview/core.py:471)), and `create_app` has no matching preview-executor shutdown hook ([app.py:123](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/app.py:123)).

Concrete fix: make the executor an app-owned preview service, reject new work during shutdown, and explicitly drain or boundedly abandon outstanding work.

### P2-5 — SUSPECTED product-policy defect; behavior CONFIRMED: opening a different file retains the previous document’s undo history

`loadDesign` replaces the design through the temporal store but never clears past/future states ([design.ts:416](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/stores/design.ts:416)). File open invokes that method and immediately treats the new document as saved ([DesignFileMenu.tsx:90](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/design/DesignFileMenu.tsx:90)). Undo can therefore cross the file boundary. No contract states whether this is intentional.

Concrete fix-spec: external file open should establish a new undo epoch and clear history. Decide separately whether “Load design” from a job is a replace-document action or an undoable edit.

### P2-6 — CONFIRMED: an all-failed auto-export is marked complete and will not retry

Automation calls `markExported` after every resolved bundle, before examining whether `files` is empty or failures occurred, and then merely reports the failures ([automation.ts:27](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/jobs/automation.ts:27)). Setting `auto_export_completed_at` excludes that job from future attempts.

Concrete fix: do not set completion when every selected format failed. For partial success, persist per-format status so only failed formats remain retryable.

### P2-7 — CONFIRMED: frontend and server advertise different product versions

The UI identifies itself as `2.4.1`, while FastAPI and `/health` advertise `2.0.0` ([TopBar.tsx:28](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/shell/TopBar.tsx:28), [app.py:27](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/app.py:27)).

Concrete fix: derive both from one build/version source and expose that same identifier in health, logs, and the frontend.

## Ship-readiness checklist

- [ ] Fix all five P1 findings and add cross-layer regressions for WS gap recovery, panel close/reset, atomic solve creation, `r0` expression spelling, and incomplete fidelity.
- [ ] Fix or explicitly accept each P2 with an owner-recorded rationale.
- [ ] Run server, frontend, shared-codec, and mesher-preview suites against the exact shipping revisions.
- [ ] Run a real-browser WebGL pass covering panel close/reopen, reconnect, keyboard controls, focus, context loss, equal-sized curvature buffer reuse, and disposal.
- [ ] `__wg2ViewportTestHook` is already reinstated and installed while the live Scene is mounted ([demandRender.ts:46](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/viewport/demandRender.ts:46), [ViewportCanvas.tsx:227](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/viewport/ViewportCanvas.tsx:227)). Freeze it with an actual-browser test: `forceFrame()` must synchronously cancel a pending rAF, execute queued tasks once, invalidate once, and restore/delete the hook on unmount ([demandRender.ts:26](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/viewport/demandRender.ts:26)).
- [ ] Align the release version and perform one final clean-tree artifact smoke test.

This review was read-only; no repository files were changed.