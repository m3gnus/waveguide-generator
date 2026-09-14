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
| **Every launch mode settles** (§4.5). `commit_transaction` has one caller, `HealthyStartSettlement.settle`. The window and browser modes reach it through `StatusController.settle_update_transaction`, and `--no-gui` through `_NoGuiHealthyStart`. | `launchers/statusapp/healthy_start.py`; `launchers/statusapp/controller.py`; `launch/serve.py` |
| **Healthy-start cleanup deletes the whole `<data>/updates` folder**, on both paths. | Off macOS `desktop.py:705-713`; macOS `desktop.py:822`, `:825-836` |
| An uncommitted transaction keeps `.previous`, and the next update then refuses to start. | `apply_update.py:948-950` ("A previous update has not completed its healthy-start check") |
| `--browser` and `--no-gui` run through `status_main`. Linux falls back to browser mode when Qt cannot open a window. | `desktop.py:1778-1780`, `:1812-1818` |
| Browser mode settles on the controller's first poll of its own server whose interface is served, and reports a start it cannot confirm. `--no-gui` runs the server in-process with no controller, settles after a self-probe, and reports every exit that leaves a transaction open. | `launchers/statusapp/controller.py` `poll`, `settle_update_transaction`; `launchers/statusapp/view.py`; `launch/serve.py` `_NoGuiHealthyStart` |
| Staging is keyed by version: `<data>/updates/<version>/{downloads,staged}`. | `server/updates/bundle.py:866-869` |
| The installer refuses when staging and the app are on different volumes, and v0.3.2 does the same. | `bundle.py:774-786` |
| The server writes the schema-1 handoff request at the end of staging, then reports `ready`. | `bundle.py:940-953` |
| The launcher accepts the request only with exactly its five keys and staged paths inside the data directory. It deletes the request as it consumes it. | `launchers/statusapp/updater.py:108-175` |
| The launcher runs the **staged** helper, `<staged app>/launchers/apply_update.py`, with `cwd` at the data directory. It checks containment again. | `updater.py:562-566`, `:571`, `:587-604`, `:611-616` |
| The launcher consumes the request **before** it stops the server. | `launchers/statusapp/controller.py:1013-1026`; `view.py:141`, `:191-201` |
| Writing the handoff request latches "restart approved" in the server (§4.2). While it is latched, `POST /api/solve`, `POST /api/jobs/{job_id}/retry`, `POST /api/updates/install` and `POST /api/cadlink/ingest` answer 409 `update_restart_pending`. | `server/updates/restart.py`; writers in `server/updates/bundle.py` and `server/updates/service.py`; refusals in `server/jobs/api.py`, `server/updates/api.py` and `server/app.py` |

After `8bccff0c`, the writing side of area 1 was implemented. `commit_transaction` writes
the completion record (§2.2) before `remove_journal`, and healthy-start cleanup on both
paths removes only the committed transaction's staging roots (§2.5), through
`reclaim_committed_staging` in `launchers/apply_update.py`. The two rows above that say
otherwise describe `8bccff0c`.

The restart latch (§4.2) and launch-mode settling (§4.5) were implemented after that. The
three rows that describe them name files and functions, not line numbers.

The update service then became the record's reader (§2.2 "Readers", §2.3). It explains
the last outcome, holds back a build that rolled back, and lifts one suppression on an
explicit retry: `last_outcome`, `held_back_entry` and `UpdateService.lift_suppression` in
`server/updates/service.py`, `POST /api/updates/retry` in `server/updates/api.py`,
`lift_suppressed_build` in `launchers/apply_update.py`, and the update dialog in
`frontend/src/shell/UpdateControl.tsx`.

The job runtime then took the latch too (§4.3). A job the restart ends reads as ended by
the update restart, and no queued job starts under an approved restart: `JobRuntime` in
`server/jobs/runtime.py`, and the interruption marks in `server/jobs/store.py`.

Still to do:

