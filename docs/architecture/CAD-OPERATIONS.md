# CAD operations

This is the contract for work that crosses between Waveguide Generator (WG) and a CAD
adapter such as the WGLink add-in for Fusion. It defines what an accepted operation is,
how a repeated or conflicting delivery is recognised, which attempt may record a
result, and what a recorded outcome means.

- The durable half is the `cad_operations` table in `cadlink.db`
  (`server/cadlink/store.py`, schema 12). The file keeps the format older releases
  open; see "Existing installations".
- The executable half, meaning the kinds, shapes, digest and vocabulary, is
  `server/cadlink/operations.py`.

This page and that module must agree. Change them together.

**Status of this cut.** The store, the identity rules and the vocabulary below are in
place. The solve-command outcome ledger lives in the store, and Fusion's solve commands
are delivered through it (see "Solve-command delivery"). WG and its add-in speak
delivery version 3 for the heartbeat and Fusion-bound requests; WG's separate
`solveCommandDelivery` capability is 4 while its request consumer runs (see "Delivery
version"). Every request is its own file, and there is no single-slot marker and no twin.
The adapter follows the order
in "Fusion-bound mutations", marks an operation as applying before it writes, and
settles what an interrupted session left. The backend owns a solve operation end to end
(see "Setup revisions" and "Preparation"): its retained snapshot, its setup revision,
its fenced preparation stages, its approvals and its bound request. A later step adds a
field to a request only under a new digest version.
WG-produced return and handoff files are now accepted into this store before
publication. Fresh version-3 heartbeats now settle their document evidence,
applying marks, recent outcomes, and exact last-request trace durably.

## Operation kinds

| Kind | Target: what it addresses | Inputs: immutable references | Needs a live CAD session |
| --- | --- | --- | --- |
| `receive_snapshot` | `{}` | `{bundle_path, manifest_sha256}` | No |
| `prepare_and_solve` | `{}` | `{return_id, bundle_path, manifest_sha256}` | No |
| `request_return` | `{document_id, design_id, instance_id, expected_baseline}` | `{}` | Yes |
| `insert_link` | `{destination, export_id}` | `{}` | No; it can request a new document |
| `update_link` | `{document_id, design_id, instance_id, expected_baseline}` | `{export_id}` | Yes |

- **Fields.** Every field is a required string. All must be non-empty except
  `prepare_and_solve.return_id`, which a legacy single-slot solve marker may omit (the
  empty string).
- **No other field is accepted.** A transport field such as a session token or
  `requestedAt` is refused rather than silently hashed.
- **`expected_baseline`** is `{kind: "document_signature_hash", value}`: the
  document-wide signature the adapter reported when WG prepared the request.
- **`destination`** is exactly `{kind, value}`. Its kind is `document`, whose
  value is the active document ID from a live version-3 heartbeat, or
  `new_document`, whose value is the request ID. With no live heartbeat WG
  requests a new document rather than guessing a stale destination.
- **Exact targets.** A request that addresses an existing instance (`request_return`,
  `update_link`) names that instance exactly. "The one matching link in this design" is
  not a target. That resolution survives only for legacy single-slot markers, whose rows
  are marked legacy.
- **Inserts.** `insert_link` has no instance yet. Its target is the destination
  document plus the export identity. A redelivered insert is recognised by that export
  identity already stamped on a link.
  No `insert_link` row existed before this cut, so replacing its former
  `{document_id, export_id}` shape does not reinterpret a stored digest-version-1 row.
- **Solves and snapshots** address nothing in CAD, so their target is empty and
  everything they need is an input. Later inputs join under a new digest version: a
  snapshot id and a setup revision for a solve, and an import intent for a received
  snapshot. A received snapshot's one producer is a delivery over HTTP (see "Delivery
  over HTTP"); it records no intent yet.

## Identity

- **Operation ID.** The producer chooses it, and it stays stable across retries and
  redelivery. For a Fusion solve command it is the `commandId`. It is an opaque,
  non-empty string, and one ID space covers every kind.
- **Canonical request digest.** `sha256:` followed by the hex SHA-256 of the UTF-8 bytes
  of this document:

  ```json
  {"digestVersion": 1, "inputs": {}, "kind": "prepare_and_solve", "target": {}}
  ```

  - `target` and `inputs` are the normalised objects from the kinds table.
  - It is serialised with sorted keys, `,` and `:` as separators, no whitespace and no
    ASCII escaping.
  - Strings are hashed exactly as delivered, with no Unicode normalisation, trimming or
    case folding.
  - The key order a producer sent does not matter. An extra field is refused before
    hashing.
- **Digest version.** Stored with each row (`digest_version`). Only version 1 exists. A
  later canonicalisation gets a new number; a row keeps the version it was accepted
  under, and a delivery must be compared under that version.
- **The baseline is part of the identity.** A refreshed baseline makes a new operation
  with a new ID, never a rewrite of the old one. The signature is document-wide, so an
  edit anywhere in the returned scope conflicts. That is deliberately conservative.

## Delivery

**Local trust boundary.** The server binds only to loopback. A missing `Origin`
header is allowed, so any local process running as the same user can call the
ordinary API; changing that API's authentication is a product decision, not a
property of this file transport. The request inbox relies on the user's profile
permissions and, on POSIX, owner-only (`0700`) modes on `ipc/wglink`, its
`.wg-solve-requests` inbox, and the data directory that contains `cadlink.db`.
WG applies that tightening once per process to directories it owns. It leaves
an existing directory unchanged when its configured data path contains a
symbolic link, or when the filesystem refuses the optional mode change.
On Windows these paths live under the per-user profile and WG does not attempt
ACL changes here.

A WG-bound command is handled in this order:

1. read the delivery;
2. validate its identity and payload;
3. persist the operation, or recover it;
4. acknowledge the delivery;
5. execute or resume.

A command is accepted only once its identity, digest, target and inputs are committed.

| Delivery | Result |
| --- | --- |
| Same operation ID, same digest | `recovered`: the existing operation, with whatever it has already recorded |
| Same operation ID, different digest or kind | `conflict`: the delivery is rejected. The stored operation is untouched, and its result is not the answer to this delivery. |
| Different operation ID, identical inputs | A separate, explicit request |

- **"Restartable"** means accepted work is recovered after the WG backend restarts. It
  does not mean work continues after the app and its backend have exited.
- **Transactions are short.** Each store call is one transaction. None is held open
  while Fusion recomputes or a mesher runs.

## Attempts and fencing

- **Generation.** Each row carries `attempt_generation`, which is 0 when the operation
  is accepted.
- **Claim** is a conditional update. It succeeds only when the caller names the current
  generation and the operation is `received`, `processing` or `needs_user_input`. It
  increments the generation and sets `processing`.
  - A second consumer holding the same generation loses.
  - Recovery takes over by claiming with the generation it read.
- **Recording an outcome** is also conditional on the generation, and it never touches a
  terminal row. A result from an obsolete attempt changes nothing and cannot revive a
  cancelled operation.
- **Job ids stick.** A job id, once attached, is never cleared.
- **Solve commands.** The backend prepares each solve command as an attempt of its own,
  claiming it first (see "Preparation"). Nothing else records their outcomes: the outcome
  route an earlier build's browser called records nothing (see "Solve-command
  compatibility").

## States

| State | Meaning | Terminal | Claimable |
| --- | --- | --- | --- |
| `received` | Accepted; no attempt has started | no | yes |
| `processing` | An attempt holds the current generation | no | yes, as a takeover |
| `needs_user_input` | Waiting for the user: unsaved work, a source review, no setup yet | no | yes |
| `accepted` | Done. For a solve, its job exists | yes | no |
| `rejected` | Can never proceed as requested | yes | no |
| `cancelled` | The user dismissed it | yes | no |
| `recovery_required` | Reserved. A CAD mutation began and its completion evidence is missing | no | no |
| `cancel_requested` | The user dismissed it while an attempt holds it; the attempt records `cancelled` at its next step | no | no |

- **Transient is not an outcome.** "Cannot proceed now" never means "can never
  proceed". A transient failure is retried inside the attempt.
- **`recovery_required`** applies only to a CAD mutation (`insert_link`,
  `update_link`). It is never retried automatically, and it is not a rejection. It is
  settled only by reading the document (a reconciled `accepted`) or by the user
  (`cancelled`). The state is reserved now so the vocabulary does not change when the
  adapter starts to report it.

## Outcomes

`reason` holds a **reason code**, or nothing. A reason that is not a code is refused.

| Code | State | Meaning |
| --- | --- | --- |
| `baseline_conflict` | `rejected` | The document no longer matches the expected baseline, and there is no evidence that this operation already applied |
| `target_not_exact` | `rejected` | The target does not resolve to exactly one instance |
| `snapshot_invalid` | `rejected` | The snapshot fails verification, or no longer matches the operation's inputs |
| `snapshot_unavailable` | `rejected` | A received snapshot's bundle stayed unreadable for 24 hours (see "Delivery over HTTP"). Sending it again is a new operation |
| `setup_required` | `needs_user_input` | No setup revision is bound yet; a CAD-authored model never borrows the open project's |
| `findings_need_review` | `needs_user_input` | The preparation has blocking findings not yet approved on that preparation |
| `frame_confirmation_required` | `needs_user_input` | An unlinked (CAD-authored) snapshot whose project has not confirmed the solver frame it was prepared in (see "Unlinked solver frame") |
| `preparation_failed` | `needs_user_input` | Retaining or meshing failed in a way another attempt can overcome: a worker crash, a timeout, a return that is not in the WGLink folder and has no retained copy |
| `engine_unavailable` | `needs_user_input` | The engine the setup names cannot take this record; the message names the capable engines |
| `submission_refused` | `needs_user_input` | The jobs system refused the request, or submitting it failed without creating a job |
| `update_restart_pending` | `needs_user_input` | An update restart was approved while the preparation ran, so nothing was submitted. Queued again once no restart is pending (see "Preparation", "Update restart") |
| `interrupted` | `needs_user_input` | The backend stopped, or the answer was lost, while an attempt held the operation |
| `ready_to_solve` | `needs_user_input` | Prepared, and waiting for the user to start the solve |
| `superseded` | `cancelled` | A newer unstarted update for the same exact link replaced this request |
| `expired` | `cancelled` | An insert remained unclaimed for 30 minutes |
| `session_changed` | `cancelled` | A return request named a Fusion session that is no longer current |
| `publication_failed` | `cancelled` | The operation was stored but its request file could not be published |
| `adapter_refused` | `rejected` | Fusion explicitly refused this exact request and no document evidence says it applied |
| `adapter_not_started` | `cancelled` | Fusion discarded a leftover claim with neither evidence nor an applying mark |
| `adapter_failed` | `rejected` | Fusion reported over the live protocol that the request failed before it changed the document (a mutation that failed while executing is `recovery_required` instead) |

The three rejections are final because a refreshed baseline, target or snapshot is a new
operation. Every `needs_user_input` code keeps the operation, and another
preparation can proceed.

`outcome_json` is an object with at most these fields:

- `message`: a human-readable explanation;
- `reconciled`: `true` when the outcome was recovered from the document rather than
  executed;
- `evidence`: with `reconciled: true`, the observed `{operation_id, export_id}`. Its
  `operation_id` must be this operation's.

A reconciled outcome is always `accepted`.

## Fusion-bound mutations

These rules bind every adapter that mutates a CAD document for WG.

1. **Reconciliation comes before the baseline check.**
   - On any delivery or redelivery, the adapter first reads the operation's evidence on
     the exact target: the operation ID stamped next to the export identity.
   - If that evidence shows this operation already applied, the outcome is `accepted`
     with `reconciled: true`, and nothing mutates.
   - The baseline is checked only when there is no such evidence.

   Checking the baseline first would turn a lost acknowledgement into a false conflict,
   because the operation's own write is what changed the document.
2. **Reconciliation is a read.** It never runs the update, not even a "no-op" update,
   because that path writes to the document.
3. **Evidence is the last write.** The operation ID is stamped after every mutation
   phase. An operation that began mutating (marked before its first write) but has no
   completion evidence is `recovery_required`.
4. **Without evidence, a changed baseline is `baseline_conflict`.** It is reported
   before mutating, never as an overwrite.
5. **The target and the baseline are re-checked immediately before the first write.**
   The adapter resolves the exact target first. It then hands the baseline check to the
   mutation itself, which runs it after its last read and before its first write. A
   check made earlier can be outrun by the reads in between.

`fusion_mutation_precheck()` in `server/cadlink/operations.py` states this order in code.

**How WGLink carries this out.**

- **The target.** An update names the document, the exact instance and the baseline;
  without all three WGLink changes nothing. A handoff that names no instance is an
  insert. It is refused when the active document already links that design, because
  "the one matching link" is not a target.
- **The evidence.** Update and Insert stamp the operation ID beside the export identity
  as their last write.
- **The applying mark.** Immediately before its first write, a WG operation is marked on
  the root component: `applying_operation`, holding `{operation_id, kind, instance_id,
  export_id}`. The mark is removed once the evidence is written, and a later completed
  change of the same instance also removes it. A mark whose operation left no evidence
  is `recovery_required`. The operation is never run again, and the heartbeat publishes
  the mark as `document.applyingOperation`, which WG reports as `recoveryRequired`.
- **Leftover claims.** A claim (see "WG-produced Fusion requests") that an interrupted
  session left behind is hidden from every listing. The first tick of the next session
  settles each one, and only reads the document to do it: evidence on a link means it
  applied, the mark means recovery is required, and neither means it never started. The
  claim is removed, and nothing is run again.

## Ordering

- Explicit solve requests stay separate. A later request never silently erases an
  earlier one.
- CAD mutations are serialised per document, with exact instance targeting inside it.
- **Supersession.** An update that has not started may be superseded only by a newer
  update of the same exact target: the same document and instance. An insert, or an
  update of another instance, is never superseded, and neither is a request that has
  started. For a file-delivered request, "started" means the add-in has claimed it.
  - WG withdraws the older file when it publishes the newer one, logs it, and names it
    in the export response (`cadHandoffSuperseded`), which the UI reports.
  - The add-in drops the older of two such files it finds, which covers one WG could not
    remove, and names it in the heartbeat with the outcome `superseded`.

## Setup revisions

A setup revision is everything a solve of one snapshot needs except the snapshot itself:
the drive channels with resolved driver numbers, the voltages, mesh sizes, skipped
sources, exterior-only, the combine and passive-cardioid settings, the preparation
options (`area_drift_overrides`, `symmetry_mode`, `surface_deviation_mm`) and the solve
options, the engine included. Which library driver a channel's numbers came from is kept
beside them (`driver_references`) for the record; the numbers are what a retry
reproduces, because the driver library has no revisions.

- **Immutable and content-named.** `cad_setup_revisions` holds each revision once; the
  same content is the same revision (`POST /api/cadlink/setup-revisions`). A revision
  names no snapshot, and is validated as a complete solve request before it is stored.
- **The engine is the user's.** A setup carries the engine selected in WG's solver
  selector, never one CAD Link chose. If that engine cannot take the record, the
  operation waits (`engine_unavailable`) with the capable engines named, and nothing
  switches engines silently. Recalling a run does not change the selector.

## Project setups

A solve Fusion sends for project B is prepared from B's own setup, while the editor keeps
whatever project is open. Nothing on the backend reads the live UI:

- **Recorded by the frontend.** As the user changes a project's solve settings, the
  frontend records them as that project's setup for its source inventory
  (`PUT /api/cadlink/project-setups`, `{lineageId, inventory, setup}`); the latest
  recording is the project's. The solver selection is recorded the same way
  (`PUT /api/cadlink/solver-selection`, `{engine}`).
- **The operation keeps its settings.** The revision an attempt selects is recorded on the
  operation before it meshes, so it survives a preparation that fails. A follow-up that
  names no revision -- a frame confirmation, an approval, a retry -- continues with the
  revision the operation holds, or its last preparation's, so approvals given on that
  preparation still apply. A named revision always wins: an action that chooses settings
  ("Use these settings and solve", a new engine after `engine_unavailable`) sends one.
- **Resolved from the snapshot.** A preparation of an operation that has never had a setup
  revision, and names none, takes the snapshot's project as ingestion files it -- the lineage of the solver anchor instance's
  WG design or, when the anchor names no design, the one its Fusion document already has;
  never a new claim -- and that project's setup for exactly the snapshot's sources (id,
  canonical role and required, as the returns listing states them). A recorded setup this
  build cannot use is `setup_required` too.
- **Across exports.** When returns require `source-identity-v1`, each source id is the
  CAD-authored identity of that logical source, so the next export of the same sources
  finds the same setup, and a source whose identity was reassigned does not inherit it.
  The inventory key is unchanged, so setups recorded for returns without the feature keep
  their exact key.
- **Whose it is.** An operation's summary names its snapshot's document and, when WG knows
  it, the project (`snapshot.documentName`, `snapshot.projectLineageId`), so the UI can
  offer to open that project for its settings; the `setup_required` message names the
  document as well.
- **The engine is the one selected in WG.** A recorded setup keeps its engine only until
  the selection says otherwise; the setup actually used is itself a setup revision, which
  the operation names.
- **None yet** means `setup_required`: a first-time model waits for the user to choose
  its settings, and never borrows another project's.
- **The polar grid** of the request is widened to what the ingestion derived for the
  snapshot, as the frontend does; the runtime refuses a narrower one.

## Unlinked solver frame

A return with no WG instance -- a model drawn from scratch in CAD -- carries no throat
frame, so nothing in it says which way it radiates. It is not solvable until its solver
frame is confirmed, once per project. WG infers the frame from the geometry and
preselects it, so confirming it is normally one press of Solve.
`server/cadlink/solver_frame.py` is the executable half of this section, and
`server/cadlink/frame_infer.py` the inference.

- **The frame.** The model axis that points out of the mouth, one of
  `+z -z +x -x +y -y`, is the solver's +Z; the origin is the model's own. Two contracts
  exist.
  - **`cad-solver-frame-v2`** (every new preparation) also fixes the roll. The solver's
    +Y is the model's **up**, so the horizontal polar plane (solver x-z) contains the
    forward axis and is perpendicular to up. Up is the CAD document's up axis when the
    return states it (`coordinate_system.document_up` under `document-up-v1`); otherwise
    CAD +Z, or +Y when the forward axis is ±Z. A forward axis parallel to the stated up
    takes the other of +Y and +Z, recorded as `forward-parallel-to-document-up`. The
    transform's rows are (up × forward, up, forward). `+z` is the identity under every
    rule, so the modelled frame, and every declared (reduced) domain, keep exactly the
    transform they had.
  - **`cad-solver-frame-v1`** turned each axis by the minimal rotation, keeping the
    perpendicular axis the two frames share. Records and confirmations made under it keep
    that meaning: a v1 record still resolves, previews and solves as v1 while its v1
    confirmation stands. A project confirmed under v1 is not asked again: for the same
    `export_frame`, its v1 axis is carried forward to a v2 preparation, which is meshed
    in it and preselects it (`source: "carried"`), and Solve confirms it under v2. It is
    never taken as confirmed before that. The frontend fixture
    (`frontend/src/viewport/solverFrame.fixture.json`) pins the v1 matrices; the frontend
    applies whatever matrices the server hands it.
  - `spec_matrix` is the only producer of these matrices: preparation meshes with it,
    the mesh cache key holds the complete frame, the ingestion record states it, and the
    preview and the confirmation read it, so the preview is the solved frame.
- **Frame requirement.** A confirmation holds for the contract, the manifest's
  `coordinate_system.export_frame` (absent = `root-component`) and, under v2, the stated
  document up (`document_up`, null when none). With the axis these fix the whole
  transform. A snapshot written in another component's coordinates, or stating another
  up, is a different frame and is confirmed again. A return declaring a reduced domain
  (`assembly.domain`) states its planes in the modelled frame, so it allows only `+z`.
- **Confirmed per project.** `cad_frame_confirmations` holds the latest confirmation per
  key: the snapshot's project (`lineage:<id>`), or the exact snapshot
  (`snapshot:<manifest hash>`) when it belongs to no project (an unsaved CAD document).
  The key and requirement come from the ingestion record, never from a request. It is
  not part of a setup revision, which any client records. No row means unconfirmed;
  nothing is written when a store opens. Each row also records the complete transform,
  its up and where that came from, and the suggestion it agreed with (`provenance`
  `suggested`) or overrode (`chosen`) (`frame_json`, null on rows written before it).
- **The automatic suggestion (M1e).** One pure function over the full model in CAD
  coordinates: the record's solver mesh, mirrored back across its domain planes (WG's
  own cuts are provisional and never constrain it) and mapped back through the inverse
  of the frame it was meshed in. Sources come from the production source tags, never
  from area matching. Three kinds of evidence score all six axes: role-weighted source
  normals (HF 9 : MF 3 : LF 1, passive cardioid 0, averaged within a role first), a
  role-weighted far-observer visibility survey with small tilts, and the largest
  envelope opening the sources reach. The result is automatic only when the vote share
  is at least 0.60, the normalised lead at least 0.25, and at least two evidence types,
  one of them geometric, support the winner at a minimum strength. Otherwise it asks,
  with the reason in words, as it does with no HF or MF source, unvalidated source
  identity, sources that cannot be seen from outside, an unreliable surface
  orientation, or an unrestricted winner the snapshot does not allow (it never
  promotes a weaker allowed axis). Weights and thresholds are frozen under the
  algorithm version.
  - It runs inside the explicit commands: backend preparation computes it once the
    preparation is recorded, and `POST /api/cadlink/ingest` starts it after answering.
    It is cached in `cad_frame_suggestions` by snapshot and algorithm version; a survey
    that could not run is not cached.
  - It preselects; it never confirms. **Precedence:** a confirmed frame whose identity
    matches, then a v1 confirmation carried forward, then an automatic suggestion the
    snapshot allows, else nothing. When a
    snapshot's automatic suggestion disagrees with the confirmed frame, the preview
    carries a non-blocking notice and the confirmed frame still holds.
