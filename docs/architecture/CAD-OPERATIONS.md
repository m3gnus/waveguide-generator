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
are delivered through it (see "Solve-command delivery"). WG and its add-in speak one
delivery version, 3, and nothing older (see "Delivery version"): every request is its
own file, and there is no single-slot marker and no twin. The adapter follows the order
in "Fusion-bound mutations", marks an operation as applying before it writes, and
settles what an interrupted session left. The backend owns a solve operation end to end
(see "Setup revisions" and "Preparation"): its retained snapshot, its setup revision,
its fenced preparation stages, its approvals and its bound request. A later step adds a
field to a request only under a new digest version.

## Operation kinds

| Kind | Target: what it addresses | Inputs: immutable references | Needs a live CAD session |
| --- | --- | --- | --- |
| `receive_snapshot` | `{}` | `{bundle_path, manifest_sha256}` | No |
| `prepare_and_solve` | `{}` | `{return_id, bundle_path, manifest_sha256}` | No |
| `request_return` | `{document_id, design_id, instance_id, expected_baseline}` | `{}` | Yes |
| `insert_link` | `{document_id, export_id}` | `{}` | Yes |
| `update_link` | `{document_id, design_id, instance_id, expected_baseline}` | `{export_id}` | Yes |

- **Fields.** Every field is a required string. All must be non-empty except
  `prepare_and_solve.return_id`, which a legacy single-slot solve marker may omit (the
  empty string).
- **No other field is accepted.** A transport field such as a session token or
  `requestedAt` is refused rather than silently hashed.
- **`expected_baseline`** is `{kind: "document_signature_hash", value}`: the
  document-wide signature the adapter reported when WG prepared the request.
- **Exact targets.** A request that addresses an existing instance (`request_return`,
  `update_link`) names that instance exactly. "The one matching link in this design" is
  not a target. That resolution survives only for legacy single-slot markers, whose rows
  are marked legacy.
- **Inserts.** `insert_link` has no instance yet. Its target is the destination
  document plus the export identity. A redelivered insert is recognised by that export
  identity already stamped on a link.
- **Solves and snapshots** address nothing in CAD, so their target is empty and
  everything they need is an input. Later inputs join under a new digest version: a
  snapshot id and a setup revision for a solve, and the import intent for a received
  snapshot once it has a producer.

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
- **This cut's solve commands.** The browser runs a solve command, and the outcome route
  records its result as the operation's current attempt, without claiming first.

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
| `setup_required` | `needs_user_input` | No setup revision is bound yet; a CAD-authored model never borrows the open project's |
| `findings_need_review` | `needs_user_input` | The preparation has blocking findings not yet approved on that preparation |
| `preparation_failed` | `needs_user_input` | Retaining or meshing failed in a way another attempt can overcome: a worker crash, a timeout, a return that is not in the WGLink folder and has no retained copy |
| `engine_unavailable` | `needs_user_input` | The engine the setup names cannot take this record; the message names the capable engines |
| `submission_refused` | `needs_user_input` | The jobs system refused the request, submitting it failed without creating a job, or no solve may start now (an update restart is pending) |
| `interrupted` | `needs_user_input` | The backend stopped, or the answer was lost, while an attempt held the operation |
| `ready_to_solve` | `needs_user_input` | Prepared, and waiting for the user to start the solve |

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
- **Resolved from the snapshot.** A preparation that names no setup revision takes the
  snapshot's project as ingestion files it -- the lineage of the solver anchor instance's
  WG design or, when the anchor names no design, the one its Fusion document already has;
  never a new claim -- and that project's setup for exactly the snapshot's sources (id,
  role and required, as the manifest states them). A recorded setup this build cannot use
  is `setup_required` too.
- **The engine is the one selected in WG.** A recorded setup keeps its engine only until
  the selection says otherwise; the setup actually used is itself a setup revision, which
  the operation names.
- **None yet** means `setup_required`: a first-time model waits for the user to choose
  its settings, and never borrows another project's.
- **The polar grid** of the request is widened to what the ingestion derived for the
  snapshot, as the frontend does; the runtime refuses a narrower one.

## Preparation

A solve operation is prepared by the backend, in stages. The UI issues
`POST /api/cadlink/operations/{id}/prepare` (a setup revision; whether to submit; the
blocking findings the user reviewed, and on which preparation) and observes.

