# Full-installer handoff

The default `/api/updates` service sends signed full installers. Its checker,
download proof, eligibility and status schema are specified by
[INSTALLER-CLIENT-CONTRACT.md](INSTALLER-CLIENT-CONTRACT.md). Source, portable,
unregistered, translocated and unsupported installations are notify-only.

The receiving app/runtime bridge remains available through its original
modules, classes, request kinds and health identities for the bridge release
and its successor. This service never selects or sends a layer transaction.
Tests can inject the original `UpdateService` to exercise that receiving seam.

## Approval and publication

`InstallerUpdateService` starts one client download. After signed verification,
its callback approves the existing `RestartApproval` before rechecking the
manifest, file bytes and exact destination. This orders approval against job
admission. Publication uses an fsynced temporary file and a same-directory
no-clobber hardlink, ordered under the approval lock against release and expiry. Hashing and preparation hold neither service nor approval lock. A failed verification or publication releases that exact
approval; a release observer revokes only the request object it published.
An earlier request cannot be overwritten or revoked by a failed new writer.

The private launcher request has `schemaVersion: 2`, `kind: full_installer`,
`tag`, `version`, `fromVersion`, `platform`, `installer`, `installRoot`, `size`,
`sha256`, `approval`, `readyAtEpoch` and `expiresAtEpoch`. Consumption validates bounded regular
JSON through a nonblocking descriptor with no symlink following and before/after object checks, canonical paths, the launcher's own exact install root and the expected
asset under the application's download directory. Expired approvals are discarded. Delayed new requests never
fall through to the legacy reader. Consumption does not clear the server's
restart approval. The frozen request retains its approval expiry. The launcher checks it after hashing and extraction and immediately before starting a helper. The launcher checks the installer checksum before starting
any helper through a regular-file descriptor with identity checks, and the POSIX helper checks it again after waiting.

The launcher stops its owned backend before launching the installer helper.
The existing stop protocol marks jobs `interrupted_by_update_restart`, finishes
store shutdown and checkpoints the database. If helper preparation or process
creation fails, the launcher starts the previous server again. A helper never
uses Python from the installation it changes.

`POST /api/updates/reset`, with `X-WG-Update: reset`, clears a finished download
for a retry; active workers and pending restarts refuse it. Channel changes are
also refused during a pending restart. `/api/updates/diagnostics` returns a
32 KiB tail of the bounded installer log with the home folder scrubbed.

## Application data

The installer, helper and recovery entry change only the installation and the
`update-install` folder; they never open or move the jobs database in `db/`.
The new app first opens it after the install commits, so a failure before that
leaves the previous app's database unchanged. A schema upgrade by the new app
is protected by its own snapshot and by older releases refusing a newer schema
(`SHUTDOWN-AND-RECOVERY.md`, "Returning to a release before CAD intent jobs").

## POSIX installation

POSIX native and independent recovery output share a standalone native logger.
The bundle builder compiles it into `runtime/bin/wg-installer-log` before the
runtime archive/manifest, so the retained 0.3.2 app/runtime receiver delivers it
to bridge B. Its source fingerprint changes the shared runtime identity on
every platform, preventing reuse of a runtime that lacks the logger. Fresh
installs also have an outer recovery copy. Handoff copies/fsyncs the delivered
runtime binary outside the app/runtime before either can be replaced. Both
packaged locations are covered by their manifests and the macOS seal.
Installed users need no compiler.
`install.log` and one `install.log.1` rotation each stay within 256 KiB. Existing
slots are adopted only when they are bounded regular files with one hardlink
and the owned log header; linked, aliased or foreign slots are preserved.
The logger captures slots with nonblocking, no-follow opens and validates retained
regular-file identities. Rollover truncates/writes those retained descriptors;
later pathname substitutions are preserved and cause diagnostic discard.
Recovery acquires exact native/claim ownership before logging, so there is
only one writer. The logger records its wrapper, supervisor and sink PIDs separately; a gated
sink cannot touch logs before its actual PID is durably published.
An open FIFO reader survives writer failure and drains output without changing
the native verdict. Its native supervisor keeps draining while a sink is blocked,
signals its captured child after five seconds without pipe progress, and polls
for exit without blocking the reader. Final EOF waits at most five seconds plus
a short kill grace; an unsettled child retains its recorded exclusion.
The shell wrapper also bounds final drain and reaps only recorded owned children.
Sink PID-record publication failure signals the actual spawned child and polls
for exit; an unsettled or ambiguous sink record preserves ownership.
`install.log.error` is a bounded, separate JSON diagnostic with `schemaVersion: 1`
and `kind: installer_log_error`; it is never an outcome or recovery ledger.
Before installation, unavailable diagnostics can refuse the new attempt. Once
recovery is admitted, log slot capture runs in the asynchronous sink and
unavailable diagnostics are discarded while exact identity reconciliation
continues, preserving foreign log files and the actual verdict.
If unidentified diagnostic claim material remains after that decision, the exact
recovery directory is moved to a unique `.decided.<pid>` sibling before native
exclusion is released. That preserves the occupant while clearing the fixed
startup route; it never turns a completed restore into rollback failure. A live
or ambiguous sink surviving supervisor death keeps its recorded ownership in
the shared helper lock, so the decided app may start but another installer
cannot start a competing diagnostic writer before that ownership is settled.


The launcher copies a host `/bin/sh` helper outside the app and runtime. Linux
archive extraction happens before detachment into a private directory. Every
member is validated before extraction: no absolute or parent traversal paths,
special files, repeated paths, outside links or links escaping through earlier
links. Extraction uses Python's data filter and bounded member/size totals.
Confined runtime symlinks remain supported. The helper later invokes the
tarball's native Bash installer with `--update --prefix <exact parent>`.