- **Preparation.** An unlinked snapshot is meshed in its project's confirmed frame (or
  the axis a v1 confirmation carries forward), or as modelled (`+z`) until one is, and its record states the frame
  (`normalisation.solver_frame`: contract, axis, up, up source, stated document up,
  requirement, allowed axes, matrix). Right after the preparation is recorded, and before
  findings and approvals, an unconfirmed frame waits as `frame_confirmation_required`;
  the preparation it made is the preview's geometry. Confirming `+z` resumes that
  preparation; any other axis makes a new one, and approvals never carry to it. A
  prepare request may name `frameAxis`, the axis WG showed when Solve was pressed: an
  unlinked snapshot is then solved only along it, and a confirmation changed elsewhere
  in the meantime stops at `frame_confirmation_required`, saying so, instead of changing
  the axis solved. A
  preparation is resumed only in the frame, and under the contract, this attempt would
  mesh in. `+z` is never written into the ingest options, so no mesh cached before
  this contract is made again.
- **Axial drive.** An `axial` channel is driven along the record's observation axis,
  solver +Z, which is the chosen CAD forward direction; a source facing back along it is
  flipped to drive outward, as for a model modelled along +z.
- **Every submission.** The jobs system refuses, at submission, an unlinked record whose
  frame is not the confirmed one under the same requirement
  (`frame_confirmation_required`), so `/api/solve`, a retry, a CAD operation's own
  submission and a recovered bound request all meet it. A record prepared before the
  contract states no frame and is never taken as confirmed; it is prepared again. A
  refusal with that code releases the binding and keeps the reason.
  - **One exception: jobs already queued.** A job accepted before an upgrade to this
    contract and still queued is requeued by `JobRuntime.start` as it was submitted; it
    is not submitted again, so it is not re-gated. Every later solve of that model is.
  - **Headless solves.** `server/cli/solve.py` submits through the same runtime, so a
    headless solve of an unlinked ingestion is refused until its frame is confirmed. The
    frame is confirmed only in WG's CAD Link panel or through the route below, for
    example `PUT /api/cadlink/solver-frame` with `{"ingestId": "wgi_…", "axis": "+z"}`.