- carrying the channel into the record, which stays `null` until the handoff carries it
  (§2.2 `channel`). Carrying it changes both halves of the handoff:
  - a key in the request, which is same-version (§3.4), so the release that reads the key
    can add it;
  - a new optional helper flag (§3.2), with the helper recording its value in the journal.

  That widens the handoff, so it is left to a change of its own. One constraint on that
  change: the helper parses its command line strictly, so no launcher may pass the flag to
  a helper older than the flag. Today every candidate is newer than the launcher that hands
  off to it. That stops being true once anything installs an older version, such as a
  Return to Stable (§2.6).
- destination-side staging, end to end (the updater review §2.7). Staging is still
  `<data>/updates/<version>`, and the installer still refuses when it and the application
  are on different volumes (`_preflight` in `server/updates/bundle.py`), so an install
  that spans two drives cannot update in-app. The downloader, both launcher containment
  checks, the helper, the journal, recovery and cleanup change together. Acceptance is a
  two-drive install on native Windows through download, handoff, replace, restart and
  rollback.
- how long a prepared update stays valid offline, which waits on decision D3 (§5).
- a second server of the same installation, such as a `--no-gui` start with another data
  directory: it is not shut down with the first, and its healthy start can reclaim
  `.previous` while the first data directory's transaction is open. The scoped shutdown
  of §4.4 also has no test with real decoy processes yet.
- CAD Link preparation under the restart latch (§4.2): a separate change, which also
  edits §4.2.
