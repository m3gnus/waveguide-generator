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
are delivered through it in both of their formats (see "Solve-command delivery").
WG advertises the delivery versions it reads (see "Capability file"), and publishes its
own Fusion requests as per-request files, each with a legacy twin (see "WG-produced
Fusion requests"). The adapter's reconciliation is a later step that builds on this
contract. A later step adds a field to a request only under a new digest version.

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

Both are rejections because a refreshed baseline or target is a new operation.

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

`fusion_mutation_precheck()` in `server/cadlink/operations.py` states this order in code.

## Ordering

- Explicit solve requests stay separate. A later request never silently erases an
  earlier one.
- CAD mutations are serialised per document, with exact instance targeting inside it.

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

A Fusion solve command reaches WG in one of two formats. Both resolve to the same
operation: kind `prepare_and_solve`, with the `commandId` as its operation ID.

| Format | File, under `<data dir>/ipc/wglink/` | `schemaVersion` |
| --- | --- | --- |
| Per-command file | `.wg-solve-requests/<commandId>.json` | 2 |
| Legacy single slot | `.wg-solve-request.json` | 1 |

- **Fields.** Both formats carry `target: "waveguide-generator"`, `commandId`,
  `returnId`, `bundlePath`, `manifestSha256` and `requestedAt`. A per-command file may
  also carry `operationId`. When present it must equal `commandId`.
- **Digest.** The `prepare_and_solve` digest above, over `return_id`, `bundle_path` and
  `manifest_sha256`. `requestedAt` and the file's name and folder are transport. One
  command delivered in both formats therefore has one digest, and is one operation.
- **Writing a per-command file.** A producer stages it under a name that starts with `.`
  or does not end in `.json`, then renames it into place. WG reads only `*.json` names
  that do not start with `.`.
- **The file name is the producer's convention.** WG identifies a command by the
  `commandId` inside the file, not by the file's name.
- **Files WG cannot read.** WG leaves a file where it is, and acts on nothing in it,
  when it is malformed, lacks a required field, has a `schemaVersion` WG does not know,
  or has an `operationId` that differs from its `commandId`. A claim WG cannot read is
  left the same way. WG looks at such a file again on every poll, and a newer WG may
  understand it.

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
  outcome.
- **A different request under a held ID is refused.** The file is removed and the
  refusal is logged. The refusal is also the answer when the operation holding the ID
  is finished, or is not a solve. While that operation is an unfinished solve, it is
  not: a client takes an answer under a command ID as the end of that command, so the
  unfinished operation stays the one handed out.
- **Otherwise the answer is the oldest unfinished solve operation**, in the order WG
  accepted them. A command that waits on the user stays first in line, and a later
  request never takes its place. This is not "latest wins".
- **The command is rebuilt from the stored inputs.** Its `requestedAt` is when WG
  accepted it, because the producer's timestamp is transport and is not stored.
- **The checks against the workspace still apply** before a command is handed out. A
  command that fails one is `rejected`, with its reason as the operation's outcome.

**Mixed versions.** WG negotiates on its side:

- **WG accepts both formats.** An add-in that writes only the legacy slot keeps working.
  An add-in writes per-command files only when WG advertises that it reads them, and
  otherwise keeps writing the legacy slot. WG advertises it in its capability file
  (see "Capability file").
- **The legacy slot keeps its write-side race.** A producer that writes it replaces any
  older command WG has not consumed yet, and that older command is lost before WG sees
  it. Claiming by rename closes the race on WG's side only: a command written after
  WG's claim always survives. The race ends when the producer writes per-command files.
- **WG never runs one command twice.** The same `commandId` in both formats, with the
  same request, is one operation. With a different request it is a conflict, refused as
  above.
- **Refusing old add-ins instead** would mean refusing legacy-slot deliveries at the
  consuming step. The rest of the contract is unchanged by that choice.

## Solve-command compatibility

`GET /api/cadlink/solve-command` and `POST /api/cadlink/solve-command/outcome` keep
their response shapes. The outcome route records the first terminal outcome, and
repeating the same outcome is idempotent. Its `cleared` field is true once an outcome
stands for the command, so WG no longer holds it as unfinished.

An outcome is recorded on the operation the store holds under its command ID. A
per-command file or legacy slot still waiting under that ID does not change which
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

**Mixed versions.** An add-in that predates this contract checks the baseline before it
looks for evidence. A lost acknowledgement from such an add-in can therefore still
surface as a false conflict, until the add-in implements the order above.

## Capability file

WG tells the add-in which delivery versions it reads in
`<data dir>/ipc/wglink/wg-capabilities.json`. WG writes it atomically at every start:

```json
{"schemaVersion": 1, "producer": "waveguide-generator", "solveCommandDelivery": 2, "fusionRequestDelivery": 2}
```

- **`solveCommandDelivery: 2`.** WG reads per-command solve files (see "Solve-command
  delivery"). An add-in then writes `.wg-solve-requests/<commandId>.json` instead of
  the legacy slot.
- **`fusionRequestDelivery: 2`.** WG publishes return requests and handoffs as
  per-request files, each with a legacy twin (next section).
- **Reading it.** A reader ignores fields it does not know. Any of these means the
  legacy route, never a refusal:
  - a missing or unreadable file;
  - a `schemaVersion` the reader does not know;
  - a value that is not an integer of at least 2.

  An add-in that predates the file ignores it.
- **A stale file.** WG never deletes it. If an older WG later runs on the same data
  folder, per-command files wait on disk until a WG that reads them starts. Nothing
  else overwrites or consumes them, so a stale advertisement can delay a command but
  never lose it. The delay has no bound when the newer add-in stays installed: after a
  downgrade to a WG that does not reinstall the add-in it ships, or with an add-in WG
  does not manage, such as a developer copy. How to bound it, for example by removing
  the file when WG stops or by a fallback in the add-in, is not settled here.

## WG-produced Fusion requests

WG asks Fusion for two kinds of work:

- a **return request**: export the active document back to WG (`request_return`);
- a **handoff**: open or update a completed export (`insert_link` or `update_link`).

Each request has a request ID that WG generates, and that ID is its operation ID.

| Request | Per-request file, under `<data dir>/ipc/wglink/` | Legacy slot | Slot record |
| --- | --- | --- | --- |
| Return request | `.fusion-return-requests/<requestId>.json` | `.fusion-return-request.json` | `.fusion-return-requests/.legacy-slot.json` |
| Handoff | `.fusion-handoffs/<requestId>.json` | `.fusion-handoff.json` | `.fusion-handoffs/.legacy-slot.json` |

- **Two copies, one request.** The per-request file has `schemaVersion: 2`. The legacy
  slot holds its **twin**: the same fields with `schemaVersion: 1`. Both carry the
  fields the slot always carried, plus `operationId` (equal to `requestId`, which a
  handoff now also carries) and `deliverySequence`. An add-in that predates per-request
  files reads only the slot, ignores the extra fields, and runs the request once.
- **`deliverySequence`** is a positive integer. WG sets it one higher than any request
  of that kind still on disk or in the record. It orders the requests on disk without
  a clock. It is not unique across failed publishes, so a reader identifies a request
  by its `requestId`, never by its sequence.
- **Order of writes.** WG writes the file, then the twin, then the slot record,
  `{"operationId": ..., "deliverySequence": ...}`, naming the twin now in the slot. A
  twin therefore never appears before its file.
  - Every write retries a `PermissionError` briefly: on Windows a reader can hold the
    file open. Any other error is not retried.
  - If the twin still cannot be written, WG reports the publish as failed and removes
    the file again, retrying a `PermissionError` briefly. A per-request reader may
    already have taken the file, or may hold it open so that it stays. Such a request
    can still run.
  - If the record cannot be written, WG logs it; the request is delivered anyway. At its
    next publish or start, WG first brings the record up to the twin in the slot.
- **Staging.** Every file is written under a name that starts with `.` and ends in
  `.tmp`, then renamed into place. A reader takes only `*.json` names that do not
  start with `.`.

**An add-in that reads per-request files** handles each kind in this order:

1. **Discard what a legacy reader took.** This applies when all three hold, checked in
   this order:
   - the record names a request and its sequence;
   - the legacy slot is absent;
   - that request's file is still present.

   Read the record first, then check the slot, then the file. WG writes the twin
   before the record, so a record read first cannot be newer than the slot seen after
   it. Checking the slot first can discard a request WG publishes in between.

   A reader that follows this contract never deletes a twin (step 3). It deletes an
   older WG's slot only while that slot still holds the request it ran (step 4). WG
   empties a slot itself only once the record names that twin. So this state means an
   add-in that reads the slot alone took the recorded request; the exceptions are under
   "What can be lost".

   Delete its file, and every request file whose `deliverySequence` is not above the
   record's, and run none of them. The earlier files were replaced in the slot before
   that reader looked. Keep files with a higher sequence: WG may be writing their
   twins now.
2. **Take each request file in `deliverySequence` order**, then by name.
   - Leave a file without a valid sequence (a positive integer) where it is: WG did not
     write it.
   - Claim a file by renaming it to a name that starts with `.`. A failed claim is
     retried on the next pass; on Windows that happens while WG reads the file.
   - Run it at most once per request ID, then delete the claim.
   - Whether an unstarted request may be superseded by a newer one for the same target
     is a separate policy, and this contract does not settle it.
3. **Never run, and never delete, a slot that names an `operationId`.** It is a twin,
   and only a legacy reader takes it. WG retires a twin once its file is gone.
4. **A slot without an `operationId`** comes from a WG that predates per-request files.
   It is the only copy: run it once. Then delete the slot only if it still holds that
   same request (the same `requestId`, or `exportId` for a handoff) and still names no
   `operationId`, as the legacy acknowledgement does. Leave the slot record alone.
5. **A return request runs only in the session it names** (`sessionId`), as before.

**What WG removes:**

- request files under step 1, before each publish and at every start;
- at every start, a twin whose file is gone, once the record names that twin;
- when it publishes a return request, earlier return requests that name a different
  session, because no add-in will run them;
- the file it has just written, when the twin cannot be written.

WG removes nothing else.

**Mixed versions.**

- **New WG, old add-in.** The add-in runs each request once, from the slot. A newer
  publish replaces a twin the add-in has not read, as before, and that request never
  runs. WG discards the files such an add-in leaves behind at its next publish or
  start, so an add-in upgraded later does not run them again.
- **New WG, new add-in.** Each request runs once, from its own file. The add-in never
  runs a twin.
- **Old WG, new add-in.** The slot has no `operationId` and is the only copy. The
  add-in runs it once.
- **What is not closed.** In each case below, an upgraded add-in can see a request
  again:
  - an old add-in running a request from the slot while WG replaces the slot (WG keeps
    that file until the old add-in takes a later request);
  - WG stopping, or failing to write the record, between the twin and the record, when
    an old add-in then takes the twin before WG repairs the record.

  What that means depends on the kind:
  - A return request does not run again, because an upgraded add-in starts a new
    session.
  - A handoff is compared first: the add-in compares the link's export identity before
    it updates, as it always has, and does not update a link that already carries that
    export. An insert handoff whose link is no longer in the active document is
    inserted again.
  - Reconciling against the document beyond that is the adapter's later step.
- **A downgraded add-in** reads only the slot. If a per-request reader already took the
  file of the twin still in the slot, the downgraded add-in may run that twin once more.
  That lasts until a WG that publishes per-request files next publishes, or starts and
  can both repair the record and remove the slot. The same limits apply.
- **What can be lost.** Beyond the losses the single slot always had, three narrow cases
  can discard a request that has not run:
  - After a downgrade, a WG that predates per-request files writes the slot while one of
    this WG's requests is still waiting, for example after a failed claim. Once a reader
    takes that older slot, step 1 treats the waiting request as taken.
  - A newer WG publishes in the moment between a reader's check and its delete in step
    4, and the delete removes the new twin. The legacy acknowledgement has always had
    the same window.
  - A power loss keeps a later write but not an earlier one. WG does not flush the
    folders themselves to disk.