- **Routes.** `GET /api/cadlink/solver-frame?operationId=|ingestId=` answers every axis's
  `solverFromAssembly` and `previewFromRecord` (the matrix that turns the record's
  geometry into that axis's frame) and its up, the record's contract and frame, the
  requirement, the project's confirmation (with its recorded frame), the snapshot's
  `suggestion` (`status` `automatic`/`ask`/`unavailable`, `axis`, `confidence`,
  `reason`, `reasonCode`, `algorithm`, per-evidence scores), `preselected`
  (`{axis, source: "confirmed" | "carried" | "suggested"}` or null) and `differs` (the non-blocking
  notice, or null). It computes the suggestion if the import's survey has not finished.
  `PUT /api/cadlink/solver-frame` `{operationId | ingestId, axis}` confirms it (422 for a
  linked snapshot or an axis the snapshot does not allow). Confirming prepares nothing,
  so the update-restart latch does not apply. `POST /api/cadlink/ingest` meshes an
  unlinked return in its project's confirmed frame; the request names none.
- **Linked snapshots** are unaffected: they are solved in their anchor's frame and never
  asked.

## Preparation

A solve operation is prepared by the backend, in stages. The UI issues
`POST /api/cadlink/operations/{id}/prepare` (a setup revision; whether to submit; the
blocking findings the user reviewed, and on which preparation) and observes.

A manual Solve starts with `POST /api/cadlink/operations` and
`{operationId, ingestId}`. The backend resolves that immutable ingestion record to its
content-addressed `.wgreturn`, refuses `snapshot_not_retained` when the copy is gone,
and accepts a `prepare_and_solve` operation with its snapshot recorded immediately.
The request's internal `bundle_path` is `ingest/<ingestId>`: it binds the digest to the
exact ingest rather than naming an exchange folder that can disappear. Repeating the
same pair recovers the operation; reusing the id for another ingest is
`operation_conflict`. A UI then stores its setup revision and prepares the operation
through the same route and stages as a solve requested by Fusion.

| Stage | What is done | Committed as |
| --- | --- | --- |
| `received` | Accepted. The snapshot is retained in WG's storage before the delivery is acknowledged; a return that cannot be read yet keeps its delivery for a while (see "Consuming a delivery") | the operation row, `snapshot_json` |
| `validating` | The retained copy is found, or made now for an operation received before retention | `snapshot_json` |
| `preparing-mesh` | The retained snapshot is ingested and meshed with the setup's options | an ingestion record, published under the attempt's fence |
| `ready` | The preparation is recorded, with its blocking findings | `cad_preparations`, `preparation_id` |
| `submitted` | The request is bound, then submitted under `cad-solve:<id>` | `request_json`, then `accepted` with the job |

- **One attempt at a time.** Each preparation claims the operation (a new generation).
  Every stage write, the ingestion record's publication, the approvals an attempt
  records and the outcome are conditional on that generation. A later preparation takes
  the operation over; the earlier attempt's next write is refused and it stops without
  committing a stage, a record or an outcome. (Ingestion claims a project lineage and an
  archive name before it publishes; an obsolete attempt can leave such a claim, which
  the next preparation of the same return reuses.)
- **Dismissal.** `POST .../cancel` first reconciles a solve with the jobs store.
  - If its submission key already made a job, the operation follows the job: it is
    `accepted` with it, and the user cancels the job in the jobs list.
  - If its request is bound, so a job may exist, and the jobs store cannot be read, the
    route answers 409 and dismisses nothing: WG cannot confirm yet whether a job exists.
    That holds whether the operation is idle or an attempt is submitting it. A cancelled
    operation is never reconciled later, so dismissing it then could leave a job running
    under an operation that reads "cancelled".
  - Otherwise it cancels an idle operation at once, and fences a running attempt, which
    then records `cancelled` whatever it found. The one exception is a job the attempt
    had already created: the job exists, the operation is `accepted` with it, and the
    user cancels the job in the jobs list.

  A CAD mutation under way is not dismissed; it may need recovery from the document
  instead. An attempt that fails unexpectedly leaves its operation waiting
  (`preparation_failed`), never held.
- **Retained snapshot.** The snapshot a solve command names is retained when the command
  is received, before its delivery is acknowledged, under
  `<data>/imports/bundles/<manifest hash>.wgreturn`. The operation stores the hashes, and
  the copy's place follows from them. Preparation reads that copy, never the exchange
  folder, so a return accepted before its folder was removed still prepares after a
  restart. The copy leaves out the captured CAD document, which is not geometry. An
  operation whose return has no copy and is not in the folder waits
  (`preparation_failed`).
- **The snapshot's own project.** The backend prepares into the project the snapshot
  belongs to, never a model that is open, so the ingest's project gate is given the
  solver anchor instance's WG design and that exact instance, resolved as
  `snapshot_project` resolves them (see "Project setups"). A snapshot whose anchor names
  no design, authored in CAD, names none, as before.
- **Approvals.** A blocking finding is approved on one preparation
  (`POST .../approvals`, `{preparationId, findingIds}`, or `approvals` on the prepare
  request), and submitted as `<report_sha256>:<finding_id>`. Only findings that
  preparation reported as blocking can be approved. A preparation of the same snapshot,
  setup revision and meshing semantics is resumed, not made again, so approvals given on
  it apply. Anything else is a new preparation, and a waiver never carries to it.
- **The binding point.** The exact solve request is bound immediately before it is
  submitted, and from then on never changes. A recovery submits exactly the bound
  request, whatever setup was chosen since. Only a submission that created nothing
  releases the binding, so the user can change the setup: a refusal by the jobs system,
  or a failure after which the submission key names no job (the jobs system writes the
  key with the job). A request that may have a job -- a crash, a jobs database that
  cannot be read -- stays bound.
- **Recovery.** At startup, and before every preparation, an operation whose submission
  key already made a job is `accepted` with that job; a submission-key conflict is
  settled the same way. An operation an attempt held when the backend stopped is taken
  over and waits (`interrupted`). Startup recovery only reads the jobs database; the
  jobs runtime starts on its own.
- **Delivery.** The backend is the one consumer of Fusion's solve commands. About once a
  second it collects the delivered commands (retaining each snapshot before the delivery
  is acknowledged) and starts preparing every operation no attempt has touched, from its
  project's setup, submitting when it is ready. An operation whose delivery is kept,
  because its return cannot be read yet, is started once it is retained or once its
  delivery is given up. It starts only an operation still
  untouched at the generation it listed, so it never takes over the user's own attempt,
  and an operation waiting for the user is not retried unasked; the UI issues `prepare`,
  `approvals` and `cancel` and observes. Without a WGLink folder nothing is collected,
  because nothing could be retained. A failing pass is logged once per distinct error and
  the loop backs off to half a minute while it persists. Operations an older build left
  untouched are prepared at the first start after the upgrade, as the user asked when
  sending them. Nothing else consumes them: `GET /api/cadlink/solve-command` answers that
  nothing is pending (see "Solve-command compatibility"). `WG2_CAD_DELIVERY=0` turns the
  loop off (the test suite does).
- **The WG request inbox (schema 4).** `.wg-solve-requests/<operationId>.json` carries a
  Send (`kind: receive_snapshot`) as well as a Solve (`kind: prepare_and_solve`);
  schema 3 is a Solve and stays current, schemas 1 and 2 are refused as outdated. WG
  advertises `solveCommandDelivery: 4` on its own (the heartbeat and Fusion-bound
  delivery stay 3), and advertises nothing while the consumer is off. A file WG can
  identify as a request but cannot accept is claimed, refused with its reason and
  deleted; one it cannot identify is left alone (a field it cannot even compare, such as
  a list, included); a claim whose bytes were read and are not a request ends. A file WG
  could not *read* (another process holds it, a drive went away) is never refused for
  it: the read is retried briefly, the claim is kept and read again each pass, and after
  30 passes it is reported once, still kept. A refused claim WG cannot delete is
  reported once and its delete retried quietly. A schema-3 file naming a kind other than
  `prepare_and_solve` is refused, not read as a Solve. A
  Send taken by file is settled as the live route settles one, and the page displays it
  from its `cadOperation` event. Every row the inbox creates, recovers or refuses is
  published on `/ws/jobs`; a refusal with no row of its own is published as
  `cadInboxRefusal` and kept (the last 20) for `GET /api/cadlink/delivery`, which also
  says whether the consumer runs, why its last pass started nothing, and when a pass last
  completed. The loop pushes that state (`cadDeliveryStatus`) when its declined reason
  changes or a pass runs past 15 s, so the panel needs no clock to hear of either. A
  page's first connection also recovers Sends accepted or refused in the previous ten
  minutes (or since the tab's last successful recovery, across a reload), and never shows
  one twice. Every connection also recovers the terminal outcome of exactly the
  operations the page was waiting for, so a solve that finished while disconnected still
  takes its result slot and a refused one still says so; unrelated history is not
  applied. The recovery boundary moves only after a recovery succeeds; a failure is shown
  and retried three times. Displays of recovered Sends are ordered by when each was sent
  and never replace a newer one or a return the user picked meanwhile.
- **Status the page holds.** Each running Fusion status carries when it was observed
  (`updatedAt`), how long it counts as current (`statusTtlSeconds`), how the add-in
  publishes (`observationPolicy`) and whether it sends through the request inbox
  (`addinInboxTransfer`). With the gate off, the page ages a held status past its window
  into "Fusion last reported <time>, not observed since" by itself, with no request. With
  the gate off the returns listing still runs as before for an add-in that does not
  declare the inbox transfer (the shipped pin publishes a plain Send only as a return in
  the folder), and the CAD Link panel says so. While
  the consumer is off the live delivery route answers a retryable 409
  `delivery_consumer_disabled` instead of accepting what nothing would run.
- **Coordination gate.** `WG2_CAD_COORDINATION=off` (read once at start-up, reported on
  the "application initialized" log line, the CAD Link panel and
  `GET /api/cadlink/coordination`) stops the frontend's clock-driven CAD returns listing
  and Fusion-status read unless CAD work is in flight: an unfinished operation, or a
  Send or pull the user started. They still run on explicit events (start-up, window
  focus, entering CAD mode, choosing a folder), a Send reads Fusion's status when it is
  pressed, and operation changes arrive as `cadOperation` messages on `/ws/jobs`. The
  default, `on`, is the behaviour before the gate existed. The gate never stops the
  delivery loop above: that is the transfer path, not coordination. An operation parked
  on the user (`needs_user_input`, `recovery_required`) is not work in flight: neither
  read can move it, so it keeps no clock running. A design edit still reads Fusion's
  status once, as the event it is (it compares the edited design with the linked one;
  WG reads its own copy of the heartbeat and WGLink does no work for it); no clock
  follows it.
- **Send never becomes an unbound create.** When the Fusion status gives no action --
  the add-in offline or outdated, an update under recovery, or Fusion already holding
  the design -- Send is refused with that reason rather than written as a create with no
  expected document, which would put a second Fusion document over a linked one.
- **Update restart.** Once an update restart is approved, WG starts no new CAD
  preparation until it happens (docs/reference/UPDATE-TRANSACTION-CONTRACT.md §4.2). The
  latch is the one the solve, retry, install and ingest routes read.
  - `POST .../prepare` answers 409 `update_restart_pending`, with the envelope those
    routes use, and starts nothing. The operation stays as it is.
  - The delivery loop does nothing while it is set: it starts no preparation, and
    collects no delivered file, so a received operation stays `received` and a
    delivered file stays on disk as it was.
  - A preparation asked for before the approval but not yet started starts nothing
    either: it reads the latch again before it claims the operation. An operation still
    `received` stays so, for the delivery loop's next pass. Any other, such as a Solve
    now on a solve that waits for the user, waits as `update_restart_pending`, so it is
    queued again like the others instead of being dropped. The request itself is not
    kept: the queued attempt prepares and submits from the project's setup, as the
    delivery loop does, and approvals sent with it are asked for again.
  - A preparation already running when the restart is approved stops at submission and
    waits as `update_restart_pending`, if it gets there before WG stops. One the shutdown
    ends first is taken over at the next start and waits as `interrupted`, as any
    interrupted attempt does.
  - A solve waiting as `update_restart_pending` is queued again (`received`, at its own
    generation) by the next start's recovery, or by this process's delivery loop once the
    latch comes down without a restart (released, or expired), whether or not a WGLink
    folder is selected. The loop prepares it, as any received operation, while a folder is
    selected. It resumes the preparation it had made, with the approvals given on it. A
    request an earlier attempt already bound is submitted exactly. Otherwise nothing was
    bound, so the setup is the project's current one: a setup changed meanwhile is picked
    up, as the binding-point rule says.
- **Events.** Every committed change is published on the jobs channel as
  `{"v": 1, "kind": "cadOperation", "operation": {...}}`, after it is stored. It carries
  no cursor: a client that misses one reads `GET /api/cadlink/operations` (the unfinished
  operations) or `GET /api/cadlink/operations/{id}` (one operation, with its preparation
  and approvals), which are authoritative.

## Preparation identity

A preparation is identified by its ingestion record, whose mesh is keyed by every input
that decides it. The key also names WG's own meshing semantics -- the sizing constants in
`server/mesh/imported.py` -- whenever they differ from those every earlier key was made
under. So no existing mesh is re-made, and a later change of those constants, with every
keyed input unchanged, is a new preparation. The fingerprint rounds those constants to 12
decimal places, so a last-bit difference between platforms' maths libraries is not a new
identity. A retained run keeps the mesh its own record names; nothing deletes it.

## Retention

Cleanup never removes what a pending operation references. Retained snapshots and meshes
under `<data>/imports` are never pruned, and the captured CAD documents a pending
operation's preparation was made from are kept alongside those unfinished runs still
need.

## Existing installations

- **The import.** Before schema 12, terminal solve-command outcomes lived in
  `<data dir>/ipc/wglink/solve-commands.json`. Opening the store imports that file into
  `cad_operations` in the same transaction as the schema upgrade:
  - `accepted` stays `accepted`;
  - `refused` becomes `rejected`;
  - the message moves to `outcome_json.message`;
  - the row is marked `legacy = 1`.
- **Nothing is reconstructed.** A legacy row keeps what history recorded and nothing
  more. Its digest, digest version, target and inputs stay NULL, and nothing is rebuilt
  from today's data.
- **Retiring the file.** It is renamed to `solve-commands.json.migrated` only after the
  transaction commits. If an earlier import already left that name, the new copy gets a
  timestamp suffix.
  - An interrupted import rolls back entirely and reruns on the next open.
  - If the rename fails, the rows stay committed. The next open imports nothing new,
    because a row already in the store always wins, and retries the rename.
  - Only the store is ever read, so there is never a second active ledger.
- **Outcomes without a request.** An outcome reported for a solve command whose request
  file WG no longer holds is kept the same way (`legacy = 1`), because its request
  identity is unknown. If WG already holds an unfinished operation under that id, the
  outcome is recorded on it without a digest comparison, because there is no delivered
  request to compare.
- **Redelivering a legacy ID.** A delivery with the same kind recovers the legacy row by
  its ID alone, whatever its digest, and its recorded outcome is replayed. This applies
  to outcome-only rows too. The row is never executed again and never rewritten. A
  delivery of a different kind under that ID is a conflict.
- **The later stages are additive too.** They add two tables (`cad_setup_revisions`,
  `cad_preparations`) and nullable columns to `cad_operations` (`stage`,
  `setup_revision_id`, `request_json`, `snapshot_json`, `preparation_id`,
  `approvals_json`, `snapshot_unreadable_since`, `claim_json`), in the same upgrade
  transaction. A row written before them reads its
  stage from its state. An interrupted upgrade rolls back as a whole and completes at the
  next start, and finished work is never handed out again.
- **A rollback keeps CAD Link working.** Every release refuses a `user_version` above
  the highest it knows (v0.3.2 opens 0–11), and a rollback leaves `cadlink.db` in
  place. Schema 12 only adds a table, so the store writes `user_version` 11
  (`STORE_FORMAT_VERSION`) and recognises schema 12 by the table. An older release
  opens the file and never touches `cad_operations`; the next update finds it intact.
  - The older release does not see outcomes recorded there, nor its JSON ledger,
    which the import renamed. When the newer build opens the store again, it imports
    any ledger the older release wrote meanwhile, and a row it already holds wins.
  - A solve command still unfinished at the rollback gets no answer from the older
    release: the newer build already moved it out of the legacy slot, the only place
    that release reads. The next update hands it out again, and refuses it then if
    its return changed.
  - A file written as 12 by an earlier build of this store is accepted and written
    back as 11.
  - A later change an older reader would misread raises `STORE_FORMAT_VERSION` above
    12 (`HIGHEST_READABLE_FORMAT`), and needs a restorable snapshot
    (`docs/reference/UPDATE-TRANSACTION-CONTRACT.md` §6).

## Solve-command delivery

A WG-bound Fusion request reaches WG as its own file:
`<data dir>/ipc/wglink/.wg-solve-requests/<commandId>.json`. Schema 4 carries a `kind`:
`prepare_and_solve` is Solve and `receive_snapshot` is Send. Schema 3 remains a Solve
for compatibility with the shipped WGLink pin and other schema-3 fallbacks. Schemas 1
and 2 are refused as outdated.

- **Fields.** `target: "waveguide-generator"`, `schemaVersion`, `kind` for schema 4,
  `commandId`, `operationId` (equal to `commandId`), `bundlePath`, `manifestSha256` and
  `requestedAt`. A Solve also has `returnId`, which is present even when empty; Send
  omits it. Schema 3 is interpreted as `prepare_and_solve`.
- **Digest.** The `prepare_and_solve` digest above, over `return_id`, `bundle_path` and
  `manifest_sha256`. `requestedAt` and the file's name and folder are transport.
- **Writing a file.** A producer stages it under a name that starts with `.` or does not
  end in `.json`, then renames it into place. WG reads only `*.json` names that do not
  start with `.`.
- **The file name is the producer's convention.** WG identifies a command by the
  `commandId` inside the file, not by the file's name.
- **Files WG cannot identify as requests.** WG leaves a staging name, non-JSON file,
  another target, or otherwise unidentifiable content alone. A file it can identify as a
  request but cannot validate is claimed, refused visibly, and deleted.
- **What the shipped pin writes.** The pinned WGLink `04b2524b` reports delivery version
  3 and writes schema-3 Solves, which WG accepts as `prepare_and_solve`. Its plain Send
  is not a schema-3 inbox request: WG finds that return by listing the returns folder.
  A WGLink older than delivery version 3 writes the single slot
  `.wg-solve-request.json`, or a version-2 file in the folder above; WG claims such a
  command and refuses it with the remedy as its reason. It is never run. A command under
  an ID the store already holds is instead a repeat delivery, and is recovered or
  refused as the delivery table says.

If a Send or Solve write's outcome is unknown, the add-in retries the same ID and fields
a bounded number of times. Only an exact reread of the complete request is reported as
sent; if it still cannot be confirmed, the user sees **Unconfirmed**, with the short ID
and a prompt to check WG's CAD Link panel before sending again. It never reports "not
asked" or silently invents a new request ID.

**Consuming a delivery.** WG takes each file in five steps:

1. **Claim** it: rename it to a unique `.wg-solve-claim-<random>.json` in the same
   folder. A producer that writes the same path afterwards writes a new file, which the
   next poll takes. If the rename fails, WG tries again on the next poll. That happens
   when the file is already gone, or on Windows while its writer still holds it open.
2. **Read** the claim. What the rename took is the request.
3. **Persist** it: accept the operation, or recover the one it repeats, as in the
   delivery table above. A delivery over HTTP is accepted by the same code, with the
   same digest (see "Delivery over HTTP").
4. **Retain** the snapshot the operation names in WG's own storage (see "Preparation").
   - Retained, or never retainable as it is named (malformed, changed since the command,
     outside the WGLink folder): go on. Preparation refuses a return that cannot be
     retained.
   - Not readable now (not in the WGLink folder yet, a file another process holds, a drive
     that is not mounted): the claim stays, and the next pass tries again. After 30
     passes (about half a minute while passes succeed; the loop backs off while they
     fail), WG goes on anyway; the operation then waits for its return
     (`preparation_failed`), as it would have without the claim. A claim that waits never
     holds up the files behind it, and its operation is not started while it waits, even
     by a pass that stops before it reaches the claim.
5. **Delete** the claim. WG deletes only the file it consumed, and only after the store
   holds the operation. If the delete fails, the next poll recovers the same operation
   from the claim that is left.

**Crash and power-cut positions.** If WG stops between any two of the steps above, or
before or during preparation, the next start still gives each request exactly one
operation and at most one job. `server/tests/test_cad_inbox_restart.py` checks this for
both kinds, for schema 4 and schema 3 files, and for a real process killed mid-pass. What
the next start finds:

| WG stopped | What is on disk | The next start |
|---|---|---|
| after the claim, before acceptance | a claim, no operation | reads the claim and accepts it |
| after acceptance, before retention | a claim, operation `received` | recovers the operation from the claim, retains, deletes the claim |
| after retention, before the delete | a claim, snapshot retained | recovers, deletes the claim |
| after the delete, before preparation | no file, operation `received` | the delivery pass prepares it |
| while preparing | no file, operation `processing` | `needs_user_input` (`interrupted`); Solve now submits one job |
| after the job was created | job under `cad-solve:<id>`, not yet recorded | start-up recovery records `accepted` with that job |

A received snapshot ends `accepted`, with no job, from every position.

A power cut is different, because two writes are not durable at once:

- `cadlink.db` and `jobs.db` run in WAL mode with `synchronous=NORMAL`
  (`server/platform/sqlite.py`). A commit survives WG being killed, but a power cut or an
  operating-system crash can roll back the most recent commits.
- The claim rename and the claim delete are not followed by a flush of the folder. A
  power cut can undo either of them, whatever happened to the database.

What the user sees depends on which of these writes survived:

- **The delete survived and the acceptance did not.** The request is lost. The file is
  gone, so the add-in's pickup check reports nothing, and WG has no operation to show.
  The user sees neither the model nor a solve, and presses Send or Solve again in Fusion.
  That is a new command with a new ID. If only later commits were lost, such as a
  preparation stage, start-up recovery finishes the operation from the state that
  survived, as in the table.
- **The acceptance survived and the delete did not.** The request file, or its claim,
  is back. Delivering it again is safe (C5). It has the same ID and digest, so it
  recovers the stored operation, with no second operation and no second job.
- **Both were undone.** The file is back and the operation is gone, so WG accepts it
  again as new. If `jobs.db` kept a job under `cad-solve:<id>`, preparation finds it and
  records it instead of submitting again.
- **Not covered.** The two databases are separate files. A power cut could keep the
  operation's `accepted` outcome and lose the job it names. The operation would then name
  a job that the jobs list does not have. No test covers this case. Nothing submits the
  solve again, so the user solves again from Fusion.

WG accepts this position rather than using `synchronous=FULL` for acceptance. No
position gives a second operation or a second job. The worst a power cut does is lose a
request that the user sends again.

A claim left by an interrupted poll is finished by a later one. Within one poll,
deliveries are taken oldest first: by `requestedAt` compared as text, then by the file's
modification time, then by its name. `requestedAt` is optional, and a file without one
is taken first.

**What a delivery pass does.** Nothing polls for commands any more. The backend's delivery
loop (see "Preparation") moves delivered files into the store, oldest first, and prepares
the operations from there. A pass stops at a delivery that is owed an answer of its own;
later files wait for the next pass.

- **A delivery owed its own answer** is a command whose outcome already stands (the
  answer replays it), an older add-in's command, or a different request under an ID whose
  operation is finished or is not a solve (the answer refuses it). The file is removed
  and the refusal is logged. No client receives the answer.
- **A different request under the ID of an unfinished solve** is refused and removed as
  well, and the refusal is logged. It does not end the operation holding the ID.
- **Nothing is handed out.** The loop starts each operation no attempt has touched, oldest
  first. A command that waits on the user stays waiting, and a later request never takes
  its place. This is not "latest wins".
- **A command whose job already exists is reconciled, not prepared again.** A solve
  command's job is submitted under the submission key `cad-solve:<commandId>`: by the
  backend's preparation, and before the backend owned solves, by the browser. If the jobs
  store holds a job under that key, it is that command's outcome and its report never
  arrived: a lost acknowledgement, a restart, an upgrade from a build whose browser
  submitted it. Preparation records `accepted` with that job before it does anything else
  (see "Preparation", "Recovery").
- **The exchange folder decides nothing once a return is retained.** Preparation reads
  WG's copy, so a return that has since left the WGLink folder, or changed there, still
  prepares from what was accepted. A return that was never retained is checked when
  preparation retains it; one that no longer matches its command is `rejected`
  (`snapshot_invalid`).

**Delivery over HTTP.** A WGLink with a live session delivers the same item by
`POST /api/cadlink/live/deliveries` (docs/reference/CADLINK-LIVE-PROTOCOL.md, section 8)
instead of writing the file. It names the bundle by reference in the WGLink folder, as the
file does, and is `prepare_and_solve` or `receive_snapshot`.

- **One acceptance.** The file pass and the route accept through one function: the same
  digest (`requestedAt` is transport on both), the same conflict rule, taken from the
  store's result. One operation delivered by file and over HTTP, in either order or at
  once, is accepted once and prepared once; one consumer in the process accepts at a time.
- **Retained before the answer.** The route answers 200 (`created` or `recovered`) once
  the snapshot is retained, or once it can never be retained as named (preparation then
  refuses it). A return that cannot be read now is answered 503 and retried by the
  add-in. The first such answer sets a deadline 30 s later; a retry at or after it is
  acknowledged (200), and a solve then waits for its return (`preparation_failed`), as a
  file claim does after its passes. The deadline is remembered for five minutes past
  it; a retry later than that starts a new 30 s bound.
- **Held while in flight.** Before the route accepts, it records a hold on the operation,
  apart from the file claims' waits. The hold ends with a 200. A 503 keeps it until its
  deadline, after which the operation is no longer held (the delivery pass may start it)
  even before the retry arrives. A conflict (409), a busy store or an error leaves the
  hold as it was before that delivery, so a conflicting delivery never releases another
  one's 503 hold. A delivery pass lists `received` operations first and reads the holds
  after, so it never starts an operation whose delivery is still being accepted or
  retained. The holds are in memory: a restart forgets them, and the add-in's retry is
  recovered by digest.
- **A received snapshot** is retained, then claimed and recorded `accepted`; one that can
  never be retained as named is `rejected` (`snapshot_invalid`); one that cannot be read
  now is not claimed and gets no outcome. Nothing is ingested, meshed or solved. It is
  never `accepted` before WG holds its bytes.
- **Unsettled snapshots.** A received snapshot left `received` (WG stopped between
  acceptance and outcome, or the return was not readable within the bound) or
  `processing` (a settler claimed it, and WG stopped or the store refused its outcome) is
  settled the same way at startup and at the start of each delivery pass with a WGLink
  folder, and by the add-in's retry. Every such operation is reached, however many stay
  unreadable, and one a delivery holds is skipped. Settling claims the operation again,
  so the new generation takes it over and a late outcome from the earlier claim is
  refused. The route never answers 200 for a snapshot still `received` or `processing`
  except at the 30 s bound; the settlement finishes it after.
- **Bounded wait for a snapshot.** The first time a received snapshot's bundle is found
  unreadable is stored with the operation (`snapshot_unreadable_since`), so a restart
  does not reset it. Once it has stayed unreadable for 24 hours the operation is
  `rejected` (`snapshot_unavailable`) and logged. Sending it again from Fusion is a new
  operation with a new ID.

## Solve-command compatibility

`GET /api/cadlink/solve-command` and `POST /api/cadlink/solve-command/outcome` are kept, and
neither decides anything. The backend's delivery loop is the one consumer of solve
commands; a second, independently active consumer is what these routes must never be
again.

- **`GET /api/cadlink/solve-command`** always answers `{"command": null}`. It claims no
  delivery, records no outcome and hands out no command.
- **`POST /api/cadlink/solve-command/outcome`** takes the same body as before and records
  nothing. It answers `{"commandId", "recorded": false, "cleared": true}`: `cleared` tells
  a client to drop its copy, because the backend owns the command. A job a client
  submitted under `cad-solve:<commandId>` is the operation's outcome, and the backend
  finds it through that key (see "Preparation", "Recovery").

**Why they answer instead of returning 404.** The pages of v0.3.2 and v0.3.3-rc.1 poll the
first route and post to the second, and such a page can still be open in a `--browser`
tab across an update restart.

- It reads a failed poll exactly as "nothing pending", so for the first route either
  answer would do.
- A failed report is different. The page's Dismiss leaves its card up. Its Solve reports
  that the acknowledgement failed after the job was created. A newer return from Fusion
  stops its arrival handling part way. A 2xx answer lets it drop its copy instead.

With nothing handed out, such a page parks no new command and starts no solve of its own.
A command it parked before the restart stays on that page until the user solves or
dismisses it there; a job it submits for it is reconciled through the submission key.

## Delivery version

WG and WGLink speak delivery version **3** for the heartbeat and Fusion-bound requests,
and nothing older. WG separately advertises `solveCommandDelivery: 4` while its request
consumer runs; it omits that capability while the consumer is disabled. Release owner
decision, 2026-09-13: no older add-in is supported, and WG always uses the add-in it
ships.

- **WG refuses an older add-in.** The add-in reports its version in the heartbeat as
  `deliveryVersion`. A live heartbeat without it, or below 3, gives the status
  `addin_outdated`, and WG reads nothing more from it. WG publishes no return request to
  such an add-in (HTTP 409 with the remedy), and refuses its solve commands (see
  "Solve-command delivery"). The UI names the remedy, from what WG's startup did about
  the add-in (`addinRefresh`).
- **WGLink refuses an older WG.** It needs WG to advertise version 3 in its capability
  file. Without that it writes no solve command, never runs WG's requests, and says once
  per session that WG needs updating.
- **WG always installs and updates its own add-in.** At every start WG brings Fusion's
  WGLink to the commit it pins, from the package the release ships
  (`server/cadlink/addin_update.py`). It installs it where Fusion is installed and has no
  WGLink, and replaces a WGLink that no Waveguide Generator manages. It leaves alone a
  developer sync, and an add-in another Waveguide Generator installation manages; WG
  still refuses either while it is too old, and says why. The add-in Fusion is running
  changes only when Fusion restarts.
- **WGLink activation.** Changing Fusion's add-in follows the order an app update needs
  (`server/cadlink/addin_update.py`):
  - **After this start is confirmed.** Nothing changes while this installation has an open
    update journal. A healthy start closes it through `commit_transaction`, which the
    healthy-start settlement calls in every launch mode: the window, `--browser` and
    `--no-gui` (`launchers/statusapp/healthy_start.py`). The only other way a journal goes
    is recovery removing one it cannot trust once it has rolled back or aborted
    (`launchers/apply_update.py`); the build that then starts is the one recovery kept. So
    a build that rolls back never leaves its add-in behind with the older WG. A source
    checkout has no journal.
  - **Never while Fusion is open, or while WG cannot tell.** A fresh WGLink heartbeat, a
    running Fusion process, and a process check that fails or times out
    (`fusion_process_state`) all count as open. The change is then recorded as pending in
    `<data>/integrations/wglink/activation/<installation key>/pending.json`, outside
    `<data>/updates/`, and nothing is created in Fusion's add-ins folder. The record names
    the build (`version`, `commit`, `runtimeId` from `APP-MANIFEST.json`), the required
    pin, the target and its ownership, and the verified package by digest. The status
    reads "WGLink activation is pending until Fusion closes".
  - **Decided under `.WGLink-install.lock`.** The pass that replaces the add-in reads the
    start, Fusion, the pin, the build, the pending work and the owner under the lock the
    replacement holds. It reads the pin and build again just before the installer runs,
    and the installer asks about Fusion, the pin and the build once more immediately before
    it moves anything; a "no" leaves the target untouched and the work pending, or
    superseded. Pending work from another build, pin, installation or add-ins folder is
    discarded and logged, never installed; so is work whose target another installation or
    a developer sync has taken since.
  - **Who finishes it.** While WG runs, the Fusion status poll retries a pending activation
    once Fusion has closed, and the startup pass retries it about once a minute, so it does
    not depend on the CAD Link UI being open. Each start re-checks it once confirmed. When
    WG is not running, nothing is promised.
  - **Rollback material.** The managed add-in a replacement displaces is kept, marker and
    all, in `activation/<installation key>/previous/`. A hand-copied add-in is not kept.
  - **Fusion's registry** (`JSLoadedScriptsinfo`) is read, never written. A duplicate
    registration, one from another folder, or one not set to run on startup is reported
    with the status (`addinRefresh.registration`).
  - "Activated" means Fusion's next start loads the new copy, never that it runs it.
    What Fusion actually loaded is the identity WGLink reports when it registers a live
    session (`docs/reference/CADLINK-LIVE-PROTOCOL.md`), with `matchesPin` saying whether
    its commit is the pinned one; a mismatch is reported, not refused. The status poll
    reports it at response time as `addinRefresh.loadedIdentity`
    (`addin_update.loaded_addin_identity`), and `null` while no live session is valid. It
    appears only where the status already carries `addinRefresh`: an outdated add-in, or an
    activation decision other than "disabled".
- **What an older WG left is removed.** At every start WG removes the single slots
  `.fusion-return-request.json` and `.fusion-handoff.json`, their records
  (`.legacy-slot.json`), and request files of another schema. No add-in this WG talks to
  runs them.
- **A rollback** to a release before version 3 (v0.3.2, v0.3.3-rc.1) leaves the newer
  add-in in place: those releases do not manage WGLink. The add-in refuses that WG's
  requests and says so, and a solve command it writes waits untaken, with a notice (see
  "Capability file"). Reinstalling the release's WGLink, or updating WG, restores a
  matching pair. The older WG removes nothing of version 3; the next update of WG takes
  what is still waiting.

## Capability file

WG tells the add-in which delivery version it reads, which WG-bound request schema it
reads, and which optional return features
it accepts, in `<data dir>/ipc/wglink/wg-capabilities.json`. WG writes it atomically at
every start:

```json
{"schemaVersion": 1, "producer": "waveguide-generator", "solveCommandDelivery": 4, "fusionRequestDelivery": 3, "sourceIdentity": 1, "liveProtocol": 1, "documentUp": 1}
```

- **`sourceIdentity: 1`** means WG reads returns that require `source-identity-v1`
  (`docs/reference/MULTI-INSTANCE-CAD-IDENTITY.md`, "Cross-export source identity"). An
  add-in must declare that feature only when WG advertises it, because a WG without it
  refuses the bundle as an unknown required feature. It is advertised only when the file
  is readable, `schemaVersion` is 1, and `sourceIdentity` is an integer of at least 1 (not
  a boolean) -- the same reading rules as the delivery versions. Anything else means "do
  not declare it". Adding the field changed neither `schemaVersion` nor either delivery
  version.
- **`documentUp: 1`** means WG reads returns that require `document-up-v1`: the CAD
  document's up axis in `coordinate_system.document_up` (`"+y"` or `"+z"`), which sets
  the roll of an unlinked model's solver frame (see "Unlinked solver frame"). The
  feature and the field are required together. Read by the same rules as
  `sourceIdentity`; an add-in states it only when WG advertises it.
- **`liveProtocol: 1`** means this WG serves live protocol 1 at the address in
  `wg-endpoint.json` (`docs/reference/CADLINK-LIVE-PROTOCOL.md`). An add-in goes live only
  when WG advertises it, read by the same rules; anything else means "use the files". It
  is additive: the files above keep working, with or without a live session.

- **Reading it.** A reader ignores fields it does not know. A missing or unreadable file,
  a `schemaVersion` the reader does not know, or a value that is not an integer read as
  "WG does not speak version 3", and the add-in refuses as above.
- **A stale file.** WG never deletes it, and a release before version 3 neither writes
  nor removes it. After a downgrade it can therefore still say 3 while the older WG
  runs. The add-in does not rely on it alone:
  - it reports a single-slot marker, or a request file of another schema, as an older
    WG's whatever the file says. A version-3 WG removes those at its start, before it
    advertises, so one found beside a "3" was written since;
  - it tells the user when WG has not taken a solve command within about a minute. The
    command is not moved or dropped; it runs when a WG that reads it takes it.

## WG-produced Fusion requests

WG asks Fusion for two kinds of work:

- a **return request**: export the active document back to WG (`request_return`);
- a **handoff**: open or update a completed export (`insert_link` or `update_link`).

Each request has a request ID that WG generates, and that ID is its operation ID. Each is
one file under `<data dir>/ipc/wglink/`:

| Request | File |
| --- | --- |
| Return request | `.fusion-return-requests/<requestId>.json` |
| Handoff | `.fusion-handoffs/<requestId>.json` |

WG commits the operation row before renaming the request file into place. A registry
failure publishes no file. The complete hidden staged file is fsynced first; if the
rename and its compensating cancellation both fail, startup finishes that staged
publication. Otherwise a file-write failure cancels the newly created row as
`publication_failed`, so no request is left waiting for a file that never existed.
A recovered same-ID operation is never republished: its visible file or hidden Fusion
claim, if any, already owns delivery, and a terminal operation must never run twice.

- **Fields.** `schemaVersion: 3`, `target: "fusion360"`, `requestId`, `operationId`
  (equal to `requestId`), `deliverySequence`, and the request's own fields.
  - A return request names `sessionId`, `designId`, `documentId`, `instanceId` and
    `expectedReturnStateHash`. All are required; WG refuses to publish one without a
    baseline.
  - A handoff names the bundle and its export identity. An update also names
    `expectedDocumentId`, `expectedInstanceId` and `expectedReturnStateHash`, all three
    or WG refuses it (HTTP 422, before anything is built). A handoff with no instance is
    an insert and adds its durable `destination` object. The version-3 required keys are
    unchanged; readers ignore this additive transport field.
- **`deliverySequence`** is a positive integer, one higher than any request of that kind
  still on disk. It orders the requests without a clock. It is not unique across
  requests the add-in has taken, so a reader identifies a request by its `requestId`,
  never by its sequence.
- **Staging.** Every file is written under a name that starts with `.` and ends in
  `.tmp`, then renamed into place. A reader takes only `*.json` names that do not start
  with `.`. Every write retries a `PermissionError` briefly: on Windows a reader can hold
  the file open.
- **What WG withdraws when it publishes.** Earlier return requests that name a different
  session, because no add-in will run them; and an unstarted update of the same exact
  link (see "Ordering"). Only a file still on disk under its own name is withdrawn: one
  the add-in has claimed has started.
  WG first renames a withdrawable file to a hidden holding name, records its durable
  cancellation, then deletes it. If recording fails, WG restores the visible file.
- **Insert expiry.** On startup and every `/fusion-status` poll, WG removes an insert
  still under its own name after 30 minutes and records `cancelled`/`expired`. Losing
  the removal race means Fusion claimed it, so WG leaves its operation unsettled for
  the heartbeat outcome pass.

**The add-in** handles each kind in this order:

1. **Take the files in `deliverySequence` order**, then by name. Leave a file without a
   valid sequence, or of another schema, where it is: WG did not write it for this
   version.
2. **Claim** a file by renaming it to a name that starts with `.wglink-claim-`. A failed
   claim is retried on the next pass; on Windows that happens while WG reads the file.
3. **Run it at most once** per request ID, in the order "Fusion-bound mutations" gives,
   then delete the claim, whatever the outcome.
4. **A return request runs only in the session it names** (`sessionId`).

**Or over the live protocol.** A live add-in takes the same requests through
`/api/cadlink/live/requests` (`docs/reference/CADLINK-LIVE-PROTOCOL.md`, section 7): WG
hides the visible file under `.<requestId>.json.live-<claimId>.tmp`, claims the operation
(`processing`, stage `adapter-received`, the claim recorded in `claim_json`), then deletes
the hidden file. The file is the mutual-exclusion token, so a request is taken exactly
once, by whichever transport renames it first. The add-in then reports the stages
`queued-for-fusion` and `executing` and the outcome, fenced on its installation and the
attempt generation, with the heartbeat's outcome mapping below.

**Heartbeat outcome mapping.** WG reads only a fresh heartbeat with
`deliveryVersion >= 3`, live or file: the one `fusion_status.select_heartbeat` chooses
(`docs/reference/CADLINK-LIVE-PROTOCOL.md`, section 6), and in a status poll the same one
the status reports. Document link evidence is considered before every reported
outcome; an exact `operationId` plus `exportId` settles a mutation as reconciled
`accepted`. `document.applyingOperation.operationId` settles only Insert/Update as
`recovery_required`. A missing observation stays `processing`.

| Add-in field/value at pinned add-in `04b2524b4` | WG result |
| --- | --- |
| `recentOutcomes: superseded` (`_pending_handoff`) | `cancelled` / `superseded` |
| `recentOutcomes: discarded` (`_sweep_leftover_claims`) | `cancelled` / `adapter_not_started` |
| `recentOutcomes: reconciled` (`_apply_pending_handoff`, `_sweep_leftover_claims`) | reconciled `accepted`, using the operation's export identity |
| `recentOutcomes: recoveryRequired` (`_apply_pending_handoff`, `_sweep_leftover_claims`) | `recovery_required` for Insert/Update only |
| `recentOutcomes: wgOutdated` (`_notice_outdated_wg`) | logged and ignored |
| `recentOutcomes: notTaken` (`_notice_untaken_solves`, solve channel) | ignored for Fusion-bound rows |
| `lastRequest: refused` (`_apply_pending_return_request`, `_apply_pending_handoff`) | `rejected` / `adapter_refused`, unless document evidence already accepted it |
| `lastRequest: applied` (`_apply_pending_return_request`, `_apply_pending_handoff`) | accepts a return request; a mutation still requires document evidence |
| `lastRequest: failed` (`_apply_pending_return_request`, `_apply_pending_handoff`) | logged and left `processing` |
| `lastRequest: running` (`_begin_request`) | left `processing` |
| `lastRequest: requested` (`_request_wg_solve`, solve channel) | ignored for Fusion-bound rows |

`recentOutcomes` is a 16-item ring. `lastRequest` is one overwritten slot, matched by
`correlationId` (the operation ID for WG-produced requests); WG logs its correlation
and attempt IDs once per change. Missing that slot never authorizes a guessed outcome.

**What can be lost.** A request withdrawn as superseded never runs, by design. A power
loss keeps a later write but not an earlier one, because WG does not flush the folders
themselves to disk.