- what the Phase 1 hardening left open:
  - staging by a release that writes no owner marker is protected by the sweep's quiet
    period alone until its helper's journal names it (§2.5);
  - the controller's `/health` build check applies to a bundle only, since a source
    checkout has no transaction (§4.6);
  - on Windows, renaming a request out of the launcher's reach can be refused while the
    launcher has the file open; the server retries, then treats the handoff as possibly
    under way (§4.2);
  - the update dialog writes the fetched logs through a `ClipboardItem` so WebKit accepts
    the write; that path has not been exercised in the desktop windows themselves.

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
  - It is read on every update status (`UpdateService.get_status`), not cached, so a
    retry takes effect at once. Only a standalone app reads one: the helper never
    installs a source checkout.
  - The status carries `lastOutcome`: the record with every field normalized, less
    `installation` and `stagingRoots`, which are an installation hash and folders on this
    machine. It also carries `suppressed` (§2.3).
  - The record's `detail` is often an exception's message, which can name a folder. In
    `lastOutcome` the home folder in it reads `~`, as it does in a problem report
    (`server/diagnostics/scrub.py`).
  - The dialog shows the outcome as a "Last update" fact, and explains one that did not
    install. "Copy update diagnostics", in the same dialog, copies the update state
    with both fields. It leaves out the install command, which names a local folder.
  - "Copy update diagnostics" also carries the updater's own logs, read when it is
    clicked from `GET /api/updates/diagnostics`: the last 64 KB of `update.log`,
    `update-handoff.log` and `rollback-handoff.log`, each from its first whole line, with
    the home folder as `~` (`update_log_tails` in `server/diagnostics/bundle.py`). The
    problem report carries the same tails.
  - A failed rollback writes no record: it leaves this installation's journal in
    `rolling-back`, which every start's recovery would otherwise have finished. The status
    reports it as `repairRequired`, the transaction and the journal's detail with the home
    folder as `~`, and the dialog says "Rollback failed, repair required"
    (`_repair_required` in `server/updates/service.py`). It reads the journal and changes
    nothing in it.

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
  - A release that matches an entry is reported with `suppressed` set to that entry,
    no `action` and `canInstall: false`, and `POST /api/updates/install` refuses it.
    Its `availability` stays `available`, because the release exists. The top bar reads
    "update held back" rather than "update available". Nothing installs an update except
    through that route.
  - The retry is `POST /api/updates/retry`, with the header `X-WG-Update: retry` and the
    entry's identity as its body. It removes the one equal entry and installs nothing.
    It answers 409 when the record holds no such entry. The record is rewritten by
    `lift_suppressed_build`, beside the writer in `launchers/apply_update.py`.
  - The retry rewrites the record while the app runs, as healthy-start cleanup does when
    it records `reclaimed` after removing the staging. Each reads the record again just
    before it writes and changes only its own field, so neither undoes the other.
  - A release's identity is its version and the `commit` and `runtimeId` of its
    published app manifest, which is the build's own `APP-MANIFEST.json`. The version
    must match. A commit or runtime id that either side does not know is not evidence of
    a different build, so suppression fails closed, and the retry lifts it.
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
- Their location is chosen there, and it must not be under `<data>/updates/`. It is
  `<data>/integrations/wglink/activation/<installation key>/`: `pending.json`, and the
  managed add-in a replacement displaced, under `previous/`
  (`server/cadlink/addin_update.py`; `docs/architecture/CAD-OPERATIONS.md`, "WGLink
  activation"). The package it names is the one inside the app layer, verified by digest.
- The updater never reads, writes or removes them. Nothing in Phase 1 creates them.
- The update status does report WGLink's partial success, from the activation's
  in-memory verdict and never from these files: `wglink`, the verdict and its detail
  with the home folder as `~` (`_wglink_activation` in `server/updates/service.py`,
  imported inside the call so `server/updates` never imports `server/cadlink` as it
  loads). While the verdict is `pending`, the update dialog says, after an installed
  update, "Waveguide Generator updated successfully. Close Fusion to finish updating
  WGLink. WG will confirm when it is ready to reopen." The CAD Link view says it too
  when no CAD folder is chosen.
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

**Staging no transaction names.** A staging that failed or was abandoned before any
helper wrote a journal is named by no journal and no record. Three things keep it from
leaking:

- **An owner marker.** The server that stages writes
  `<data>/updates/<version>/.staging-owner.json` (`schema`, `installation`, `pid`,
  `createdAt`) as it starts. It removes the marker when the attempt is called off: a
  launcher's discard or an expired approval (§4.2). The writer is
  `BundleUpdateInstaller._run` in `server/updates/bundle.py`; the reader is beside the
  cleanup in `launchers/apply_update.py`.
- **A failed staging cleans up after itself.** One that fails before its request is
  written removes the folder if that run created it, and otherwise only its own marker.
- **A sweep.** After the scoped cleanup, every healthy start sweeps the
  `<data>/updates/<version>` folders nothing owns (`sweep_unowned_staging`). A folder goes
  only when all of these hold:
  - no journal names it, and this installation's own journal is not open;
  - this installation's record does not still retain it;
  - the handoff request of this start's own controller, while present, does not name it;
  - no owner marker still speaks for it;
  - nothing in it has changed for an hour.

  A marker speaks for its folder for an hour whatever its process, and after that, up
  to a day old, only while its process runs. The launcher stops the server before the
  helper writes its journal, which is why a marker outlives its process. Another
  installation's unreadable journal stops the sweep, as it stops the scoped cleanup.
  Links are never followed, only folders directly inside `<data>/updates` are
  considered, and `<data>/updates` itself is never removed.

Another installation's marker that still speaks for a root also stops the scoped cleanup
from removing that root. That covers another copy staging the same version before its
helper has written a journal. A release that writes no marker is protected by the quiet
period alone until its helper's journal names its staging.

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

In the code, the latch is `RestartApproval` in `server/updates/restart.py`, one per server
process (`application.state.update_restart`):

- The bundle installer and the checkout install set it just before they write the
  request, and release it if the write fails for any reason.
- Four routes refuse while it is set: `POST /api/solve`, `POST /api/jobs/{job_id}/retry`,
  `POST /api/updates/install` and `POST /api/cadlink/ingest`. The ingest is refused by a
  middleware in `server/app.py` (`RESTART_GATED_POSTS`) before its handler runs. It
  schedules a deferred viewport and a capture of the CAD document, which copies tens of
  megabytes, and both outlive the request.
- **What stays open.** A route that does bounded work inside its request and persists no
  job stays open: every read, `POST /api/solve/plan`, the field plane,
  `/api/solver-mesh`, the STEP, STL and WGLink exports, and the job routes that act on a
  job that already exists (stop, delete, metadata, recombine). The graceful stop gives
  them time to finish.
- The CAD Link solve command does not go through `/api/solve`: the backend submits it to
  the job runtime directly (`submit=runtime.submit` in `server/cadlink/api.py`). The
  route's refusal does not apply to it, while the job runtime still marks no job running
  under an approved restart (§4.3). Gating CAD Link preparation itself under the latch is
  a separate change, not made here.
- A refused ingest leaves the return unread on disk and still listed. The interface shows
  the message as a failed CAD preparation and offers to prepare it again. Nothing is
  reported to Fusion. After the restart the return is ingested again when the user
  prepares it, or when a Fusion solve command asks for it again.
- The launcher's notice is `update-released.json` in the status control directory
  (`UPDATE_RELEASED_FILENAME` in `launch/serve_options.py`). It is written when the
  launcher discards a request, when a checkout handoff cannot start, and when the window
  refuses a bundle request because an earlier update's rollback material is still present.
  The server's status watcher (`_watch_statusapp` in `launch/serve.py`) removes it and
  releases the latch.
- If the notice still cannot be written after three tries, `update.log` says so, and the
  launcher does nothing more. It does not restart the server, which would end running
  solves for a restart that installs nothing.
- **The latch expires.** It comes down on its own `RESTART_APPROVAL_TTL` (300 s) after it
  was set, if this process is still running, and logs one line to `server.log`.
  - Why 300 s: after a real handoff the launcher consumes the request within about a
    second, then stops the server within its shutdown timeout (8 s). A server still
    running minutes after approval therefore has no handoff pending. Five minutes rather
    than one leaves room for a machine that is paging or suspended.
  - Expiry is checked whenever the latch is read: by a refusing route, the update status,
    and the install status. The job runtime also reads it when an approval that holds its
    queue is due to expire (§4.3).
  - The update dialog shows an expiry as a failed attempt, as it shows a discard, unless
    the launcher had already taken the request (below).
- A called-off restart is a failed attempt the update dialog shows, through the existing
  `installState` and `error` fields: "The update to `<version>` did not start: `<reason>`.
  Try again."
- **The request is taken back first.** When an approval comes down, the writer that
  approved it renames its own request, if it is still there, to `<request>.revoked`
  (`revoke_request` in `server/updates/restart.py`) before it reports anything. The
  launcher reads a request and then deletes it, and a delete that finds the file gone
  hands off nothing, in this release and in v0.3.1 and v0.3.2 alike. So a launcher that
  comes back late, for example after the machine slept past the approval, cannot restart
  WG after the dialog said the update did not start.
  - An expiry that finds the request already gone means the launcher took it: a handoff
    is under way, the dialog does not say the update did not start, and the staging keeps
    its owner marker (§2.5). A launcher's discard notice, or a request that could not be
    written, rules the handoff out whether or not the file is still there.
- **A release names its approval.** `approve` returns an id. The writers release with it,
  so a release that arrives after a newer approval changes nothing, and the expiry checks
  the same id. The launcher's notice carries no id and releases whatever is pending; only
  one approval is ever pending.
- **The checkout request is due on arrival.** It used to carry the server's wall-clock
  time plus 0.75 s, which the launcher compared with its own wall clock, so a clock step
  could hold it past the expiry. The server now writes it to a temporary name, serves the
  0.75 s on its own monotonic clock, and only then moves it where the launcher looks, with
  `readyAtEpoch` 0 (`CHECKOUT_HANDOFF_DELAY` in `server/updates/service.py`). A request whose
  approval came down in the meantime never appears.
- The interface shows the refusal's message where it shows any refused solve, retry,
  install or CAD preparation.

### 4.3 Existing work

Before files are replaced, every running job has either finished or been cancelled with
a recorded reason, such as "cancelled for the update restart". A job ended by the
restart is never left reading as running, or as failed for no stated reason.

In the code, `JobRuntime` (`server/jobs/runtime.py`) holds the server's latch (§4.2),
which `mount_jobs` passes it:

- **A shutdown while a restart is approved is the update restart.** It marks each running
  job as ended by the update restart, in the same write that requests the job's
  cancellation and before it waits. A job stopped at its checkpoint then reads "Ended by
  the update restart". One that the shutdown budget cuts off reads the same on the next
  start (`recover_on_startup`). A shutdown with no approval is Quit, as before.
- **The mark sits beside the Quit mark, and no released build reads either.** v0.3.2
  and v0.3.3-rc.1 have neither mark in `server/jobs/store.py`, and their
  `recover_on_startup` marks every job it finds running as an error, "Simulation
  failed". So after an automatic rollback to one of them, a job the update restart cut
  off reads as a failed simulation, as a crash does. Only a start of this release or a
  later one reads the mark.
- **No job is marked running once a restart is approved.** The scheduler takes no job
  from the queue while the latch is set. A job it had already taken is marked running
  through `RestartApproval.admit`, which writes the mark under the lock `approve` takes.
  The two are therefore ordered:
  - the approval came first, and the job goes back to the front of the queue;
  - or the job was marked running first, and it is running work that the restart ends
    with its own reason.
- **A queued job stays queued in the job store**, and the next start runs it: the new
  build, or the old one after a rollback. If the latch comes down without a restart,
  this process runs it. That includes the latch's expiry (§4.2). A held queue reads the
  latch again when the approval is due to expire, so it does not wait for another read.
- **The narrow race of §4.2 is closed where a job starts.** A solve that passes its
  route's check just before the latch goes up is accepted and queued. It is not marked
  running after the approval.

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

In the code, `launchers/statusapp/healthy_start.py` is the one path, and every mode writes
the same `update.log` lines through it.

A start that settles writes `Healthy start: update transaction <id> committed from state
'<state>'.`, then the cleanup's own lines, such as `Removed healthy-start rollback layer:
<path>` and `Removed the update downloads: <path>`.

A start that cannot confirm the build writes this, and settles nothing:

```
This start did not confirm the build: <reason>. Not reclaiming the previous layers yet; update transaction <id> (state '<state>') stays open.
```

The part after the semicolon appears only when a transaction is open. The line is written
only when a settle would have had something to do: an open transaction, `.previous`, or
staging that the completion record still retains. An ordinary start writes nothing.

By mode:

- **The controller** settles on its first ready poll (`settle_on_ready`), and only on a
  snapshot of its own server. An adopted server is declined with that reason: exit 2
  means another server holds the instance lock, so the new build never served. This
  applies to the window as well.
- **The desktop window** builds its controller with `settle_on_ready=False`. It delegates
  from its native event loop, as it always committed, because HTTP readiness alone is not
  enough to discard rollback material there. It passes the snapshot that ended its
  frontend wait. When its start fails, or does not answer within 120 s, it writes the
  same line before the rollback that follows.
- **The status window** polls until the interface is served, for up to 30 s after the
  backend answers. It reports once if the start fails (an error with no live server), or
  if 120 s pass without an answer it can confirm.
- **`--no-gui`** probes `/health`, which must name this build, and `/` for up to 20 s
  after uvicorn reports started. Its check exists as soon as the data directory is known,
  so every exit reports. That covers a refused or missing interface, a failed migration,
  no free port, the instance lock held elsewhere, `create_app` raising, and the server
  stopping before it answered.

**Interrupting a settle.** On macOS the cleanup moves `.previous` out of the bundle and
then re-seals the bundle, and it is not safe to interrupt between the two:

- A process that dies there leaves the bundle unsealed and the rollback material in the
  holding directory.
- The next update is then refused until that is resolved, and the refusal names the
  directory.
- A stopping `--no-gui` start waits for a settle under way. A stop that came from a
  signal or a closed console has also begun the shutdown backstop, which ends the process
  about 5 s later, so that is what such a stop gives a settle. A second Ctrl+C ends it at
  once, and so does killing the window's process.

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

The evidence each mode has for these:

- **The expected build.** Every mode, before it commits, compares the installed app
  layer's `APP-MANIFEST.json` identity `(version, commit, runtimeId)` with the build the
  decided journal left installed: the `to` build of an update that installed and of a
  rollback that restored, and the `from` build of an update that rolled back or was
  abandoned. Every field the journal names must match. A mismatch commits nothing,
  reclaims nothing, and writes the "did not confirm the build" line of §4.5. A journal
  that names no build, which an older helper wrote, is not compared. In the code:
  `installed_build_mismatch` in `launchers/statusapp/healthy_start.py`, and
  `journal_live_build` in `launchers/apply_update.py`.
- The window and browser modes take the first two from a served `/health` and interface
  route. In a bundle, the controller's own server must name the installed app layer's
  build label in `/health`. A server that names another build fails the frontend-ready
  predicate, which the window's wait and its settle both use, and the backend lamp says
  why. A `/health` that has not answered yet blocks nothing, so a probe that timed out
  while the interface is served delays no start. The server refuses at start an
  interface stamped for a different version.
- `--no-gui` also checks that `/health` names this process's build.
- The last two are implied rather than checked. uvicorn serves, and reports started, only
  after the application's startup handlers have run, and the job store opens there.
- None of this counts when the interface that answered belongs to a server this start did
  not launch (§4.5).

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
4. **Staging that no transaction names.** Resolved by the owner marker, the failed
   staging's own cleanup and the guarded sweep of §2.5 ("Staging no transaction names").
   - Before, a staging that failed or was abandoned before the helper wrote a journal
     left `<data>/updates/<version>` behind for good. Examples: a download, digest or
     manifest check that failed, or a staged update that was never applied. The folder
     can hold a runtime archive over 100 MB.
   - What remains: staging by a release that writes no marker is protected only by the
     sweep's quiet period. Keying staging by build identity (D2) would make a shared
     version folder impossible.

---

## 7. Tests

**Strict expected failures.** None at present. The three that §4.2 and §4.5 had are
regression tests now. A new requirement written ahead of its code gets one, and the change
that implements it removes the marker.

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
| `test_a_browser_mode_start_settles_the_update_transaction` | §4.5 | Browser mode's controller settles on its first ready poll |
| `test_a_no_gui_start_confirms_or_reports_the_update_transaction` | §4.5 | A `--no-gui` server that never served leaves the transaction open and names it in `update.log` |
| `test_a_no_gui_start_whose_self_probe_fails_reports_the_transaction` | §4.5 | A server that started but failed its self-probe reports once and settles nothing |
| `test_a_no_gui_start_that_serves_its_interface_settles_the_transaction` | §4.5 | Live uvicorn and a real self-probe: the commit, the record and the cleanup |
| `test_the_desktop_window_settles_from_its_event_loop_not_its_first_poll` | §4.5 | The window's controller waits for the window |
| `test_a_solve_submitted_after_restart_approval_is_refused` | §4.2 | `/api/solve` answers 409 once the handoff request is written |
| `test_a_retry_after_restart_approval_is_refused_while_reads_stay_open` | §4.2 | Retry and install refuse with the error envelope; a read route stays open |
| `test_a_restart_approval_is_released_when_the_handoff_request_cannot_be_written` | §4.2 | A bundle request that cannot be written releases the latch, and solves are accepted again |
| `test_a_request_the_launcher_discards_releases_the_servers_latch` | §4.2 | The launcher's discard reaches the server's watcher and clears the latch |
| `test_a_release_notice_that_cannot_be_written_is_logged_and_the_server_kept` | §4.2 | A notice that cannot be written is retried, then logged, and the server is not restarted |
| `test_an_approved_restart_expires_when_no_handoff_follows` | §4.2 | The latch comes down on its own after `RESTART_APPROVAL_TTL`, once, logs one line and tells its listeners |
| `test_a_called_off_restart_shows_as_a_failed_install` | §4.2 | A discard shows in the install status as a failed attempt, with the reason |
| `test_a_late_release_for_an_earlier_approval_leaves_the_new_one_latched` | §4.2 | A release carries the approval it answers; a late one for an earlier approval leaves the newer one latched |
| `test_a_cad_return_ingested_after_restart_approval_is_refused` | §4.2 | `POST /api/cadlink/ingest` refuses with the envelope |
| `test_an_adopted_server_does_not_settle_this_installations_transaction` | §4.5 | An exit-2 adoption is declined and reported, and nothing is reclaimed |
| `test_the_window_declines_an_adopted_server_too` | §4.5 | So does the window's own delegation |
| `test_a_no_gui_start_that_refuses_its_interface_reports_the_transaction` | §4.5 | An exit before the server exists still names the open transaction |
| `test_a_no_gui_start_with_no_free_port_reports_the_transaction` | §4.5 | So does a port failure |
| `test_healthy_start_writes_nothing_when_there_is_nothing_to_settle` | §4.5 | An ordinary start that cannot confirm writes nothing about updates |
| `test_a_failed_build_is_held_back_until_an_explicit_retry_lifts_only_it` | §2.2, §2.3, D6 | End to end: the rollback records the failed build, the next check holds it back and install refuses it, another commit or version stays eligible, and the retry lifts that entry only |
| `test_the_update_status_explains_the_last_outcome_without_local_paths` | §2.2 | The status carries the normalized outcome, without the record's staging folders |
| `test_the_update_status_says_a_rollback_that_did_not_finish_needs_repair` | §2.2 | A journal still `rolling-back` is `repairRequired`, its detail without the home folder, and the journal is unchanged; an installed transaction or another installation's rollback is not |
| `test_a_build_field_nobody_recorded_is_not_evidence_of_a_different_build` | §2.3 | Suppression fails closed on a commit the record does not know |
| `test_a_retry_needs_its_confirmation_and_lifts_nothing_it_does_not_name` | §2.3 | The retry needs its header, and naming a build that is not held back changes nothing |
| `test_a_job_the_update_restart_ends_reads_as_ended_by_the_update_restart` | §4.3 | A running job the restart stops at a checkpoint names the update restart, not Quit |
| `test_a_job_the_restart_cut_off_reads_as_ended_by_it_and_as_quit_to_an_older_start` | §4.3 | A job the budget cut off reads as ended by the update restart on the next start, and as Quit to a start that knows only that mark |
| `test_a_job_queued_before_restart_approval_does_not_start_after_it` | §4.3 | A queued job waits under the approval, and runs when the approval is called off |
| `test_a_solve_the_restart_approval_overtakes_is_queued_not_started` | §4.2, §4.3 | The narrow race: a solve accepted just before the approval, and one overtaken while its engine is looked up, both stay queued |
| `test_the_job_runtime_reads_the_servers_restart_latch` | §4.3 | `create_app` gives the runtime the latch the update routes set |
| `test_queued_jobs_start_when_an_approval_nobody_reads_expires` | §4.2, §4.3 | A queue an approval holds starts when the approval expires, with no other read of the latch |
| `test_an_approval_waits_for_a_job_that_is_being_marked_running` | §4.3 | Marking a job running and approving a restart are ordered by one lock |
| `test_a_retry_made_while_healthy_start_cleanup_runs_is_not_undone` | §2.3, §2.5 | Cleanup's rewrite of the record keeps a suppression lifted while it ran |
| `test_an_outcome_detail_names_the_home_folder_as_a_problem_report_does` | §2.2 | A detail that names a folder under home reads `~` in the status |
| `test_healthy_start_sweeps_staging_that_no_transaction_owns` | §2.5 | The review's U1, inverted, on both paths: quiet unnamed staging and a long-dead owner's staging go; staging still being written and another installation's live staging stay |
| `test_cleanup_spares_another_installations_staging_before_its_journal_exists` | §2.5 | The review's U2, inverted, on both paths: another installation's live owner marker keeps a root the committed transaction named |
| `test_healthy_start_cleanup_leaves_a_pending_wglink_activation` | §2.4, §2.5 | A pending WGLink activation and the add-in it displaced survive both paths, however old |
| `test_the_sweep_spares_the_staging_of_a_handoff_request_that_is_present` | §2.5 | The controller's present request keeps the staging it names; once it is gone, the same staging is swept |
| `test_healthy_start_settles_only_the_build_the_journal_left_installed` | §4.6 | For an installed update, a rolled-back update and a restoring rollback: another commit, the transaction's other build or no manifest in the app layer commits and reclaims nothing and writes the line; the expected build then settles |
| `test_a_browser_mode_start_needs_health_to_name_the_installed_build` | §4.6 | A bundle's own server naming another build is not ready and settles nothing; the same start settles once it names the installed build |
| `test_a_no_gui_start_whose_health_names_another_build_does_not_settle` | §4.6 | Live uvicorn: a self-probe whose `/health` names another build reports once and settles nothing |

The dialog's side of §2.2 and §2.3 is tested in `frontend/src/shell/UpdateControl.test.tsx`
("a held-back build and the last outcome") and `frontend/src/api/updates.test.ts`.

Outside this file: `server/tests/test_bundle_update_installer.py`
`test_a_staging_that_fails_before_its_request_removes_the_folder_it_created` and
`test_staging_carries_its_owner_marker_until_its_request_is_discarded` (§2.5, the server's
side of the owner marker), and
`test_an_expired_restart_revokes_its_request_before_saying_it_did_not_start` and
`test_an_expired_restart_whose_request_was_taken_does_not_say_it_did_not_start` (§4.2, the
request taken back); `server/tests/test_updates.py`
`test_a_checkout_install_latches_the_restart_and_a_failed_handoff_releases_it` (§4.2,
checkout flow), `test_a_checkout_request_is_due_on_arrival_whatever_the_wall_clocks_say`
and `test_an_expired_checkout_restart_revokes_its_request` (§4.2, checkout readiness and
the request taken back), `test_the_update_status_reports_a_pending_wglink_activation` and
`test_the_update_service_imports_no_cad_link_module` (§2.4, WGLink's partial success); `server/tests/test_statusapp_controller.py`
`test_the_status_window_polls_on_until_the_interface_is_served` and
`test_the_status_window_reports_a_start_that_cannot_confirm_the_update` (§4.5, browser
mode); and in `server/tests/test_desktop_launcher.py`, the window's refusal of a second
update while rollback material is pending now also checks that it releases the latch
(§4.2), and `test_a_window_start_that_cannot_confirm_the_update_says_so` keeps the window's
own report (§4.5).

§4.3's tests run the job runtime in one process, with an engine that stands in for a
solver. The restart itself, through the launcher, is not run with real processes. §4.4
needs real processes, and its tests belong with its implementation.

The released-launcher test is the only one that runs real released code. Since
`b19912ec` the server test job fetches the `v0.3.1` and `v0.3.2` tags and sets
`WG_REQUIRE_RELEASE_TAGS=1` (`.github/workflows/ci.yml`), so it runs on all three CI
runners, and a missing tag fails the run instead of skipping the test.