On macOS the helper mounts the verified DMG read-only at a fresh explicit
mountpoint, runs `Install Waveguide Generator.command --update <exact .app>`
with `/bin/sh`, and detaches that mount. No alternate Applications directory
or volume-name fallback is used.

The helper has an independent data-directory lock and reserves the native
per-target installer lock before waiting up to 120 seconds for the launcher
PID to exit. The native installer adopts the same device/inode lock object
only when its sole `pid` record names the live helper. Native startup refuses
the installer lock or an unfinished recovery ledger before entering the app
runtime. Existing installer locks are never reclaimed from a dead PID alone.

The native installer writes a private, data-only identity ledger beside the
lock and syncs it before the first rename. The format is:

```text
WG-INSTALL-JOURNAL-1
<exact installation target>
<row count>
<live path>
<backup path>
<staged path>
<old device:inode, or empty for an initially missing object>
<new device:inode>
...one five-line group for each row...
COMMITTED (only after all installed objects were verified)
```

macOS has one app row. Linux records the application, desktop entry, icon,
desktop/icon ownership markers and command link. The ledger is never sourced
or evaluated. Native no-clobber moves and identity reconciliation keep the
existing rollback guarantees. Exit 1 means the previous objects were retained
or restored; exit 3 preserves backup paths and the ledger. A kill or power loss
leaves its saved identities available to the independent recovery entry. A new
native installer refuses an unfinished ledger rather than overwriting it.

The launcher stages a private host-shell recovery entry and data configuration
beside the exact destination before detachment. It captures the old root
identity, helper data folder and integration paths. Native startup consults
this entry before entering the app or runtime whenever its folder, native lock
or ledger survives. The layer bridge retains earlier macOS C/Linux shell
entries, so the updated app also checks at the earliest desktop entry before
controller, server or UI imports. This compatibility guard uses the same
external shell while the old root and interpreter are intact. A dead PID alone cannot authorize deletion: the entry
checks bounded record structure, every permitted path family and every occupied
old/new device/inode identity, and refuses foreign/missing objects, live owners,
unknown lock contents and concurrent recovery. Recovery holds the native target
exclusion while restoring. It archives the decided ledger and persists a
`failed`, `previousKept: true` outcome before the previous version starts.
A kill during staging is recoverable only if the originally captured root is
still the exact old object. No unrecorded destination is ever selected.

An adopted native installer checks that its helper remains alive and is its
actual operating-system parent during staging polls and transaction boundaries.
Helper death before the durable commit cancels staging or triggers the native
rollback. Successful rollback retires the ledger only after its backups are
gone. A successful install syncs `COMMITTED`, preserves the full identity table
as an external hardlinked receipt, and then retires backups. If the helper dies
after that decision, recovery verifies every exact new live identity and
publishes `installed`; it preserves any remaining old backups. Backup cleanup
leftovers retain an archived committed ledger.

The helper runs the native installer in the foreground with catchable signals
and drains a private FIFO logger. Parent cancellation is deferred through native
rollback; ignored dispositions are not inherited by the installer. Output uses
a 256 KiB rolling log. The bounded atomic outcome has `from`, `to`, `result`,
`when` and `log`. Results are `installed`, `failed` and `rollback_incomplete`.
An incomplete rollback adds separate `journalPath` and, when a saved old object
is still verified there, `backupPath` naming its actual retained location.
A missing or restored first-row backup is never reported as the retained object.
`previousKept` requires a verified old-root identity after native rollback or
complete identity reconciliation by the recovery entry. An incomplete rollback
never relaunches.

Recovery fails closed on ambiguous records and preserves their objects for
manual diagnosis. Real subprocess tests exercise helper-only and native kills,
identity recovery and signal handling. They do not establish power-loss
persistence or signed DMG behavior; those remain native qualification gates.
Whole-root replacement briefly makes the target path absent. If both helper
and native installer are forcibly killed during that interval, the staged
external recovery entry must be invoked directly before the target can be
opened; the previous executable cannot start from an absent pathname.

## Windows installation

The signed setup executable is the external helper. It receives `/DIR=<exact
registered root>`, `/WAITPID`, `/OUTCOME`, `/WGLOG`, `/RELAUNCH`, `/VERYSILENT`,
`/SUPPRESSMSGBOXES` and `/NORESTART`. Setup owns `WaveguideGeneratorSetup` before
waiting for the old launcher and before any layer rename. Its recovery record
names the exact root and displaced paths, and is flushed before the first
rename. Outcomes map successful setup to `installed`; incomplete restoration
records `rollback_incomplete` and preserves its recovery marker/backups.
`/WGLOG` selects the standalone native bounded writer; manual `/LOG` remains
compatible with ordinary Inno logging and is not used by the updater.

The Windows pre-runtime startup and recovery implementation is specified by
[WINDOWS-INSTALLER-RECOVERY.md](../../WINDOWS-INSTALLER-RECOVERY.md). Its source is produced and reviewed independently of this POSIX handoff.

The generated recovery hook and desktop bootstrap check the setup mutex before
starting recovery or the application. They retry for up to 120 seconds, close
every probe handle and refuse access-denied/unknown lookup failures. The
installer's existing delayed relaunch enters this guard; a delay alone does
not authorize launch into an installation still owned by setup.

Python tests exercise sending approval, dispatch refusal, tar safety and real
POSIX rollback/crash records. Windows setup compilation and live installation,
mutex, timeout, rollback and relaunch remain Windows qualification gates.
