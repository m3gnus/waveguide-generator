# Windows native installer recovery

The public `Waveguide Generator.exe` is a static-CRT Win32 program. It loads no
bundled Python or WG DLL until native startup has reconciled the install
journal. The interpreter remains `wg-python.exe`; the existing receiving
bridge files and their methods are retained unchanged.

## Bridge and startup admission

The runtime manifest declares `wg-native.exe` to the public executable and
`pythonw.exe` to the hidden interpreter, with the existing flat DLL destinations.
The v0.3.2 receiver already supports these flat `launcherFiles` entries, including
its reversible root-file refresh. Thus the old receiving route installs the
native entry in B. The public native entry is the final manifest entry, after
hidden Python and every DLL prerequisite, so interruption between completed
receiving publications cannot expose native startup without its interpreter.
The original receiver's two-rename gap can leave the public filename absent;
this unchanged legacy boundary is separate from full native installer recovery.
New full installers publish a stable native entry before layer displacement.

Native startup holds `WaveguideGeneratorSetup` and an exclusive OS-held
`.wg-install-lock` handle while it checks recovery, writes its fixed
`.native-start/sitecustomize.py` admission hook and creates Python. Reparse roots,
reparse hook objects and unknown sidecar entries are refused without removal.
A first changed hook must exactly match the old installed runtime's
`wg-startup-hook.py`. The native-owned `known-hooks` binary ledger then records
exact proven hook bytes, bound to root and sidecar directory file identities,
with a CRC and limits of 1 MiB, 64 records and 64 KiB per record. Total/count/length
are validated before use; malformed, foreign and exhausted history is refused
without rewriting it. History is persisted before hook/public entry publication,
so rollback, bridge runtime replacement and interrupted entry publication retain
recognition. Startup always replaces recognised bytes with its own embedded hook
before loading Python. History is recognition data, never executable input.
Both C and current hook source bytes are included in the runtime fingerprint.

Sidecar temporary files are created with `DELETE` access and
`FILE_FLAG_DELETE_ON_CLOSE`. After a complete flush, native code clears on-close
state using the extended `FileDispositionInfoEx`/`FILE_DISPOSITION_ON_CLOSE`
operation, closes and atomically renames the file. Unsupported extended
disposition is a refusal; the basic disposition class is never a fallback.
Writer termination before clearing deletes the partial file. Residual complete
fixed decimal-PID temporary names are reconciled only when their bytes prove
a full identity-bound CRC ledger or an exact recorded/embedded hook; unknown,
partial, corrupt and reparse files are preserved/refused. These Windows semantics
have explicit native tests. Hardware power loss remains separately qualified.

Only explicitly listed inherited handles reach Python: private ACK/release
events, the running-app mutex, and duplicated standard streams. The hook
verifies the actual parent PID, creation time and bundle image, then duplicates
the parent's event/mutex handles and compares their kernel object identity to
the inherited handles. Numeric environment fields alone confer no admission.
Failure exits before worker code. Capability fields are removed from the
environment and handles made non-inheritable. The child ACKs interpreter
initialization, then waits for native release. Native setup exclusion remains
held until that ACK. The running mutex remains held by parent and child until
exit, even if the native parent is killed.

Every admitted Python process binds `sys.executable` back to the public native
entry so nested workers repeat admission. `-c`, `-m` and script argv/quoting are
forwarded; native startup adds `-B` so its source-only sidecar creates no bytecode
cache. Argument-free starts restore the public executable identity and empty
argv before invoking the original receiver/bootstrap. This also starts a
restored v0.3.2 bootstrap without adding handshake code to that old app layer.
The hidden pth is native-owned compatibility state; the old public pth is kept.

## Installer transaction

Inno holds its existing setup mutex before `ssInstall`. It first performs the
bounded `/WAITPID` wait, then checks `WaveguideGeneratorRunning` under that same
exclusion. Ordinary setup refuses a running app; requested `/WAITPID` additionally
waits up to 120 seconds for all native app/worker running handles to disappear.
No mutation begins before these checks.

The binary `.upgrade-in-progress` journal has schema 2, a CRC, exact root
directory identity, process PID plus creation-time identities, and old/new
volume/file-index identities for app, runtime, recovery and the fixed root
interpreter/DLL/pth/icon/readme set. Names are fixed by the native program;
journal contents cannot nominate arbitrary deletion paths. The journal and
reserved directories are persisted before the first rename. New layers start
in preidentified empty directories; root boot files are copied to staging.

Commit validates these identities, flushes every copied file, records the
complete staged boot identities, moves boot files into place, then atomically
persists the committed journal. Old backups are removed only after that durable
commit. A killed cleanup keeps the complete new tree. An uncommitted journal is
reconciled before Python starts: only recorded new objects are removed and only
recorded old objects restored. A completed rollback is persisted before cleanup,
so killed cleanup can be retried without mistaking restored objects for partial
new ones. Foreign objects are preserved and cause refusal.
Native traversal/moves use extended-length Win32 paths so insertion of a backup
component does not break legal payload paths near the installer's path budget.
User-visible outcome paths retain their normal drive/UNC spelling.

