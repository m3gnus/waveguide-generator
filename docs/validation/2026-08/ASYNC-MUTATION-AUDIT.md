# Frontend async mutation audit (#31)

Date: 2026-08-13  
Scope: production frontend stores, coordinators, API managers, and action surfaces. Tests were read only to establish existing guarantees; `previewSocket` was intentionally out of scope.

## Rule used

An async action may mutate state after an `await` only when one of these owns the mutation boundary:

- an identity/generation check proves the result still belongs to the current intent;
- a per-key promise mutex proves no newer operation can exist;
- component cleanup invalidates the operation before it can publish;
- the mutation is explicitly attached to the captured action object rather than the current selection.

## Demonstrated races fixed

| Area | Failing interleaving | Ownership added | Regression |
| --- | --- | --- | --- |
| `CadLinkCoordinator.showIngestedMeshInViewport` | viewport intent changed while `Response.text()` was pending, after the earlier generation check | recheck imported-mesh generation after body read, immediately before parse/publish | deferred body read is superseded |
| `CadLinkCoordinator.ingest` | ingest B started; ingest A finished and cleared B's spinner/replaced its feedback | coordinator request generation, in addition to the CAD-return store's semantic ingest generation | two ingests complete out of order |
| `CadLinkCoordinator.sendToFusion` | two bridge callers completed out of order and the older result could own identity/feedback/busy | latest send request owns all post-await mutations and refresh | covered structurally by the same bridge request owner; Fusion API contract tests remain unchanged |
| Onshape send | WG design changed while the upload was pending; the old result could attach its identity to the new design | send generation plus source-design revision check before identity/status mutation | delayed upload followed by a design edit |
| Workspace settings | initial `/path` read completed after `/select` and restored the old folder | shared request generation for initial load, open, and select | select response wins over delayed initial read |
| Update dialog | refresh/install/copy completed after close and reappeared as stale feedback when reopened | dialog operation generation invalidated synchronously on close and while closed | close, resolve refresh, reopen |
| FREEFORM conversion | Cancel or unmount occurred while conversion was pending; the response still replaced the design | conversion generation invalidated by cancel/unmount | cancel delayed conversion |
| Jobs coordinator naming | user edited the run name after submit; submit completion restored the captured old baseline | compare-and-set against captured output name and submitted projection | manual name edit wins over delayed submit |
| Canonical plot cache | an evicted old request failed after a new same-key request was installed and deleted the new entry | promise identity check before cache deletion | forced eviction, replacement, then old failure |

## Audited and already safe

| Area | Existing ownership proof |
| --- | --- |
| CAD return listing, Fusion/Onshape status refresh | monotonic request refs; only the current request publishes |
| CAD-return ingest record | store `beginIngestIntent` / `applyIngest` generation contract |
| `JobsCoordinator` solve/retry calls | synchronous `submissionInFlight` ref is acquired before the first await and released by its sole owner |
| `jobsSocket` | connection generation, refresh generation, cursor/epoch checks, and validated event identity |
| Results panel primary fetch | effect cleanup (`live`) and keyed display identity prevent an old job result from painting a new selection |
| Recombined result apply | mutation checks the selected job id and replaces only the matching result |
| `fetchJobResults` | per-job in-flight promise identity; cleanup deletes only its own promise |
| Provisional results | per-job monotonic revisions reject old/duplicate deltas and request a snapshot on gaps |
| Run exports | per-job promise mutex owns busy/outcome state; notice timer checks the exact notice before clearing |
| Design save | captures the saved revision; `markSaved(revision)` leaves later edits dirty |
| Design file open/new | serialized by `busyRef`; completion is the explicit document-replacement intent |
| Export/download actions | output belongs to the captured click target; they do not write into a later selection |
| Preferences theme loading | effect cleanup prevents an unmounted theme request from publishing |
| Workspace panel renderer | renderer generation serializes asynchronous teardown/remount |

## Reviewed without speculative changes

- `ResultsPanel` export and beam/recombine flows use captured action identity; their feedback is action-level, while result mutation is job-keyed.
- `DesignFileMenu` and `useSendToCad` are serialized by the menu's same-tick `busyRef`; edits made during save/export intentionally remain descendants of the committed base identity.
- `OnshapeConnectionStatus` disables manual refresh during its initial request. The always-mounted coordinator separately generations status refreshes and performs the rate-limited connection lookup once.
- TanStack Query owns capability and update-query request lifetimes. No direct selection mutation occurs in those query functions.

No production race was changed without a concrete stale-write interleaving and a mutation-boundary ownership argument.
