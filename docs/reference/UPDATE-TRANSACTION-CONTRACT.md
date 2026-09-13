# Update transaction contract (Phase 1)

This is the implementation contract for the next round of updater work: what the
records are, what cleanup may remove, what an old release's launcher hands the new
helper, and how a restart is coordinated. It is Phase 1 ("reliability of the current
updater") of the 2026-09-12 updater review. Channels and publication stay in
[`UPDATE-CHANNELS.md`](UPDATE-CHANNELS.md), which this document does not change.

Line numbers are at `main` `8bccff0c`. Tests:
`server/tests/test_update_transaction_contract.py`. Each requirement below that the code
does not meet yet has a strict expected-failure test, marked
`xfail(strict=True, raises=AssertionError)`, so the suite stays green today and turns
red if the behaviour is fixed but the marker is left in place. Section 7 maps them.

Where a requirement depends on a decision that has not been taken, section 5 says so,
and the requirement is written so that either answer fits. Section 6 lists items for
the release owner that this contract records but does not design.

---

## 1. What the code does today

| Behaviour | Where |
| --- | --- |
| The transaction journal is `<data>/update-transaction-<installation key>.json`, schema `1`. `installed`, `rolled-back` and `aborted` are its terminal states. | `launchers/apply_update.py:334-340`, `:382-383` |
| The journal records the transaction id, operation, layers, staged paths and runtime ids. It records no version and no build identity. | `apply_update.py:1032-1052` |
| The journal validator requires only its known keys and keeps unknown ones. v0.3.2 has the same validator. | `apply_update.py:402-453`; `v0.3.2:launchers/apply_update.py:402-453` |
| **Committing deletes the journal.** `commit_transaction` calls `remove_journal` as soon as the state is terminal. Nothing else records the outcome. | `apply_update.py:2044-2049` |
| Recovery leaves a terminal journal alone, and removes an untrusted one. | `apply_update.py:1775-1776`, `:1814-1819` |
| **Only the desktop window commits.** `commit_transaction` has one caller, reached from the window's frontend wait. | `launchers/desktop.py:671`, via `:1254` and `:1573` |
| **Healthy-start cleanup deletes the whole `<data>/updates` folder**, on both paths. | Off macOS `desktop.py:705-713`; macOS `desktop.py:822`, `:825-836` |
| An uncommitted transaction keeps `.previous`, and the next update then refuses to start. | `apply_update.py:948-950` ("A previous update has not completed its healthy-start check") |
| `--browser` and `--no-gui` run through `status_main`. Linux falls back to browser mode when Qt cannot open a window. | `desktop.py:1778-1780`, `:1812-1818` |
| Browser mode settles its lamps when the backend answers, and commits nothing. `--no-gui` runs the server in-process with no controller. | `launchers/statusapp/view.py:137-139`, `:155-171`; `launchers/statusapp/__main__.py:499-518`, `:529-568` |
| Staging is keyed by version: `<data>/updates/<version>/{downloads,staged}`. | `server/updates/bundle.py:866-869` |
| The installer refuses when staging and the app are on different volumes, and v0.3.2 does the same. | `bundle.py:774-786` |
| The server writes the schema-1 handoff request at the end of staging, then reports `ready`. | `bundle.py:940-953` |
| The launcher accepts the request only with exactly its five keys and staged paths inside the data directory. It deletes the request as it consumes it. | `launchers/statusapp/updater.py:108-175` |
| The launcher runs the **staged** helper, `<staged app>/launchers/apply_update.py`, with `cwd` at the data directory. It checks containment again. | `updater.py:562-566`, `:571`, `:587-604`, `:611-616` |
| The launcher consumes the request **before** it stops the server. | `launchers/statusapp/controller.py:1013-1026`; `view.py:141`, `:191-201` |
| Nothing refuses new work during an update: the install route has no job check, and neither has `/api/solve`. | `server/updates/api.py:70-87`; `server/jobs/api.py:351`, `:419` |

After `8bccff0c`, the writing side of area 1 was implemented. `commit_transaction` writes
the completion record (§2.2) before `remove_journal`, and healthy-start cleanup on both
paths removes only the committed transaction's staging roots (§2.5), through
`reclaim_committed_staging` in `launchers/apply_update.py`. The two rows above that say
otherwise describe `8bccff0c`. Still to do:

- the update service reading the record: explaining the last outcome, applying
  suppression and the explicit retry (§2.2 "Readers", §2.3);
- carrying the channel into the record, which stays `null` until the handoff carries it
  (§2.2 `channel`).

---

## 2. Area 1: records and cleanup

### 2.1 Three records, three lifetimes

| Record | Survives until | Owner |
| --- | --- | --- |
| Recovery journal | An interrupted app replacement is resolved for certain | The existing journal, which stays the only authority for app replacement |
| Completion record: the last outcome, and failed-build suppression | Across later starts, so WG can explain an outcome and not re-offer a failed build | This contract (§2.2, §2.3) |
| Pending WGLink activation and its package | Activation succeeds, is cancelled, or is superseded | The CAD Link integration (§2.4), not the updater |

This is not a second transaction manager. The journal keeps deciding recovery. The
completion record only remembers what the journal decided.

### 2.2 The completion record

- **Location.** `<data>/update-result-<installation key>.json`, using the same
  `installation_key(resources)` as the journal. It lives outside `<data>/updates/` and
  outside the app layers, so no cleanup in this contract and no layer swap can remove it.
- **When it is written.** Before any code path deletes a journal that holds a decided
  outcome:
  - `commit_transaction` writes it before `remove_journal`. This is the healthy-start
    commit, `apply_update.py:2049`.
  - The helper that performs an automatic rollback also writes it at the moment it
    records `rolled-back`. The version it rolls back to may predate this contract, and
    that version would delete the journal without a record. For the same reason the
    helper writes it when it records `aborted`: the version it then reopens is the old
    one.
  - An untrusted journal that recovery removes (`apply_update.py:1814-1819`) is recorded
    as `unverified`, never as an invented outcome. Nothing the untrusted journal says is
    repeated as fact: the record's transaction, builds and staging roots are unknown
    (`null`, `[]`).
- **Durability.** It is written the way the journal is written: a temporary file,
  flushed, then renamed. If the record cannot be written, the journal is kept,
  `commit_transaction` reports that the rollback material may not be reclaimed, and
  `update.log` says why.
- **One record per installation.** A later write for the same transaction id replaces
  the earlier one. A write for a new transaction replaces the last outcome but carries
  `suppressedBuilds` forward unchanged; only an explicit retry (§2.3) removes an entry.
- **Fields.**
  - `schema` (`1`), `installation` and `transaction`.
  - `operation`: `update` or `rollback`; `null` when the outcome is `unverified`.
  - `outcome`: `installed`, `rolled-back`, `aborted` or `unverified`.
  - `detail` and `recordedAt`.
  - `from` and `to` build identities (§2.3), or `null` where the journal named none.
  - `channel`: the channel the update was taken from (`stable` or `beta` today). It is
    `null` until the helper is told: no release's handoff carries the channel yet, and
    the helper never guesses it.
  - `verificationBasis`: how the staged archives were verified; `release-digest` today
    (`bundle.py:888-907`), and `null` for a rollback, which stages nothing.
  - `stagingRoots` (§2.5).
  - `rollbackMaterial` (§2.6): `retained` until healthy-start cleanup has run for this
    transaction, then `reclaimed`. Cleanup removes the staging roots only while the
    record says `retained`, so it runs once, and a later staging into the same folder
    belongs to a later transaction.
  - `suppressedBuilds` (§2.3).
- **Readers.** The update service reads it to explain the last outcome in the update
  dialog, and to apply suppression. It is also part of "Copy update diagnostics".

To give the record its build identities, the helper adds `fromVersion`, `fromCommit` and
`fromRuntimeId`, and the matching `to` keys, to the journal. `begin_update_transaction`
reads them from the installed and the staged app layer's `APP-MANIFEST.json`.
`begin_rollback_transaction` reads them from the live app, which is the build being rolled
back from, and from `app.previous`. A rollback stages nothing, so its journal also carries
`supersededStagingRoots`: the staging roots of the transaction it supersedes, which are
reclaimed with it. All of these are optional keys under schema `1` (§3.3).

### 2.3 Failed-build suppression

- After an automatic rollback, the failed build's identity is added to
  `suppressedBuilds`. For an update transaction that is its `to` build; for a rollback
  transaction, its `from` build. Every automatic rollback counts, including recovery that
  restores an install cut short before its build ever ran. A journal in `rolling-back`
  does not say whether a failed start or recovery wrote it, and the explicit retry lifts
  the entry either way.
- The update service never offers or auto-installs a suppressed build. The dialog says
  why, and offers an explicit retry, which removes that one entry.
- A different build identity is never suppressed by this entry.
- **Build identity** depends on decision D2 (§5). Until D2 is taken, the identity is
  `(version, commit, runtimeId)` from the build's own `APP-MANIFEST.json`, whose fields
  are written by `scripts/build_bundle.py:422-425`. The helper can always read this,
  whichever release's launcher started it.
  - Under this interim key, a rebuild of the same commit is suppressed with its failed
    sibling. That is conservative, and the explicit retry lifts it.
  - If D2 adopts a `buildId`, the build writes it into the same manifest, and it becomes
    the key.
- Suppression takes effect only in a version that implements this contract. A rollback
  to an older version leaves the record for the next version that reads it.

### 2.4 Pending integration state

- The pending WGLink activation and its staged package belong to the CAD Link
  integration. They are delivered through the CAD Link plan's mixed-version and
  handshake phases.
- Their location is chosen there, and it must not be under `<data>/updates/`.
- The updater never reads, writes or removes them. Nothing in Phase 1 creates them.
- Whether they can exist for an add-in WG never installed depends on D5 (§5). The
  cleanup rule below holds either way.

### 2.5 Cleanup is scoped to one transaction

Healthy-start cleanup removes exactly two things:

- the committed installation's `.previous` and `.failed` layers, as today;
- the committed transaction's **staging roots**.

A staging root is the directory that holds a staged layer the journal names. With
today's layout, that is the `<data>/updates/<version>/` above `staged/`.

- The roots are taken from the journal's layer `staged` paths, read before the journal
  is removed. They are also kept in the completion record's `stagingRoots`.
- A root is removed only if it resolves strictly inside `<data>/updates/`.
- Cleanup never removes `<data>/updates/` itself, a sibling of a root, or anything a
  journal for another installation names (`journal_describes`, `apply_update.py:684`).
- A commit that finds another installation's journal reclaims nothing under
  `<data>/updates/`.
- The same rule applies on both paths: `desktop.py:705-713` and `:822`.
- When destination-side staging moves `staged/` next to the installation, the rule
  applies to every root the journal names. Downloads left in a different root are
  removed only when that root is this transaction's.

While staging is keyed by version (`bundle.py:866`), two transactions for one version
share a root. Keying staging by build identity is part of D2. Until then, one version
has one staging root, and the root belongs to the transaction that last staged into it.

### 2.6 The recovery window

Automatic rollback is possible until healthy-start cleanup runs. After that `.previous`
is gone. The completion record then says `rollbackMaterial: "reclaimed"`, and a later
Return to Stable installs a fresh target. It never assumes a rollback layer exists.

A staging root that cannot be removed, such as a folder holding a file Windows has
locked, is logged and left in place, and the record still says `reclaimed`. A later
retry could remove a later transaction's staging in the same folder, which is worse than
the leak.

---

## 3. Area 2: old-to-new handoff

### 3.1 What an old release does

An update from an old release starts with old code. For v0.3.1 and v0.3.2 the sequence
is:

1. The old server stages into `<data>/updates/<version>/staged/{app,runtime}`.
2. It writes the schema-1 request:
   `{"schemaVersion": 1, "kind": "apply_bundle", "version", "stagedAppDir", "stagedRuntimeDir"}`.
3. The old launcher consumes the request and runs the **candidate's**
   `<stagedAppDir>/launchers/apply_update.py`, on the staged runtime's interpreter, or on
   its own interpreter for an app-only update, with this command line:

   ```
   --bundle <bundle> --data-dir <data> --staged-app-dir <staged app> --parent-pid <pid>
   [--staged-runtime-dir <staged runtime>] [--relaunch-arg=<server argument> ...]
   ```

The command line is identical at both tags. `launchers/statusapp/updater.py` is
unchanged between v0.3.2 and `8bccff0c`.

### 3.2 What the candidate's helper must accept

- **That exact command line.** New behaviour arrives through new optional flags. A flag
  an old launcher does not pass is never required.
- **The version-keyed staging layout under the data directory.** Destination-side
  staging may add accepted roots. It never rejects the old layout while a supported
  release still stages there, and it never drops a path check just so a new location
  passes.
- **The oldest supported interpreter.** For an app-only update the interpreter is the
  old installation's runtime (Python 3.13 today). The launcher puts the staged app first
  on `PYTHONPATH` (`updater.py:567-570`), so the helper imports WG code only from its
  own staged layer. Third-party packages still come from that interpreter's
  site-packages, so the helper uses only what the oldest supported runtime provides.
- **A failed candidate** leaves an installation the old app can start. This is the
  existing rollback.

Every change to the handoff or journal schema names the oldest reader and writer it
supports, and the migration route for anything older.

### 3.3 Journal readers and writers

- **Writers:** the candidate's helper, including when it rolls back.
- **Readers:** the new app, which commits and recovers. After an automatic rollback, the
  **old** app's recovery and commit also read it.
- **The oldest reader is v0.3.2.** v0.3.1 has no journal and ignores the file. v0.3.2
  requires `schema == 1` and its known keys. It treats any state outside its terminal
  set as unresolved, and an unresolved journal blocks its commit.

This gives three rules:

1. New journal fields are optional keys under `schema: 1`.
2. No new state value is written to a journal an old version may read.
3. Changing `schema` is allowed only once v0.3.2 is no longer a supported rollback
   target, and the change names the new oldest reader.

### 3.4 The handoff request is same-version

The request is written and read by one version, because the server and the launcher are
replaced together. The v0.3.x reader refuses any extra key. A request left on disk
across an update is discarded by the new reader, never acted on.

---

## 4. Area 3: the restart transition

### 4.1 The approval point

Restart approval is the moment the **handoff request is written**:

- bundle installs: `bundle.py:940-953`;
- checkout installs: `server/updates/service.py:1658-1670`.

Today's one-step flow already approves the restart when the user clicks Install. The
dialog says WG "verifies it, then restarts to finish"
(`frontend/src/shell/UpdateControl.tsx:485`). A two-step flow ("Restart WG now / Later")
writes the request from the Restart action instead. Both flows share this point.

Whether the one-step flow should also refuse new work during the download itself is a
product question this contract leaves open (§6).

### 4.2 No new installation-owned work after approval

- The code that writes the request also latches "restart approved" in the server
  process.
- The latch is **not** derived from the request file being present. The launcher
  deletes the file as it consumes it (`controller.py:1013-1026`), before it stops the
  server (`view.py:191-201`), so a file check would reopen during shutdown.
- While the latch is set, `POST /api/solve` (`jobs/api.py:351`) and
  `POST /api/jobs/{id}/retry` (`:419`) answer **HTTP 409**. The error envelope carries
  code `update_restart_pending`, stage `submission`, `retryable: true`, and a message
  that names the pending restart.
- Every other route that starts installation-owned work refuses in the same way. Read
  routes stay open.
- The latch is released when the attempt fails before the handoff. A process that exits
  takes the latch with it. A failed handoff restarts the server (`view.py:196-199`),
  and the new process starts unlatched.
- The launcher can also discard a request instead of handing off
  (`updater.py:150-169`, reported at `controller.py:1022-1025`), leaving the same
  server running. That must clear the latch too: the launcher tells the server over the
  status control channel it already owns, and the server treats it as a failed attempt.
  A server must never stay latched with no handoff pending.

### 4.3 Existing work

Before files are replaced, every running job has either finished or been cancelled with
a recorded reason, such as "cancelled for the update restart". A job ended by the
restart is never left reading as running, or as failed for no stated reason.

### 4.4 Scoped shutdown

Shutdown stops only this installation's processes: the server tree the controller
started and its solver workers. The helper already waits for `--parent-pid`. Shutdown
never stops by process-name pattern, never stops another WG installation, and never
stops an unrelated Python process.

### 4.5 Every launch mode settles its transaction

**Settling** means two things, with the same log lines in every mode:

- `commit_transaction`, which writes the record (§2.2);
- the healthy-start cleanup (§2.5).

A mode that cannot confirm the build appends to `update.log` the transaction id and the
reason. It never leaves the transaction open silently. The evidence differs by mode:

- **Window and browser.** Both run the `StatusController` (`desktop.py:1827`;
  `statusapp/__main__.py:529-568`). The controller settles on the first snapshot that
  satisfies the frontend-ready predicate (`desktop.py:1129-1133`), which is the evidence
  the window uses today. The window's own call at `:671` then delegates to it. This
  covers the automatic Linux fallback to browser mode (`desktop.py:1778-1780`).
- **No-GUI.** `launch/serve.py` runs the server with no controller. It settles after
  uvicorn reports that it started, and after a self-probe of `/health` and the
  interface route succeeds. If the probe fails, or the process exits before the probe,
  it writes the report instead.
  - It does not settle inside `create_app`. The servers that the window and browser
    modes start run `create_app` too, and a commit there would pre-empt the controller's
    frontend evidence.

Why this matters: an open `installed` transaction keeps `.previous`, and the next update
is refused (`apply_update.py:948-950`). A Linux installation whose Qt cannot open a
window reaches browser mode on every relaunch. It can therefore update once and never
again. This is a static reading and has not been reproduced.

### 4.6 A healthy start

A start is healthy when all four of these hold:

- the expected build is serving;
- the interface belongs to that build;
- the data stores are readable;
- the essential local services have started.

It never depends on the internet, on Fusion, or on a solver qualification run.

---

## 5. Pending decisions this contract depends on

The updater review lists seven open decisions. None is assumed here.

| Decision | Dependency, and how either answer fits |
| --- | --- |
| D1. When Beta becomes "latest main" | None. The record stores the channel a build was installed from, whichever builds that channel carries. |
| D2. Build identity fields and the version mapping, including one version with two builds | The suppression key and the staging key (§2.3, §2.5). Until D2 is taken: `(version, commit, runtimeId)` from `APP-MANIFEST.json`, and staging stays version-keyed. If D2 adopts `buildId`: it is written into the same manifest and becomes both keys, and cleanup needs no change because it follows the journal's paths. If D2 instead forbids two builds of one version: the interim key stays. |
| D3. Channel manifest or release list, and how long a prepared update stays valid | When an update that is prepared but not yet approved may still be applied offline. The approval gate (§4) does not depend on it. A validity check, when there is one, runs before the handoff request is written. The record stores no authorization data. |
| D4. Update signatures | None structurally. The record's verification basis is "release digest" today (`bundle.py:888-907`); adopting signatures adds a basis. |
| D5. First install of WGLink | Whether pending integration state (§2.4) can exist for an add-in WG never installed. The cleanup rule does not change. |
| D6. Who authorizes a withdrawal or recovery build | None. Suppression is lifted by an explicit retry or by a different build identity, and a recovery build is a different identity under either authority model. |
| D7. Whether to prototype Velopack | Where the records live if a framework owns the install. They are in the data directory, outside the replaceable app. A framework's own cleanup must leave them, and the journal, alone. |

---

## 6. Open items for the release owner

These are recorded, not designed.

1. **`cadlink.db` stays readable by the release a rollback returns to.** Resolved
   with a compatible strategy; no snapshot is needed.
   - The CAD operation store (`fff039fc`) adds schema 12 to `cadlink.db`: one table,
     `cad_operations`, and the import of the solve-command JSON ledger.
   - Every older WG refuses a `user_version` above its own:
     `v0.3.2:server/cadlink/store.py:249-251` opens 0–11 only. `cadlink.db` lives in
     the data directory, and neither automatic rollback nor Return to Stable restores
     it. Builds from `fff039fc` wrote 12, which left CAD Link unusable in v0.3.2 after
     a rollback.
   - The store now writes `user_version` 11 (`STORE_FORMAT_VERSION` in
     `server/cadlink/store.py`) and recognises schema 12 by its table. v0.3.2 opens
     the file, never touches the table, and keeps working. The next update finds
     everything the store held. A file a build since `fff039fc` wrote as 12 is
     accepted and written back as 11.
   - Test: `server/tests/test_cadlink_store_rollback.py`. It runs v0.3.2's own store
     against a file this build upgraded, where the tag is reachable, and checks the
     written format against v0.3.2's frozen acceptance everywhere.
   - What the older release does not see: outcomes recorded in `cad_operations`, and
     its own JSON ledger, which the import renamed. A solve command redelivered to it
     under an ID the newer build already answered is treated as new. On the next
     update, whatever it wrote to a new ledger is imported again, and a row the store
     already holds wins. A solve command still unfinished at the rollback gets no
     answer from the older release, which reads only the legacy slot the newer build
     already emptied; the next update hands it out again.
   - A later change an older reader would misread must raise `STORE_FORMAT_VERSION`
     above 12 (`HIGHEST_READABLE_FORMAT`, the highest value this build opens) and
     needs a restorable snapshot. A later Return to Stable never silently
     restores an old snapshot over newer work.
2. **When the gate starts in the one-step flow:** at the Install click, or when the
   request is written (§4.1).
3. **A rollback to v0.3.1 or v0.3.2** does not apply suppression, and v0.3.1 ignores
   the journal altogether (§2.3, §3.3). Those versions stay compatible readers only in
   the sense of §3.3.
4. **Staging that no transaction names is never reclaimed.**
   - A staging that failed or was abandoned before the helper wrote a journal leaves
     `<data>/updates/<version>` behind. Examples: a download, digest or manifest check
     that failed, or a staged update that was never applied.
   - The whole-folder removal cleared it at the next healthy start after an update.
     Scoped cleanup (§2.5) does not, and the folder can hold a runtime archive over
     100 MB.
   - A guarded sweep is not designed here. It would remove version folders that no
     journal or record names and that no staging in progress is using.

---

## 7. Tests

**Strict expected failures.** Remove the marker in the change that implements the
section.

| Test | Section | Fails today because |
| --- | --- | --- |
| `test_a_browser_mode_start_settles_the_update_transaction` | §4.5 | The controller reaches a healthy start, and the journal stays `installed` |
| `test_a_no_gui_start_confirms_or_reports_the_update_transaction` | §4.5 | The `--no-gui` start neither commits nor writes anything about the transaction |
| `test_a_solve_submitted_after_restart_approval_is_refused` | §4.2 | `/api/solve` accepts a solve after the handoff request is written |

**Regression tests.**

| Test | Section | What it keeps |
| --- | --- | --- |
| `test_a_committed_update_leaves_a_completion_record_when_its_journal_goes` | §2.2 | The healthy-start commit records the outcome before it deletes the journal |
| `test_a_committed_record_names_both_builds_and_the_transactions_staging` | §2.2 | The field list, with the build identities the journal carries |
| `test_an_automatic_rollback_is_recorded_before_an_older_release_deletes_the_journal` | §2.2, §2.3 | The helper records a rollback as it decides it, and suppresses the failed build |
| `test_an_abandoned_update_is_recorded_before_an_older_release_deletes_the_journal` | §2.2 | The helper records `aborted` as it decides it, and suppresses nothing |
| `test_rolling_back_a_start_that_failed_suppresses_the_build_it_removed` | §2.3, §2.5 | The rollback helper suppresses its `from` build and carries the undone update's staging |
| `test_recovery_records_a_journal_it_cannot_read_as_unverified` | §2.2 | An untrusted journal is recorded as `unverified`, with nothing taken from it |
| `test_a_commit_that_cannot_record_its_outcome_keeps_the_journal` | §2.2 | No record, no reclaiming |
| `test_a_later_transactions_record_carries_suppressed_builds_forward` | §2.2 | A new transaction replaces the outcome, not the suppression |
| `test_healthy_start_cleanup_removes_only_the_committed_transactions_files` | §2.5 | Another transaction's download survives this host's cleanup path |
| `test_cleanup_spares_what_another_installations_journal_names` | §2.5 | A version folder shared with another copy's open transaction stays, on both paths |
| `test_cleanup_removes_nothing_under_updates_while_another_journal_is_unreadable` | §2.5 | On both paths |
| `test_a_journal_naming_another_installation_reclaims_nothing_under_updates` | §2.5 | Even with this installation's own staging still to reclaim, on both paths |
| `test_cleanup_never_removes_the_updates_folder_or_anything_outside_it` | §2.5 | The folder itself, a root outside it, a link out of it and a link to a sibling, on both paths |
| `test_cleanup_finishes_after_an_interrupted_start_and_never_runs_twice` | §2.5, §2.6 | `rollbackMaterial` drives the staging cleanup, once |
| `test_the_v032_reader_accepts_this_helpers_journal_and_its_commit_keeps_the_record` | §2.2, §3.3 | v0.3.2's own reader accepts the new journal keys, and its commit leaves the record. Skipped where the tag is unreachable, as below. |
| `test_the_candidate_helper_installs_from_a_v03x_launchers_command_line` | §3.2 | This checkout's helper installs from the v0.3.x command line and staging layout, app-only and with a runtime. The command line is frozen in the test, so this runs everywhere. |
| `test_a_released_launcher_hands_the_candidate_a_command_it_accepts` | §3.1, §3.2 | The v0.3.1 and v0.3.2 launchers themselves, extracted from their tags, consume a schema-1 request and build exactly the frozen command line, and this checkout's helper installs from it. It is skipped where the tags are unreachable: CI checks out one commit with no tags. The frozen test above still runs there. |

§4.3, §4.4 and the positive no-GUI confirmation need a live server or real processes.
Their tests belong with the implementation.

The released-launcher test is the only one that runs real released code, and today it
runs only where the tags exist. Fetching `v0.3.1` and `v0.3.2` in the server test job
would make it run on all three CI runners; that is a follow-up, not part of this
contract.
