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
The current preparing-model id already exists in the job read model, so no server
or OpenAPI change is needed here.

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
re-ingest those bundles after completion. Historical bundles remain eligible for
initial selection, which is never meshed again in the background: it stays
selected and not prepared until the user prepares it. Inbox-capable add-ins send
through receipts/jobs; folder arrivals open by themselves unless the observed
add-in reports inbox transfer, so an older add-in, or one that has not reported
(coordination off), keeps opening them.

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