| Stage | What is done | Committed as |
| --- | --- | --- |
| `received` | Accepted. The snapshot was retained in WG's storage before the delivery was acknowledged, when the return could be read then | the operation row, `snapshot_json` |
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
- **Dismissal.** `POST .../cancel` cancels an idle operation at once, and fences a
  running attempt, which then records `cancelled` whatever it found. The one exception
  is a job the attempt had already created: the job exists, the operation is `accepted`
  with it, and the user cancels the job in the jobs list. A CAD mutation under way is
  not dismissed; it may need recovery from the document instead. An attempt that fails
  unexpectedly leaves its operation waiting (`preparation_failed`), never held.
- **Retained snapshot.** The snapshot a solve command names is retained when the command
  is received, before its delivery is acknowledged, under
  `<data>/imports/bundles/<manifest hash>.wgreturn`. The operation stores the hashes, and
  the copy's place follows from them. Preparation reads that copy, never the exchange
  folder, so a return accepted before its folder was removed still prepares after a
  restart. The copy leaves out the captured CAD document, which is not geometry. An
  operation whose return has no copy and is not in the folder waits
  (`preparation_failed`).
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
  project's setup, submitting when it is ready. It starts only an operation still
  untouched at the generation it listed, so it never takes over the user's own attempt,
  and an operation waiting for the user is not retried unasked; the UI issues `prepare`,
  `approvals` and `cancel` and observes. Without a WGLink folder nothing is collected,
  because nothing could be retained. A failing pass is logged once per distinct error and
  the loop backs off to half a minute while it persists. Operations an older build left
  untouched are prepared at the first start after the upgrade, as the user asked when
  sending them. The browser no longer consumes `GET /api/cadlink/solve-command`, which
  stays for diagnostics. `WG2_CAD_DELIVERY=0` turns the loop off (the test suite does).
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
  `approvals_json`), in the same upgrade transaction. A row written before them reads its
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

A Fusion solve command reaches WG as its own file:
`<data dir>/ipc/wglink/.wg-solve-requests/<commandId>.json`, with `schemaVersion` 3. It
is the operation `prepare_and_solve`, with the `commandId` as its operation ID.

- **Fields.** `target: "waveguide-generator"`, `commandId`, `returnId`, `bundlePath`,
  `manifestSha256` and `requestedAt`, plus `operationId`, which must equal `commandId`.
- **Digest.** The `prepare_and_solve` digest above, over `return_id`, `bundle_path` and
  `manifest_sha256`. `requestedAt` and the file's name and folder are transport.
- **Writing a file.** A producer stages it under a name that starts with `.` or does not
  end in `.json`, then renames it into place. WG reads only `*.json` names that do not
  start with `.`.
- **The file name is the producer's convention.** WG identifies a command by the
  `commandId` inside the file, not by the file's name.
- **Files WG cannot read.** WG leaves a file where it is, and acts on nothing in it,
  when it is malformed, lacks a required field, has a `schemaVersion` WG does not know,
  or has an `operationId` that differs from its `commandId`. A claim WG cannot read is
  left the same way. WG looks at such a file again on every poll, and a newer WG may
  understand it.
- **What an older add-in writes.** A WGLink older than delivery version 3 writes the
  single slot `.wg-solve-request.json`, or a version-2 file in the folder above. WG
  claims such a command and refuses it, with the remedy as its reason (restart Fusion so
  it loads the add-in WG installed). The refusal is recorded and answered like any
  other. It is never run. A command under an ID the store already holds is instead a
  repeat delivery, and is recovered or refused as the delivery table says.

**Consuming a delivery.** WG takes each file in four steps:

1. **Claim** it: rename it to a unique `.wg-solve-claim-<random>.json` in the same
   folder. A producer that writes the same path afterwards writes a new file, which the
   next poll takes. If the rename fails, WG tries again on the next poll. That happens
   when the file is already gone, or on Windows while its writer still holds it open.
2. **Read** the claim. What the rename took is the request.
3. **Persist** it: accept the operation, or recover the one it repeats, as in the
   delivery table above.
4. **Delete** the claim. WG deletes only the file it consumed, and only after the store
   holds the operation. If the delete fails, the next poll recovers the same operation
   from the claim that is left.

A claim left by an interrupted poll is finished by a later one. Within one poll,
deliveries are taken oldest first: by `requestedAt` compared as text, then by the file's
modification time, then by its name. `requestedAt` is optional, and a file without one
is taken first.

**What polling answers.** `GET /api/cadlink/solve-command` first moves delivered files
into the store, oldest first, until one is owed an answer of its own. Then:

