# Fusion request display ownership

For 0.3.6, `shell/cadlink/fusionSolveDisplay.ts` owns automatic display for
Fusion Solve requests. Arrival selects the request and opens Results so its
Preparing progress is visible. As soon as `cad_state.preparation.ingest_id`
exists, the shared `openImported` CAD view shows that exact mesh, including for
held requests without a numbered run. Acceptance restores the retained run
setup while preserving the same on-screen mesh; completion supplies results.
No browser ingest or second solve is submitted for this display.

Before an ingestion exists the server provides no displayable geometry. Showing
the original model during STEP meshing would require an earlier server preview
artifact and its identity; this slice does not introduce a second preparation.
The preparing-model id is already part of the job read model. The accepted
return identity is also exposed as `cad_return` before and after binding.

Explicit selection, document replacement and edits win over automatic display.
A skipped/failed open stays pending, with one developer diagnostic per request
through the existing client-log endpoint. Jobs updates retry transient read
failures. Returning to CAD or explicitly returning to Results rearms navigation
only; it never rearms an obsolete document, model choice or settings snapshot.
Only a request that arrives or changes in this session (or within the
recent-completion startup window) takes over the screen; an older held request
is reached through the request list and the toolbar chip, never at start. The
held-request pop-up follows the same rule, and "Later" is remembered per
request.

`JobsCoordinator` adopts prepared models only for WG-initiated solves. Folder
arrival discovery defers to Fusion Solve for job-owned bundles and cannot
re-ingest the exact return already displayed. Historical bundles remain eligible for
initial selection, which is never meshed again in the background: it stays
selected and not prepared until the user prepares it. Inbox-capable add-ins send
through receipts/jobs; folder arrivals open by themselves unless the observed
add-in reports inbox transfer, so an older add-in, or one that has not reported
(coordination off), keeps opening them.

Binding retains the accepted bundle path and return id atomically in
`task_metadata.cad.return_identity`. The job read model projects that identity as
`cad_return`; both the display owner and current-model reader consume it, including
when the first snapshot is already bound. Older bound rows without this identity
remain readable and omit `cad_return`, preserving their response bytes.

Only the current run of an eligible request that has not been acknowledged as
displayed owns its return. Folder inventory supplies `returnId`: a new id at the
same path is a new arrival even if its directory timestamp is unchanged. Legacy
deliveries with no id fall back to the path and an export timestamp no later than
the request. Discovering the exact return already shown is idempotent; it does
not re-ingest the model or grant an old run permanent ownership of the path.
Display claims remain per job/run, and a later explicit model selection still wins.

## 0.3.7 follow-up

Plain Send receipts still belong to `receiptDisplay.ts`; legacy folder returns,
manual preparation and Onshape returns still belong to `arrivals.ts`. These
paths have different source-availability, settings-retention and legacy restore
contracts. Consolidate their intent arbitration into the imported-model owner
when changing the durable request/display protocol, preserving those contracts.
Fusion Solve does not use those ingestion paths. Preserve the current shared
artifact loader, setup restoration and generation fences rather than duplicating
them in a new coordinator. A durable server display request should also replace
the recent-completion startup window.