Single-EXE Inno setup has a loader and a temporary installer process. The native
watchdog records loader, installer and active mutator PID plus creation time.
It observes exact opened process handles; loader-only death stops only matching
recorded installer/mutator identities before taking native exclusion and the
transaction lock to recover. Commit requires the exact live watchdog handle.
The transaction lock serializes startup, watchdog and remaining commit/rollback
helpers; process death releases it automatically.

Outcome records are atomically written/flushed UTF-8 with `installed`, `failed`
or `rollback_incomplete`. `previousKept:true` requires actual old app/runtime
restoration. `backupPath` names a verified old object still present; it is never
the journal or a backup already restored. `journalPath` is separate diagnostics.
Refusal preserves the journal/backups. Uninstall explicitly removes the owned
native sidecar, lock and staging names as well as the normal bundle layers.

## Bounded updater diagnostics

The detached updater passes `/WGLOG=<data>/update-install/install.log` instead
of Inno's vendor `/LOG`. `SetupLogging=no` prevents an automatic unbounded
temporary debug log; explicit manual `/LOG` remains available and cannot be
combined with `/WGLOG`. Setup extracts its own static native helper during
initialization, before replacement. Logger modes return before application
admission, install exclusion or journal dispatch, and load no bundled Python.
Each invocation briefly uses a native supervisor and worker. The supervisor
retains the exact created process handle and stops only that worker after a
five-second deadline. Logging never changes the recorded installer genealogy.

The writer measures UTF-8 bytes and bounds each record before writing. Active
`install.log` and its sole `install.log.1` backup stay within 262144 bytes.
Rollover replaces only the previously verified backup object; a raced new
occupant is preserved and refused. The writer holds a real non-reparse parent
directory, an exclusive OS-held `.install-log.lock`, and regular single-link
leaf handles. Reparse, directory and multiply linked leaves are preserved.
Existing oversized logs are normalized through bounded reads to valid UTF-8
tails before an update proceeds. Interrupted final UTF-8 characters are trimmed;
invalid interior bytes cause refusal. Process death releases the logging lock.
These fixed diagnostic files live in retained user data, outside the bundle.

The log records real setup/refusal and wait results, native protection calls,
throttled exact progress with the current actual copy filename, WGLink output,
outcomes and exit. It is a WG diagnostic stream; explicit `/LOG` supplies the
vendor per-file debugging transcript. Initial log failure refuses before
application mutation. A later failure disables further Inno logging attempts.
Native verified outcomes and watchdog recovery append their actual verdict
only after the outcome is durable; failed diagnostics never change that verdict
or authorize a commit. A killed writer may lose its final bounded record.
Early Inno loader failures before script initialization have no WG log and
cannot have begun this protected replacement.

## Required evidence

Mac static/unit tests and cross-compiling with real Win32 headers prove source
contracts/compilability only. They do not qualify native Windows installation.
Windows-native tests compile with MSVC and exercise exact old-hook rollback,
old-receiver bridge admission, changed-hook rollback/next setup, real terminated
sidecar/entry writers, extended disposition clearing/rename survival, bounded
history refusal, capability refusal, admitted nested/quoted argv, malformed
journals, foreign directory/root refusal, long backup paths and boot/layer
transaction behavior. Logger rows additionally cover continuous size checks,
single-slot rollover, Unicode/oversized inputs, foreign/raced leaves, actual
terminated/timed-out workers and flooded verified native outcomes. Actual Inno
`/WGLOG` install/timeout/copy-failure/hard-kill runs remain required separately.
These rows are explicitly skipped elsewhere.

Run `installers/windows/gates.ps1 -Setup <exact-artifact> -RunInterruptedInstall
-RunWaitPidTimeout` in a disposable Windows account. Gate install roots, data and
Fusion AddIns are private temporary folders; the product's normal per-user
uninstall registration is still exercised. Gates 17/18 observe actual copying,
kill only their recorded setup PID (loader only, then tree), restart through the
native entry, and require exact prior layer/boot hashes, a running old interpreter,
and `failed`/`previousKept:true`. Failure to observe real mid-copy interruption is
a failure, never a pass. Forced probe cleanup cannot satisfy success.

The release additionally owes real v0.3.2-to-B receiving and B-to-B+1 native
installer runs with the intended artifacts, manifest comparisons, removed module
and runtime-package checks, unchanged user-data hashes and silent WGLink
preservation. Actual power-loss/VM reset at journal/rename/copy/commit boundaries
is separate evidence; ordinary taskkill tests do not prove hardware durability.