- **A delivery owed its own answer is answered first**, one per poll; later files wait
  for the next poll. A delivery of a command whose outcome already stands replays that
  outcome, and an older add-in's command is answered with its refusal.
- **A different request under a held ID is refused.** The file is removed and the
  refusal is logged. The refusal is also the answer when the operation holding the ID
  is finished, or is not a solve. While that operation is an unfinished solve, it is
  not: a client takes an answer under a command ID as the end of that command, so the
  unfinished operation stays the one handed out.
- **Otherwise the answer is the oldest unfinished solve operation**, in the order WG
  accepted them. A command that waits on the user stays first in line, and a later
  request never takes its place. This is not "latest wins".
- **A command whose job already exists is reconciled, not handed out again.** The
  browser submits a solve command's job under the submission key
  `cad-solve:<commandId>`. If the jobs store holds a job under that key, the browser
  created it and its report never arrived: a lost acknowledgement, a reload, an upgrade.
  WG records `accepted` with that job and answers with it. Handing the command out again
  would submit it twice, or, from a client that builds the request differently, meet a
  submission-key conflict and stay parked behind it.
- **The command is rebuilt from the stored inputs.** Its `requestedAt` is when WG
  accepted it, because the producer's timestamp is transport and is not stored.
- **The checks against the workspace still apply** before a command is handed out. A
  command that fails one is `rejected`, with its reason as the operation's outcome.

## Solve-command compatibility

`GET /api/cadlink/solve-command` and `POST /api/cadlink/solve-command/outcome` keep
their response shapes. The outcome route records the first terminal outcome, and
repeating the same outcome is idempotent. Its `cleared` field is true once an outcome
stands for the command, so WG no longer holds it as unfinished.

An outcome is recorded on the operation the store holds under its command ID. A
file still waiting under that ID does not change which
request the outcome belongs to; the next poll refuses or recovers that file as a
delivery in its own right. Only a command the store has never seen takes its request
identity from a file WG still holds. With neither, the outcome is kept as a legacy row.

A conflicting report is not recorded. That covers a different outcome from the one that
stands. The route answers with the outcome that stands plus `"conflict": true`. A
client then retires its copy instead of retrying a report that can never be recorded.
The stored operation is left untouched.

Polling applies the same rule before it replays an outcome or hands a command out. A
delivery whose ID already names a different request, or a different kind of operation,
is refused and removed; "What polling answers" says when that refusal is also the
answer. The operation that holds the ID is never rewritten, and its result is never the
answer to that delivery.

## Delivery version

WG and WGLink speak one delivery version, **3**, in both directions, and nothing older.
Release owner decision, 2026-09-13: no older add-in is supported, and WG always uses the
add-in it ships.

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
    The loaded identity arrives with the Phase 4 handshake
    (`addin_update.loaded_addin_identity`).
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

WG tells the add-in which delivery version it reads in
`<data dir>/ipc/wglink/wg-capabilities.json`. WG writes it atomically at every start:

```json
{"schemaVersion": 1, "producer": "waveguide-generator", "solveCommandDelivery": 3, "fusionRequestDelivery": 3}
```

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

- **Fields.** `schemaVersion: 3`, `target: "fusion360"`, `requestId`, `operationId`
  (equal to `requestId`), `deliverySequence`, and the request's own fields.
  - A return request names `sessionId`, `designId`, `documentId`, `instanceId` and
    `expectedReturnStateHash`. All are required; WG refuses to publish one without a
    baseline.
  - A handoff names the bundle and its export identity. An update also names
    `expectedDocumentId`, `expectedInstanceId` and `expectedReturnStateHash`, all three
    or WG refuses it (HTTP 422, before anything is built). A handoff with no instance is
    an insert.
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

**The add-in** handles each kind in this order:

1. **Take the files in `deliverySequence` order**, then by name. Leave a file without a
   valid sequence, or of another schema, where it is: WG did not write it for this
   version.
2. **Claim** a file by renaming it to a name that starts with `.wglink-claim-`. A failed
   claim is retried on the next pass; on Windows that happens while WG reads the file.
3. **Run it at most once** per request ID, in the order "Fusion-bound mutations" gives,
   then delete the claim, whatever the outcome.
4. **A return request runs only in the session it names** (`sessionId`).

**What can be lost.** A request withdrawn as superseded never runs, by design. A power
loss keeps a later write but not an earlier one, because WG does not flush the folders
themselves to disk.
