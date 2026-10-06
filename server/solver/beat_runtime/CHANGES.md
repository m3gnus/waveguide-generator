# Review slices

All paths are relative to the WG repository root. Changes are additive; no
production caller adopts beat-engine, no pins change, and HBB state is untouched.

- **PR 2 — paths/assets:** `server/solver/beat_runtime/{__init__,paths,assets}.py`;
  `server/tests/beat_runtime/test_{paths,assets}.py`; this `CHANGES.md`.
  WG overrides select bases with `wg-beat-engine` appended. Asset discovery is
  lazy, uses beat-engine's public API, and reports missing package/wheel assets.
  **Review round 1 fixes:**
  - P1/B — fixed: Windows default is `%LOCALAPPDATA%/WaveguideGenerator/beat`;
    overrides still append `wg-beat-engine`. Installer's Windows `dl/x` staging
    and `julia/1.12.7` target keep the conservative worst member/backup length
    at 246 characters with a 20-character username (below MAX_PATH).
  - P1/D and P2/J — fixed: shared `paths.is_link` includes junctions/reparse
    attributes; `paths.hbb_executable` compares resolved paths with the existing
    HBB-root definitions, including aliases. No provider/protocol redefinition.

- **PR 3 — identity:** `server/solver/beat_runtime/identity.py`;
  `server/tests/beat_runtime/test_identity.py`.
  Hash named bytes recursively, including versioned manifests and selected
  project/sysimage; hash WG runtime and compiled policy separately. Unavailable
  required inputs raise; content identity has no absolute paths or stale cache.
- **PR 4 — threads:** `server/solver/beat_runtime/threads.py`;
  `server/solver/beat_threads.py`; `server/tests/beat_runtime/test_threads.py`.
  Resolve beat-engine AUTO to an integer once for future key/start/probe/warm-up use.
  Preserve the legacy HBB façade's non-Metal `"auto"` and Metal headroom.

Scope notes: positive explicit thread counts are strict (invalid values raise).
Directory symlinks in fingerprint trees are refused rather than silently omitted.
Host keys/start integration and private directory creation belong to later PRs;
the thread snapshot boundary is exercised with fakes here. No CUDA/ROCm changes
or device qualification are included.

- **PR 5 — state:** `server/solver/beat_runtime/state.py`;
  `server/tests/beat_runtime/test_state.py`; this `CHANGES.md`.
  Store `julia.json` and independent `state-{cpu,metal}.json` under
  `paths.runtime_dir()`. Stamp provider/schema/UTC update time; reject corrupt,
  foreign, incomplete and backend-mismatched records. Backend records retain
  status/step/error, resolved project, engine/runtime/executable identities,
  Julia version, effective depot/environment, optional sysimage identity,
  probe contract/fixture identity and completion evidence. Unknown identities
  are explicit nulls until provisioning resolves them. Unique private sibling
  temporaries, fsync and atomic replacement preserve the previous record on
  failure; backend failures never erase another backend's success or Julia's
  executable record. No legacy single-slot mirror or HBB writes.
  **Review round 1 fixes:**
  - P1/A — fixed: explicit directories and atomic JSON writers use
    `paths.checked_root` before mutation; direct and aliased HBB roots are refused.
  - P2/H — fixed: retain state.py's canonical executable/version/identity/origin
    schema and required updated_at; discovery/installer delegate to it.
  - P2/K — fixed: retain PR 10a's five-attempt Windows PermissionError retry;
    backend and executable-record regressions cover success/exhaustion and cleanup.
  - P3 recursion/stale temporaries — fixed: recursive JSON is absent; sweep only
    regular, unlinked matching siblings older than one day, preserving active files.
  - P3 origin — fixed: validate optional selection provenance in the one schema.

- **PR 6 — locks:** `server/solver/beat_runtime/locks.py`;
  `server/tests/beat_runtime/test_locks.py`; this `CHANGES.md`.
  Serialize shared-Julia provisioning with POSIX flock or Windows msvcrt
  nonblocking byte-zero locking. Keep `provision.lock` permanently; close
  releases the kernel lock after exceptions or owner death. Poll only genuine
  contention, announce waiting once, and propagate unsupported-lock errors.
  `provision.holder.json` is atomic, best-effort diagnostics, never PID authority.
  Tests cover separate processes, owner death, same-process exclusion,
  persistent inode, descriptor cleanup and the Windows path with fakes,
  including contention during initial byte creation.

  **Review round 1 fixes:**
  - P1/A — fixed: explicit lock directories call `paths.checked_root` before
    mkdir/open/holder writes; direct and aliased HBB roots remain untouched.
  - P1/D — fixed: linked/junction/reparse provider roots and lock files are
    refused before opening the persistent lock inode.

PR 5/6 scope notes: target the official JWSound/BEAT_Engine package; neither
module imports beat_engine or changes an existing caller. CPU/Metal only, as
requested; readiness identity matching and completion-proof validation belong
to PRs 11/13. The full HBB status-callback guard (reported-once warning and
idempotent wrapping) belongs to PR 10 provisioning orchestration, not state
storage. Lock wait callbacks are best-effort already. PR 10 must also translate
lock acquisition errors into backend failure records, preserving the original
error if that diagnostic write fails. Actual Windows kernel locking remains
a Windows qualification gate; the msvcrt path is exercised with fakes here.
- **PR 7 — discovery/legacy hint:** `server/solver/beat_runtime/discovery.py`;
  `server/tests/beat_runtime/test_discovery.py`; this `CHANGES.md`.
  Resolve explicit → configured (`WG2_BEAT_JULIA`) → WG `julia.json` → PATH.
  Invalid configured paths raise; removed/edited binaries and foreign records
  are ignored. Opt-in HBB reads supply only an executable hint, regardless of
  legacy status; no HBB directory is written and no readiness is imported.
  **Review round 1 fixes:**
  - P1/A — fixed: discovery record writes check explicit roots before hashing
    or mutation; direct/aliased HBB roots have regression coverage.
  - P2/H — fixed: retain state.py read/write delegation; roundtrip tests assert
    canonical fields and timestamp. The earlier temporary discovery schema is obsolete.
  - P2/J — fixed: explicit/configured/recorded/PATH executables resolving inside
    HBB roots are only legacy hints; discovery returns no adoptable executable,
    so the installer creates WG ownership. Alias regressions cover all sources.
  - P3 origin/launcher/recursion — fixed: record optional selection source;
    one-off explicit records cannot outrank future discovery. Keep launch paths
    as supplied (including juliaup aliases); recursive legacy JSON is absent.

- **PR 8 — downloads/checksums/disk:** `server/solver/beat_runtime/installer.py`
  (release matrix, fetch/checksum helpers and disk budgets);
  `server/tests/beat_runtime/test_installer_downloads.py`; this `CHANGES.md`.
  Julia 1.12.7 covers macOS arm64/x86_64, Windows x86_64 and Linux
  x86_64/aarch64. Injectable fetchers write `.part`; SHA-256 verification
  precedes atomic publication. CPU requires 2 GiB and GPU budgets remain 6 GiB.
  Offline/interrupted downloads and checksum failures preserve prior archives.
  **Review round 1 fixes:**
  - P1/A — fixed: standalone download destinations and optional provider roots
    are checked before directory creation. Fixtures use separate WG/HBB trees.
  - P1/D and P2/E — fixed: download directory/file checks include junctions
    and reparse points; link checks stop at the provider root. Ancestor aliases
    are allowed only with resolved containment below that root.
  - P3 MB progress — fixed: default downloads report MB progress about every
    five seconds via the guarded status callback; fake clock/response regression.

- **PR 9 — extraction/recovery:** `server/solver/beat_runtime/installer.py`
  (extraction, ownership/recovery and executable selection);
  `server/tests/beat_runtime/test_installer_extraction.py`; this `CHANGES.md`.
  Validate ZIP/tar staging before publishing a version/platform tree, recover
  interrupted staging/replacement, and preserve older WG installs for workers.
  External/custom executables and explicit older Julia remain usable; legacy
  managed hints trigger a new WG download. Unowned trees and linked staging
  paths are refused; no external or legacy install is deleted. External choices
  are recorded without stamping the requested portable version onto them.

  **Review round 1 fixes:**
  - P1/A — fixed: ensure/extract and mutating directory helpers check explicit
    roots before filesystem mutation, including recovery and staging cleanup.
  - P1/B — fixed: short Windows staging/install layout and pre-download/extract
    MAX_PATH gate. Without LongPathsEnabled, oversized roots produce a clear
    shorter-WG2_BEAT_RUNTIME_DIR/enable-long-paths error before mutation.
  - P1/C — fixed: write `.wg-staging.json` before unpack; refuse unowned staging
    and reserved archive markers; remove only owned staging, retaining its marker
    after interrupted cleanup. Regressions cover refused and owned stale trees.
  - P1/D — fixed: every former symlink check includes junction/reparse points,
    including root, downloads, staging, target, backup, markers and archive trees.
  - P2/E — fixed: allow symlinked ancestors (e.g. /home, /tmp, /var, .local),
    compare resolved containment, and refuse directory aliases at/below the
    provider root. Internal archive library links are checked separately.
  - P2/F — fixed: restore a valid owned backup over an owned invalid target;
    recover before network/disk checks, including missing/invalid executable
    records. Backup deletion is best-effort and retried even with a valid record;
    markers survive open files and directory sharing violations. Unowned trees
    are still refused, and publication cannot fail merely because cleanup is busy.
  - P2/G — fixed: tar symbolic/hardlink targets must remain within their promoted
    top-level tree; check again after extraction for indirect symbolic-link escapes.
    Internal library links remain valid; escapes/chains/hardlinks have regressions.
  - P2/H — fixed: executable records use state.py exclusively, retaining PR 10a.
  - P2/I — fixed: obsolete legacy records are ignored by canonical reads; a
    fresh valid PATH result is not excluded because an old record names it.
  - P2/J — fixed: HBB binaries from explicit/configured/PATH/old external records
    trigger a WG download; their files/records remain untouched. No HBB reuse.
  - P3 ZIP/origin/launcher/caps — fixed: normalize backslashes before traversal
    checks; keep one-off explicit choices out of future precedence; preserve launch
    aliases; cap unpacked data at 4 GiB and member count at 100,000 for ZIP/tar.

PR 9 review subdivisions: 9a archive/layout safety (`installer.py` layout,
private-directory, unpack and publication helpers; `test_installer_safety.py`
through archive limits, existing extraction tests); 9b recovery/selection
(`installer.py` owned cleanup/recovery and ensure_julia; `test_installer_recovery.py`
and selection regressions in `test_installer_safety.py`). Shared paths.py changes
belong to PR 2 and discovery/state contracts remain in PRs 5/7/10a.

PR 7–9 scope notes: the target is official JWSound/BEAT_Engine (`beat-engine` /
`beat_engine`); these modules need no engine import or WG-specific engine API.
`julia.json` now uses state.py's provider/state_schema/updated_at plus executable,
version, origin and identity (SHA-256 of executable bytes), with optional selection
provenance; never backend readiness. PR 10a completed read/write delegation.
Installer callers must hold the provisioning lock; PR 10c owns orchestration.
All five official artifact checksums are pinned, extending HBB's Windows-only
pin to avoid a second checksum request. Archive layout checks additionally
support macOS application bundles and reject escaping paths/links. Tests use
only temporary directories and fake fetchers; no Julia or network is invoked.
- **PR 15 — IPC/endpoints:** `server/solver/beat_runtime/ipc.py`;
  `server/tests/beat_runtime/test_ipc.py`; this `CHANGES.md`.
  Length-prefixed UTF-8 JSON objects default to a 1 MiB control frame ceiling;
  numerical streams can explicitly opt into HBB's 512 MiB ceiling.
  Clean EOF is distinct from truncated headers/bodies; invalid JSON, nonfinite
  constants, nonobjects and oversized frames are refused. Endpoints are Unix
  sockets or IPv4 loopback only; encoded Unix paths above 100 bytes fall back
  to TCP, including an explicit Unix preference. Binding never removes an
  existing socket or enables address reuse. Bound sockets are private on POSIX.
- **PR 15 — Review round 1 fixes:** `ipc.py`, `test_ipc.py`; this `CHANGES.md`.
  - P2/D — fixed: explicit endpoint directories and direct Unix binds call
    `paths.checked_root` before mutation.
  - P2/F — fixed: `receive_frame(max_bytes=1 MiB, deadline=...)` rejects the
    header before body allocation and applies one monotonic deadline to every
    receive. Cleanup shares that deadline across connect/hello/shutdown/exit.
  - P2/H (post-bind residue) — fixed: failed chmod/listen removes only the
    socket successfully bound by this invocation; failed bind preserves it.
  - P3 (exclusive Windows listener) — fixed: `SO_EXCLUSIVEADDRUSE` precedes bind.
  - P3 (nonfinite JSON) — fixed: reject constants and overflowing floats such
    as `1e999`, including nested values.
- **PR 16 — registry/cleanup:** `server/solver/beat_runtime/{registry,cleanup}.py`;
  `server/solver/beat_runtime/windows_security.py`;
  `server/tests/beat_runtime/test_{registry,cleanup,windows_security}.py`;
  this `CHANGES.md`.
  Reuse `paths.worker_dir()` outside swept sessions. Provider/protocol-scoped
  keys, strict records, unique atomic temporary files, 0700 roots and 0600
  record/spec/lock files replace HBB's permissive record handling. Corrupt,
  foreign, linked and nonprivate records raise `RecordRefused` and remain.
  Persistent advisory locks cover recheck/publish and cleanup; kernel release
  on owner death and fixed Windows byte zero preserve spawn exclusion.
  Cleanup verifies provider/key/HMAC and the host's returned PID before live
  shutdown, using the authenticated connection instead of PID signals. A
  refused/missing endpoint plus a proven-dead or reused PID permits scoped pruning;
  any answering peer must authenticate even when the recorded PID is dead.
  Unverified live hosts and changed/successor records are retained and reported.
  No unauthenticated HBB stale-PID termination path is carried over.
- **PR 16 — Review round 1 fixes:** `registry.py`, `cleanup.py`,
  `windows_security.py`, `test_{registry,cleanup,windows_security}.py`;
  this `CHANGES.md`.
  - P1/A — fixed: fresh nonce challenges and HMAC-SHA256 host proofs for hello
    and shutdown, verified with `hmac.compare_digest`; tokens never go on the
    wire. Shutdown requests additionally prove client knowledge of the secret.
    Reflection regressions cover both operations; a previous hello proof is
    also refused on replay.
  - P1/B — fixed: records require `pid_start` (Linux boot ID + stat field 22,
    POSIX `ps` start time, Windows GetProcessTimes creation time). Positive
    ENOENT/ECONNREFUSED and a dead PID or start mismatch allow pruning; the same
    live host or uncertain endpoint/identity remains refused. No PID signals.
  - P1/C — fixed: ctypes checks the current process token SID against root/file
    owners and validates DACLs. New Windows roots have a protected, inheritable
    user-only DACL; foreign/nonprivate existing roots/files and junction/reparse
    points are refused. Windows branches and native API calls use fakes.
  - P2/D — fixed: explicit registry/cleanup directories and lock roots use
    `paths.checked_root` before filesystem mutation, including TCP records.
  - P2/E — fixed: `cleanup_host(..., lock=held_lock)` validates and reuses the
    held slot lock. The wrapper still acquires exclusion; contention raises
    documented `LockBusy(RecordRefused)` with descriptor cleanup.
  - P2/F — fixed: cleanup uses the PR 15 control limit and one overall deadline.
  - P2/G — fixed: lstat refuses nonregular records before open; O_NONBLOCK and
    descriptor checks also prevent a FIFO swap from blocking record reads.
  - P2/H (recordless socket) — fixed: `sweep_orphan_socket(..., lock=...)` holds
    spawn exclusion, requires no record and S_ISSOCK, and removes only a refused
    or missing endpoint. Listeners, other file types and changed inodes remain.
  - P2/I — fixed: Linux stat and POSIX ps zombie states count as dead. PR 18
    spawn must still reap its child with `process.wait()` as HBB did; liveness
    detection is not a replacement for reaping.
  - P2/J — fixed: strict integer PID range 1..2**31-1 on all platforms (within
    Windows DWORD); invalid/oversized PIDs cannot reach native liveness APIs.
  - P3 (missing unlink) — fixed: record/socket removal uses missing_ok=True.
  - P3 (record recursion) — fixed: RecursionError becomes RecordRefused.
  - P3 (Windows AF_UNIX record) — fixed: refused cleanly before socket creation.
  - P3 (Windows record sharing) — fixed: replace/unlink retry PermissionError
    at most four times with three 10 ms waits, then surface the failure.

PR 16 review subdivisions:
16a records/private publication (`registry.py` through record/spec/token APIs,
record tests); 16b spawn exclusion/liveness (remaining `registry.py`, process-race,
owner-death, timeout and Windows-fake tests); 16c authenticated cleanup
(`cleanup.py`, `test_cleanup.py`); 16d Windows ownership/DACL primitives
(`windows_security.py`, `test_windows_security.py`). Review-round record safety
fixes stay in 16a, process identity/locks in 16b, and HMAC/cleanup in 16c. These
are review slices of the same requested PR 16 scope; no host/client/ownership
implementation is included.

Future host/spawn callers use `host_key`, `key_id`, `new_token`, `HostRecord`,
`write_record`, `write_launch_spec`, `launch_spec_path`, `log_path`,
`spawn_lock_path` and `SpawnLock`. Supply the complete launch identity to
`host_key` (backend, executable/content identities, engine/runtime fingerprints,
resolved assets/project/sysimage, integer threads and effective environment);
the key helper adds WG's provider/protocol namespace. Hold spawn exclusion from
the second `read_record` through publication. Future clients use
`validate_record`, `connect_authenticated`, `send_frame` and `receive_frame`;
hosts use `auth_reply(record, request)` to validate control scope and generate
matching provider/protocol/version, key/id, nonce, HMAC proof and their own
`host_pid` in `hello_ok`/`shutdown_ok`. Clients call
`validate_hello(record, reply, request["nonce"], operation="hello"|"shutdown")`.
The exact HMAC bytes are UTF-8 concatenation of the 64-char lowercase hex nonce,
16-char key ID, decimal host PID and `wg-beat-host:1:<operation>`. Separate
`shutdown_request`/`shutdown` domains prevent reflecting a request proof as a
host acknowledgement. PR 18 must require a client proof before admitting host
submissions; hello proves host identity, while shutdown requests prove client
identity too.
`HostRecord` captures `pid_start` by default; future spawn callers may supply a
verified creation identity explicitly. Missing identities are refused on read
and publication, never replaced by the current process occupying that PID.
`cleanup_host` acquires spawn exclusion or accepts the held slot lock, requests
`shutdown_ok` and waits for host exit. `sweep_orphan_socket` uses the same locked
boundary before a replacement bind. `pid_alive` remains a conservative query,
never authorization to signal. Stream clients can reset the socket timeout
after authentication and opt into the larger numerical frame ceiling.

Deviations: the engine target is official JWSound/BEAT_Engine, as instructed;
this layer imports neither engine nor HBB. Cleanup has no PID-termination API:
authenticated IPC shutdown avoids PID-reuse races between authentication and
signalling. Missing records return None; malformed/foreign records raise rather
than masquerading as missing and allowing replacement. PR 16 is subdivided for
review size. Windows locking/liveness are fake-tested; real host lifecycle,
installed qualification and Windows Job Objects remain later PR gates.

Review-round deviations: HMAC domains include operation/version in the protocol
suffix to prevent cross-operation replay; shutdown requests also authenticate
the client. Pre-host records without process start identity are now refused,
since inferring it from a reused PID would defeat the check. The ctypes Windows
security implementation is a separate small module to keep registry policy
readable. All review findings are fixed; real Windows ACL/kernel qualification
and host/client lifecycle implementation remain later PR gates.

Review-round validation: targeted launcher run of `server/tests/beat_runtime`
and `server/tests/test_solver_beat.py`: 408 passed in 4.31 s. Ruff on runtime
sources/tests and `git diff --check` passed. The exact requested combined pytest
and Ruff commands cannot complete because `server/tests/beat_adapter` and
`server/solver/beat_adapter` are absent from this worktree. No Julia, downloads
or real user/HBB data directories were used; changes remain uncommitted as
requested.

Review round 2 (Sonnet), fixed directly:
- N1 fixed: a stale TCP record whose loopback port another process now answers
  is pruned when the recorded host is provably gone (dead PID or start-identity
  mismatch). The responder is never asked to shut down; nothing is signalled.
- N2 accepted: macOS start identity is `ps -o lstart=` (local time, 1 s); a TZ
  or DST change can make a live host look restarted. Pruning still needs a
  refused or unauthenticated endpoint, so a reachable live host is unaffected.
- N3 accepted: an elevated Windows process creates files owned by
  Administrators, which the owner-SID check refuses, so registry writes fail
  closed for elevated users. WG runs its BEAT host unelevated.
- Spawn-flow requirement (for PR 18): hold the slot's spawn lock until the host
  listens, or the orphan-socket sweep may remove a bound-but-not-listening socket.
- **PR 11 — compiled readiness probe:** `server/solver/beat_runtime/probe.py`;
  `server/solver/beat_runtime/fixtures/probe.msh`;
  `server/tests/beat_runtime/test_probe.py`; this `CHANGES.md`.
  Stage a system-v1 request and tiny closed tetrahedron (four outward faces,
  tag 2, 8 cm coordinate extent) derived from the official CPU bundle warm-up fixture.
  Ask the injected EngineWorker for one 1 kHz exterior normal-velocity solve
  and one pressure observation. File submission uses the public API without
  requiring inline-transport support. Require exactly one matching result-v2
  pressure quantity, a valid 1x1 little-endian complex base64 array, finite
  nonzero pressure and one completed terminal with integer solved_count=1.
  Malformed/nonfinite/zero results, count errors, cancellation, worker/startup
  errors and closure failures return a failed verdict with a reason. Close
  and remove staged files on all paths. No engine/HBB imports or user roots.

Future provisioning/warm-up callers use `compiled_probe(worker, directory=...,
backend="cpu"|"metal")`, supplying an EngineWorker configured with the selected
official system solver, project, environment and resolved integer threads.
The directory must already exist and be caller-owned staging. `build_request`
is pure; `PROBE_CONTRACT` is `system-v1/result-v2`. `ProbeResult.ready/reason`
provide the verdict; `fixture_identity` hashes the actual mesh bytes, and
`completion` contains result_count, solved_count and finite_nonzero only on
success. Store these with the current identity; this module writes no state.

**Review round 1 fixes (PR 11):**
- **P2/C — fixed:** call `paths.checked_root()` before staging; direct HBB
  roots and symlink/junction aliases are refused. Regression tests cover both
  HBB root overrides and symlinks, with no writes below the protected fixture.
- **P2/D — fixed:** require the official `beat-worker` v1 ready announcement,
  engine identity, system-v1/result-v2 and relevant solve/file/precision/phasor
  capabilities, including selected-backend availability. Match result
  diagnostics.bem_backend, precision and phasor_convention to the request;
  float32 requires complex64 pressure. These are fields emitted by the
  official exterior solver and ready contract; no WG engine extensions.
  Regression tests reject missing diagnostics/worker_info, wrong backend,
  precision, dtype, phasor, protocol and capabilities. The docstring states
  that fakes can impersonate the engine; authenticity requires WG's launch.
  A private construction sentinel prevents accidental ready verdicts outside
  `compiled_probe`; it is not an authentication boundary.
- **P3 cleanup/construction — fixed:** use TemporaryDirectory's
  ignore_cleanup_errors=True for Windows handles; fake cleanup regression
  preserves successful readiness. Direct ready=True construction is rejected.

- **PR 17 — client stream ownership:** `server/solver/beat_runtime/ownership.py`;
  `server/tests/beat_runtime/test_ownership.py`; this `CHANGES.md`.
  StreamOwnership serializes host clients around public EngineWorker.submit
  and its closeable iterator. Opaque WG tokens guard close/cancel and every
  release; engine-private submission tokens and process internals are unused.
  Close before first read, terminal release without an extra read, iterator/
  status/event callback errors, failed closure and dropped streams all unwind
  ownership. Closing retains the WG slot until public stream closure finishes;
  late reads, closes, callbacks and cancels cannot release or retire a successor.
  Shutdown closes admission and wakes every queued client before retiring
  active work, even if closure fails. Event handshakes test ordering without
  sleeps; tests use fakes only.

Future hosts create one `StreamOwnership(worker)` and route every client submit
through it. `submit` forwards request/operation/status_callback and optionally
invokes event_callback as events are consumed. Its OwnedStream is an iterator
with token, close and cancel; `cancel(token)` refuses stale identities. Shutdown
ends admission permanently and closes active work. A submit in engine startup
is marked cancelled and its returned stream is closed before handoff; queued
clients wake immediately. Engine startup timeout and idle-worker termination
remain manager policy. No cancellation path calls worker.terminate().

PR 17 review subdivisions: 17a ownership API and sequential lifecycle/callback
tests; 17b retirement-order, shutdown, blocked-reader and finalizer tests. These
are review slices of the same requested PR, keeping each below roughly 400 lines.

**Review round 1 fixes (PR 17):**
- **P1/A finalizer deadlock and P2 dropped retirement — fixed:** finalizers
  only enqueue owner/token pairs in a reentrant SimpleQueue. A dedicated daemon
  reaper starts lazily on first submit, closes through token-checked cancel
  outside caller locks, and releases the slot even on closure failure. Cyclic
  GC regressions collect while the collecting thread holds WG's mutex or a
  fake engine mutex, then prove retirement and successor admission. Duplicate
  queued tokens cannot close a successor.
- **P1/B asynchronous status callback failure — fixed:** every status callback
  passed to the engine catches BaseException, records the first failure on
  the token and queues cancellation through the same retirement path. Later
  callbacks are suppressed. OwnedStream.callback_error exposes the failure;
  startup and subsequent reads surface it after closure. Tests cover
  locked startup and a separate stderr thread after submit returns, including
  KeyboardInterrupt, reader survival and subsequent client admission.
- **P3 terminal race, ordering and concurrent closure — fixed:** preserve a
  received terminal event when cancel races it; document no FIFO ordering.
  close/cancel/shutdown wait for another thread's retirement, bounded by
  retirement_timeout_s (default 5 seconds), raising TimeoutError on expiry;
  reentrant closure never waits on itself. Handshake tests cover all three
  waiting APIs, timeout, reentrancy and terminal races with a successor.

PR 11/17 scope notes: official JWSound/BEAT_Engine supersedes the design's fork
target. Standard-library decoding adapts WG to the official result-v2 wire
format; it does not add a WG-specific engine requirement. A compiled-system
solve does not attest cached bundle loading. The real CPU probe and slice-2
bundle gate remain unrun because this task forbids Julia; no fake is counted as
numerical or bundle qualification. No caller, pins or requirements change.
The exact requested pytest/ruff commands cannot complete in this worktree:
server/solver/beat_adapter and server/tests/beat_adapter do not exist. Runtime
and existing solver tests are validated separately. Changes remain uncommitted
as explicitly requested.

Review round 1 validation: focused PR 11/17 tests passed (105); the targeted
runtime plus existing solver tests passed (436). Runtime/test ruff and
`git diff --check` passed. The exact combined pytest/ruff commands still fail
only because both beat_adapter directories are absent. No Julia, downloads,
real user data writes or donor-checkout edits were performed; fixes remain
uncommitted as requested.
- **PR 10a — executable records/Windows publication:**
  `server/solver/beat_runtime/{state,discovery,installer}.py`;
  `server/tests/beat_runtime/test_{state,discovery,installer_extraction}.py`;
  this `CHANGES.md`.
  Discovery now delegates executable records to state.py. Canonical julia.json
  fields are `executable`, `version`, `identity`, `origin`, provider/schema and
  update time; external version may be explicitly null, managed version must
  be known. Installer uses this schema; temporary discovery/legacy records are
  ignored rather than accepted as executable authority. Windows replacement
  retries only PermissionError, at most five attempts with 250 ms total sleep;
  exhaustion preserves the previous record and removes the sibling temporary.
  **Review round 1 fixes:**
  - P2/H — fixed: retain the canonical schema and state.py-only publication;
    old temporary records never become authority. PRs 5/7/9 exercise roundtrips.
  - P2/K — fixed: keep bounded Windows replace retry; add julia.json reader
    sharing-violation success/exhaustion regressions alongside backend coverage.

- **PR 10b — Julia subprocess steps/status guard:**
  `server/solver/beat_runtime/julia_steps.py`;
  `server/tests/beat_runtime/test_julia_steps.py`; this `CHANGES.md`.
  Injectable Popen streams UTF-8 progress, retains a bounded error tail and
  retires only its own child on interruption. `guarded_status` is idempotent,
  continues offering every line after callback errors, and reports the first
  exception once on stderr (even a broken stderr cannot fail setup).
- **PR 10c — CPU provisioning orchestration:**
  `server/solver/beat_runtime/provision.py`;
  `server/tests/beat_runtime/{conftest,test_provision}.py`; this `CHANGES.md`.
  `provision_cpu` takes the shared provisioning lock before rechecking state,
  resolves official beat-engine CPU assets and a single integer thread budget,
  installs/reuses Julia, then runs instantiate, precompile and a compiled probe.
  Steps and failures are backend-local; Julia discovery and Metal readiness
  survive CPU failures. Failed records are retryable on the next explicit call;
  force/retry bypass a matching ready record. Lock acquisition errors become
  best-effort failed records, retaining the original error if storage also fails.
  **Review round 1 fixes:**
  - P1/A — fixed: check explicit roots before lock acquisition or diagnostic
    writes, including injected environments and aliases. Isolation errors raise
    without attempting to stamp failure records in HBB directories.
  - P1/B — fixed: readiness's upgrade test recognizes the short Windows version
    directory as current, alongside POSIX version/platform directories.
  - P2/J — fixed: discovery excludes HBB-managed binaries before a ready-state
    shortcut; normal provisioning goes through WG-owned installation policy.
  - P3 launcher — fixed: preserve juliaup/launcher paths for steps and the probe;
    content identity and ownership comparisons still resolve the target separately.

- **PR 10d — provisioning identity/reuse coverage:**
  `server/tests/beat_runtime/test_provision_identity.py`; this `CHANGES.md`.
  Project/manifest, executable, threads, effective environment and probe-fixture
  changes invalidate reuse. Instantiation-created manifests participate in the
  saved identity. A ready old portable Julia still goes through the installer
  upgrade policy; explicit older executables remain valid selections. Missing
  optional engine assets and missing probe identities are recorded failures.

PR 10 handoff: `provision_cpu(directory=None, ..., probe=callable,
probe_contract=..., probe_fixture_identity=..., run_step=callable,
ensure_julia=callable)` is additive and returns a ready/failed record. The probe
receives keyword arguments `backend`, `julia_executable`, `julia_project`,
`julia_threads` (the same resolved integer as JULIA_NUM_THREADS), `environment`
(the complete subprocess environment) and `status_cb`. It returns a completion
mapping with `finite=True`, `nonzero=True`, `terminal_count=1` plus any further
JSON evidence. The PR 11 callable must actually validate a tiny COMPILED solve;
these minimum evidence checks do not replace its numerical/terminal validation.
Until PR 11 is wired, a missing probe fails with an explicit TODO diagnostic;
instantiate/precompile alone never produce ready. Current contract and fixture
identities must be supplied for proof and reuse. `run_step` has julia_steps.py's
signature; `ensure_julia` receives the existing installer's arguments.

PR 10 deviations: subdivided for review size. All reuse checks run under the
lock, including already-ready calls, to avoid trusting a pre-lock snapshot.
HBB's bundle-specific Julia probe code is deliberately not transplanted: WG
adapts to the official engine through the injectable PR 11 probe. External
versions remain unknown rather than being stamped Julia 1.12.7. The effective
JULIA_*/BLAB_* environment participates in state identity; unrelated process
environment (including credentials) is passed to children but not persisted.
Readiness façade integration and real Julia/Windows qualification remain later
gates. Tests use temporary trees, fake subprocesses/probes and fake replacement
errors; no Julia, download, GPU setup or HBB write occurs.

PR 10 validation: targeted runtime plus `server/tests/test_solver_beat.py`
passed (370 tests, 4.05 s); runtime/test Ruff and `git diff --check` passed.
The requested combined pytest/Ruff commands could not include beat_adapter:
`server/solver/beat_adapter` and `server/tests/beat_adapter` are absent from
this worktree. The available targeted tests ran through scripts/run_tests.py.

PR 5–10 review-round deviations: official JWSound/BEAT_Engine remains the target;
no engine/HBB import or existing caller/pin/requirement change. The permitted
Windows default/layout shortening departs from the original design's long
provider directory only on Windows; override bases still append the provider.
HBB executable hints download anew instead of copying a legacy tree, avoiding
shared ownership. Invalid explicit roots raise before failure diagnostics because
writing such diagnostics would break isolation. Optional selection provenance is
additive in state schema 1; older canonical records stay readable. PR 9 is split
into review subdivisions to keep archive safety and recovery independently reviewable.
No findings are objected to; all P1/P2 and listed cheap P3 findings are fixed.

PR 5–10 Review round 1 fixes validation: targeted launcher run of
`server/tests/beat_runtime` and `server/tests/test_solver_beat.py`: **643 passed**
in 6.17 s. Ruff on runtime sources/tests and `git diff --check` passed. Every
check completed in under two minutes. The exact requested combined pytest
command ran no tests (exit 4) because `server/tests/beat_adapter` is absent;
the exact combined Ruff command reports only the two missing beat_adapter paths.
No Julia, real downloads, user/HBB-directory mutation, donor-checkout edits,
caller switch or pin/requirement changes occurred. Native Windows and Julia
qualification are outside this fake-only review round; changes are uncommitted
as explicitly requested. `julia_steps.py` needed no change: PR 10b's callback
guard/subprocess tests are included in the passing targeted runtime run.
- **PR 12a — Metal hardware eligibility:**
  `server/solver/beat_runtime/hardware.py`;
  `server/tests/beat_runtime/test_hardware.py`; this `CHANGES.md`.
  `gpu_hardware()` reports Metal eligibility only on Apple Silicon with macOS
  13.3+, refusing Intel Macs and missing/malformed versions. Its `available`
  field is hardware eligibility, never runtime readiness. CUDA/ROCm always
  report `available=False`, reason `not supported in this build`.
  `detect_gpu_backend()` suggests Metal or None without importing an engine.
- **PR 12b — shared orchestration and compiled Metal wiring:**
  `server/solver/beat_runtime/{gpu,provision,probe}.py`;
  `server/tests/beat_runtime/test_gpu.py`; this `CHANGES.md`.
  `gpu.provision_gpu(directory=None, backend="metal"|None, ..., worker_factory=...,
  probe=..., **options)` gates hardware before any filesystem, asset, installer
  or probe work. CUDA/ROCm return explicit unsupported skips. `options` forwards
  the existing CPU provisioning controls, including force/retry, executable,
  project, environment, depot, threads, run_step, ensure_julia and probe identity.
  The CPU public signature is unchanged; its implementation and Metal both use
  `_provision_backend`, with backend-local records and optional setup steps.
  This reuses lock acquisition, locked recheck, discovery/installer, identity,
  julia_steps and the callback guard; state/locks/discovery/installer are unedited.
  Metal selects `assets.engine_assets("metal")` (official `julia_metal`), uses
  the GPU disk budget, instantiates, precompiles and resolves/checks Metal
  artifacts/functionality. None of those steps can establish readiness.
  The default injectable probe launches the optional public EngineWorker with
  the selected system solver/project/environment and the single resolved integer
  thread count, preserving WG Metal headroom. It runs PR 11's compiled_probe,
  requires its verdict and matching fixture/contract identity, then terminates
  its own worker. `probe.fixture_identity()` exposes the actual fixture hash.
  PR 11 result_count/solved_count/finite_nonzero evidence is retained and adapted
  to PR 10's finite/nonzero/terminal_count completion mapping.
- **PR 12c — Metal failure/identity preservation coverage:**
  `server/tests/beat_runtime/test_gpu_failures.py`; this `CHANGES.md`.
  Artifact/functionality, missing assets/package, zero/wrong-backend solve,
  unavailable negotiated backend, wrong probe identity and worker-retirement
  failures remain failed Metal records while CPU readiness stays byte-identical.
  Changed project/executable/environment/threads/fixture require another compiled
  solve. No-device calls preserve both existing records. Tests also exercise the
  default lazy public EngineWorker import with a fake optional package.

PR 12 deviations: official JWSound/BEAT_Engine replaces the design's fork target,
and CUDA/ROCm are explicitly unsupported by owner decision. Split into three
review slices to keep each below the design's approximate 400-line ceiling.
Minimal shared-provisioner parameterization avoids duplicating orchestration;
the small public fixture hash helper avoids duplicating probe-fixture policy.
Device functionality remains a preliminary artifact/device check; only the
compiled result makes Metal ready. No existing caller is switched, pins and
requirements are unchanged, and no HBB writes or stale-PID signalling are added.

PR 12 validation: the 42 new hardware/Metal tests passed; the available combined
targeted launcher run (`server/tests/beat_runtime`, `server/tests/test_solver_beat.py`)
passed 597 tests in 5.96 s. Runtime/test Ruff and `git diff --check` passed.
The exact requested pytest/Ruff commands were attempted but cannot complete:
`server/tests/beat_adapter` and `server/solver/beat_adapter` are absent here.
All checks were unpiped and below two minutes. Tests use temporary trees and
fake steps/workers; no Julia, download, real user/HBB data directory or real
Metal solve was used. Real installed-device qualification remains a later gate,
as required by the no-Julia constraint. Changes remain uncommitted as requested.

- **Review fixes (PR 10/12 round 1, PRs 5-9 round 2)** — files per review slice:
  - PR 2: `server/solver/beat_runtime/paths.py`;
    `server/tests/beat_runtime/test_paths.py`.
  - PR 7: `server/solver/beat_runtime/discovery.py`;
    `server/tests/beat_runtime/test_discovery.py`.
  - PR 9: `server/solver/beat_runtime/installer.py`;
    `server/tests/beat_runtime/test_installer_extraction.py`.
  - PR 10: `server/solver/beat_runtime/{provision,julia_steps}.py`;
    `server/tests/beat_runtime/test_{provision,provision_destinations,julia_steps}.py`.
  - PR 12: `server/solver/beat_runtime/{gpu,hardware,probe}.py`, shared
    `provision.py` completion validation;
    `server/tests/beat_runtime/test_{gpu,gpu_failures,hardware,probe}.py`.
    `probe.py`/`test_probe.py` extend PR 11's completion handoff for PR 12.
  - This `CHANGES.md` accompanies all slices; no PR 5/6/8 edits are needed.

  Findings:
  - P1/A — fixed: shared `julia_steps.julia_environment(project, environment,
    cwd=...)` resolves the project and every explicit/inherited depot entry and
    calls `paths.checked_root` with both the launch and process environments.
    Shared CPU/Metal orchestration validates before installation, each setup
    step and the probe; the subprocess helper also checks before Popen. Depot
    links resolving into HBB are refused, including WG's default depot link.
    Regressions `test_julia_write_destinations_refuse_hbb_before_subprocess`,
    `test_metal_julia_write_destinations_refuse_hbb` and
    `test_subprocess_destination_isolation_precedes_popen` cover projects,
    first/later inherited entries and symlinks; the default WG depot passes.
    These entry points have no sysimage output destination or sysimage step.
  - P2/B — fixed: relative depot entries use a captured launch cwd; the absolute
    effective value is used by both children and readiness identity. Regression
    `test_relative_depot_cwd_identity_prevents_false_readiness_reuse` covers
    argument/inherited paths and changed cwd, with reuse only for the same
    absolute depot. The direct launcher checks all relative list entries too.
    Explicit/configured executable selection and executable records now store
    absolute launch paths, preserving juliaup symlink aliases; discovery and
    installer regressions cover both selection sources and cwd changes.
  - P2/C — fixed: custom Metal probes receive no built-in identity defaults;
    both declared identities are required, and the stored contract uses the
    `custom:` namespace so the ordinary default call cannot adopt injected proof.
    Metal completion requires `bem_backend="metal"`, including cached records.
    PR 11 emits this evidence only after its existing result diagnostics checks.
    Regressions cover missing identities, missing/CPU/Metal completion evidence,
    custom proof followed by a default compiled solve, and older incomplete proof.
  - P3/1 — fixed: extraction may rmdir an empty unmarked staging directory;
    nonempty unowned trees and linked staging remain refused. The marker-write
    failure regression proves the next extraction recovers without a download.
  - P3/2 — fixed: a matching ready record returns before acquiring the shared
    lock; any miss takes the lock and resolves/rechecks again. The CPU fast-path
    regression holds a real Metal provisioning lock and forbids a second lock
    attempt; CPU and Metal locked-recheck regressions still cover competing writes.
  - P3/3 — fixed: linked provider roots are refused before any record read/write;
    diagnostic failure writes also skip a root linked during lock acquisition.
    Both refusal mechanisms have regressions preserving the destination tree.
  - P3/4 — fixed: ignored explicit/configured HBB Julia selections report their
    source and the WG-owned installation choice; both sources have regressions.
  - P3/5 — fixed: Popen uses `stdin=subprocess.DEVNULL`; regression checks the
    child arguments. Its cwd is pinned to the cwd used to resolve destinations.
  - P3/6 — fixed: `paths.is_link` treats NotADirectoryError as an absent path;
    regression uses a regular file ancestor.
  - P3/7 — fixed: the old-SDK macOS `10.16` compatibility value falls back to
    bounded `sw_vers -productVersion`; failures/malformed versions fail closed.
    Hardware documentation explains why x86_64 Python under Rosetta is refused
    (its Julia would also be x86_64); fallback and Rosetta have fake-tool tests.
  No findings are objected to.

Review-fix deviations: official JWSound/BEAT_Engine remains the target. The ready
fast path supersedes PR 10's earlier all-checks-under-lock deviation. Empty
components in a nonempty JULIA_DEPOT_PATH list are explicitly refused because
they expand to Julia-selected implicit depots whose writable destinations WG
cannot validate here; callers must name depot paths. An absent/empty variable
still selects WG's default depot. Custom Metal probe contracts are namespaced
within the existing identity field rather than adding a state schema or engine
requirement. No sysimage generation API is introduced by this review round.

Review-fix validation: the focused changed-area launcher run passed **306 tests**
in 3.30 s. The available combined launcher run (`server/tests/beat_runtime`,
`server/tests/test_solver_beat.py`) passed **731 tests** in 7.23 s. Runtime/test
Ruff and `git diff --check` passed. The exact requested combined pytest command
ran zero tests (exit 4) because `server/tests/beat_adapter` is absent; the exact
combined Ruff command likewise cannot check the absent solver/test beat_adapter
paths. All checks were unpiped and below two minutes. No Julia, network download,
real user/HBB-directory write, donor edit, caller switch, dependency change or
PID signalling occurred. All requested fixes are complete; native Julia/Metal
qualification remains outside this fake-only round. Changes are uncommitted as
explicitly requested.
- **PR 18a — host lifecycle/bootstrap:** `server/solver/beat_runtime/host.py`
  (worker construction, bind, bootstrap publication boundary and teardown);
  `server/tests/beat_runtime/{conftest,fake_host_worker}.py` and lifecycle cases
  in `server/tests/beat_runtime/test_host.py`; this `CHANGES.md`.
  Own one lazily imported official `beat_engine.EngineWorker`, using only its
  public constructor/terminate API. Publish before any Julia startup. Preserve
  HBB's 1800-second idle window, authenticated connection lifetime and graceful
  POSIX SIGTERM/SIGINT exit. Teardown is idempotent, compares the complete record
  and socket inode, and retains successors. If authenticated cleanup holds the
  spawn lock while awaiting exit, leave removal to cleanup.py rather than
  deadlocking. Fixture workers refuse every numerical/start operation.
- **PR 18b — hello/client authentication:** `host.py` (connection admission and
  control dispatch); authentication cases in `test_host.py`; this `CHANGES.md`.
  Reuse registry HMAC hello/shutdown proofs and IPC framing/control deadlines.
  Provider, protocol/version, full key (including engine identity), key ID and
  token proofs must match. A fresh host nonce and separate `client_auth` domain
  authenticate clients before lifetime admission; reflected/replayed proofs
  fail. Hello alone and silent peers cannot prevent idle exit. Bound unfinished
  handshakes to two seconds and 32 connections. Authenticated ping is supported;
  submission, ensure_started, retire and engine adoption return the named
  `HostSubmissionNotImplemented` error until PR 19.
- **PR 18c — spawn/publication:** `server/solver/beat_runtime/spawn.py`
  (start/recheck/publication); `server/tests/beat_runtime/test_spawn.py`;
  this `CHANGES.md`.
  `start_host` holds SpawnLock from the second record lookup through launch,
  private readiness, verified process-start identity, canonical publication and
  authenticated hello. Concurrent threads/processes launch exactly one host and
  construct exactly one worker. Reuse valid authenticated lifecycle records;
  cleanup.py handles proven-dead/reused records and orphan sockets, while
  foreign/unverified live records remain refused. Startup failure reaps only
  the recorded Popen child and permits retry. Parent abandonment before
  publication makes the bootstrap child time out and clean its own residue.
- **PR 18d — subprocess/platform boundary:** `spawn.py` (launch flags, app root
  and native-launcher identity), `host.py` (app cwd);
  `server/tests/beat_runtime/test_spawn_platform.py`; this `CHANGES.md`.
  Launch `sys.executable -m server.solver.beat_runtime.host` from
  `server.platform.paths.app_root()` / `WG2_APP_ROOT`, with app-first PYTHONPATH,
  stdin DEVNULL and a private `<key>.log`. POSIX uses start_new_session. A daemon
  wait thread reaps the parent's child on every exit path. Windows uses
  CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS and preserves native admission:
  the public launcher is a stub, so verify its interpreter's parent/start link
  and publish/authenticate the actual host PID. The packaged ._pth includes app;
  host main restores app cwd after the native launcher starts from bundle root.
  No breakaway flag or Job Object changes: packaged Windows Quit still kills
  this host through the launcher's Job Object (the design's Windows exception).

PR 18 review subdivisions keep lifecycle, authentication, spawn policy and
platform handling independently reviewable; host.py/spawn.py are shared files
across those subdivisions, not separate competing implementations.

Future PR 19 callers use `spawn.start_host(key, directory=None,
idle_timeout=1800, timeout=10)` to obtain an authenticated HostRecord. Keys must
already include backend, julia_executable/julia_identity, solver_script,
julia_project/julia_sysimage (explicit null when absent), integer julia_threads,
engine_fingerprint/runtime_fingerprint and the effective environment dict;
registry.host_key adds the provider/protocol namespace. The environment and
integer count are passed unchanged to the public EngineWorker constructor.
The caller remains responsible for computing complete content/asset identities.
No EngineWorker PID/private fields are assumed, and this slice does not start,
warm or submit to Julia.

Client admission: send registry.hello_message and validate_hello as before;
hello_ok additionally includes client_nonce. Send an `authenticate` frame with
registry.host_key({}) scope, key/key_id, nonce=client_nonce and
registry.auth_proof(record, client_nonce, "client_auth"). The `authenticated`
reply proves the same nonce in the `client_auth_ok` domain. Only this completed
exchange keeps the host alive until disconnect. Existing cleanup.py can send
its independently authenticated shutdown request directly after hello; client
admission is unnecessary for shutdown. Tokens never appear in wire frames or
command arguments. PR 19 must retain this admission gate for engine operations.

Deviations: the engine target is official JWSound/BEAT_Engine, as instructed.
A transient private `<key>.ready.json` extends the proposed directory layout so
the parent can hold spawn exclusion through publication on both POSIX and
Windows without inheriting/transferring an advisory lock. It contains the
child's record and launcher/start link, is atomically written with registry's
private-file helpers, and is removed after publication or abandoned bootstrap.
Unauthenticated hello does not extend lifetime, strengthening HBB's token-on-wire
admission while retaining authenticated clients' idle behavior. Four review
subdivisions replace the estimated single 250–350-line PR. Native launcher
handling is included now because assuming Popen.pid equals host PID breaks
current packaged Windows admission; breakaway remains entirely PR 22.

PR 18 validation: 36 new fake-worker tests passed (17 host, 14 spawn, 5 platform).
The targeted launcher run of `server/tests/beat_runtime` and
`server/tests/test_solver_beat.py` passed: **444 passed in 15.27 s**. Ruff on
runtime sources/tests and `git diff --check` passed. The exact requested combined
pytest and Ruff commands were attempted, but `server/tests/beat_adapter` and
`server/solver/beat_adapter` are absent; those checks cannot complete here.
Real Windows Job Object/ACL and installed runtime qualification remain later
platform gates; Windows flags/start linkage are fake-tested, and a real POSIX
wrapper process tests the distinct launcher/host PID and cwd behavior. No Julia,
downloads, real user data or HBB directories were used. All changes remain
uncommitted, as requested; no pins, requirements or existing callers changed.

- **PR 19a — host submission protocol/FIFO:** `server/solver/beat_runtime/host.py`
  (`_Job`, submission runner/connection reader and authenticated dispatch);
  FIFO, queued withdrawal and heartbeat cases in
  `server/tests/beat_runtime/test_host_submissions.py`;
  `server/tests/beat_runtime/{fake_host_worker,test_host}.py`; this `CHANGES.md`.
  Replace HostSubmissionNotImplemented with authenticated file/inline submission,
  operation forwarding, startup, metadata adoption and idle retirement. Receipt
  order defines monotonically increasing queued sequence numbers; startup and
  submissions share the FIFO. Keep the FIFO head until ownership retirement has
  finished. Independent connection readers notice disconnect/cancel while the
  engine reader blocks. Serialize status, heartbeat, metadata and event writes;
  suppress late status/heartbeat frames after terminal delivery. A next control
  request arriving immediately after a terminal reply cannot cancel completed
  work or corrupt the next exchange.
- **PR 19b — client authentication/control/adoption:**
  `server/solver/beat_runtime/client.py` (connect_client, HostedWorker control,
  lifetime lease, metadata and detach/shutdown);
  admission-proof/control cases in `server/tests/beat_runtime/test_client.py`;
  second-process adoption case in `test_host_submissions.py`; this `CHANGES.md`.
  Reuse start_host and the registry's nonce/HMAC exchange, including verification
  of the fresh client_auth_ok proof. Keep an authenticated lease until detach;
  control requests share one lock, while each submission has its own connection.
  Detach ends local admission and interrupts concurrent control reads. Shutdown
  uses authenticated cleanup, never recorded-PID signalling. Expose a copied
  worker_info, host_pid and host-owned worker_instance for future manager/session
  callers; lazy optional engine construction remains solely inside the host.
- **PR 19c — client streams/callback integration:** `client.py` (remote stream
  and submitter); `server/solver/beat_runtime/ownership.py` (callback failure
  arising inside a read); stream/cancellation cases in `test_client.py`;
  two regressions in `server/tests/beat_runtime/test_ownership.py`; this `CHANGES.md`.
  Public submit returns the existing token-checked OwnedStream and acknowledges
  host queue admission before returning. Preserve Path/Mapping and operation
  inputs, streamed engine events, status callbacks and negotiated metadata.
  Close/cancel before the first read, blocked reads, callback failures and stale
  closes all use the connection's submission and ownership.py's normal path.
  A callback error arriving while next() consumes a remote status frame remains
  the reported error even when cancellation closes the socket or an event arrives.
  Completed streams do not cancel or retire a successor.
- **PR 19d — bounded retirement/recovery:** `host.py` (checked public stream,
  bounded retirement, cancellation and teardown); disconnect, retirement error/
  hang, internal reader failure and active shutdown cases in
  `test_host_submissions.py`; cancellation/failure behavior in
  `fake_host_worker.py`; this `CHANGES.md`.
  Public stream closure runs through StreamOwnership and is bounded to five
  seconds. A closure error/timeout closes host admission before ownership can
  release its slot, wakes queued jobs and exits through normal record/socket
  cleanup. Engine termination at host teardown is also bounded; an unresponsive
  public method cannot hang process exit. Normal cancellation may make official
  EngineWorker's blocked reader raise; that expected error does not stop the host.
  Unexpected submit/read errors conservatively stop the host because the public
  API cannot distinguish an internal retirement failure from another engine
  error. Queued clients never run on an engine whose retirement failed.

PR 19 review subdivisions cover sections of shared files, as in PR 18; they are
review slices of this one requested work item. No existing caller changes.

Future PR 20 callers use `client.HostedWorker(key, directory=None, timeout=10,
idle_timeout=1800)`, with the complete resolved key required by start_host.
`adopt()` returns host_pid, worker_instance, worker_info and engine_pid=None.
`ensure_started(status_callback=...)` explicitly starts the public worker;
`submit(Path|Mapping, operation="solve"|"bem_field", status_callback=...)`
returns OwnedStream (token, close, cancel, callback_error). Engine negotiation
and operation validation remain official EngineWorker policy. Large requests
can be staged as files: host command/inline envelopes retain IPC's 1 MiB control
ceiling; result event envelopes opt into the 512 MiB numerical ceiling.
`ping()` is bounded control; `terminate()` requests idle retirement and returns
False while any host job is queued/active. `detach()` permanently closes this
client's admission/streams and releases its lease; construct a new client for
later adoption. `shutdown()` additionally shuts down the authenticated host.

Wire details: admission uses the existing PR 18 handshake. Submit sends
`{op: submit, request: absolute-path-or-object, operation: ...}` and first gets
`{type: queued, sequence: ...}`. The host streams status, heartbeat, worker_info
reports and `{type: event, event: <unchanged engine event>}` envelopes. Engine
completed/cancelled/failed events are terminal; host failures use type=failed.
A connection-specific `{op: cancel}` or EOF retires only that connection's job
through OwnedStream.cancel/close. Cancellation acknowledgements follow retirement.
Control frames have total deadlines, including partial frames after admission.
Queued/startup/solve streams send 0.5-second heartbeats; clients renew a 10-second
liveness deadline per received frame, with no total startup/solve deadline.
Socket writes remain bounded so a client that stops consuming cannot hang the
host. WG does not read/write request cancel markers here; engine stream closure
provides retirement, and staged cancellation-monitor policy belongs to PR 20.

Deviations and limits: official JWSound/BEAT_Engine supersedes the design's fork.
Official worker.py exposes a copied ready announcement via worker_info, with
engine name/version, protocol, contracts and capabilities, but no PID or unique
Julia-process identity. The host reports its own stable random worker_instance
and that metadata; neither proves the unchanged child Julia PID. The fake-worker
second-process test proves same host PID, same owned EngineWorker identity and
one startup only, not real Julia PID continuity. No `_process` or private engine
submission attributes are read. Candidate generic upstream PR: **read-only
EngineWorker.pid**, returning the live child PID or None. Real Julia/installed
adoption qualification remains owed once that API exists. Unexpected public
submit/read errors stop the host conservatively, rather than silently reusing
an engine whose internal retirement may have failed. Host retirement failures
are contained, but an unresponsive child cannot be proven dead through this
public API; no unauthenticated PID signalling is added. Explicit startup/next
submission handles re-start; automatic background warm-up is deferred to PR 21.
Four review slices replace the design's estimated single small PR.

PR 19 validation: **578 passed in 50.87 s** using the requested Python with
`scripts/run_tests.py server/tests/beat_runtime server/tests/test_solver_beat.py
-q -p no:cacheprovider`, including 27 new PR 19 cases (14 client, 11 host
submission/recovery/adoption, 2 ownership callback integration). Runtime/test
Ruff and `git diff --check` passed. Both exact requested combined checks were
attempted: pytest collected no tests because `server/tests/beat_adapter` is
absent; Ruff reports the two absent beat_adapter directories. No Julia,
downloads, full WG suite, real user/HBB data writes, dependency/pin changes or
reference-checkout edits occurred. Changes remain uncommitted as requested.

**Review round 1 fixes (PR 18):**

Files follow the existing PR 18 review slices; this is one fix round on top of
PR 19, whose submission/client behavior is retained:
- **18a lifecycle:** `host.py`; lifecycle/logging/path/accept regressions in
  `server/tests/beat_runtime/test_host_review.py`; explicit factory calls in
  `test_host.py`; this `CHANGES.md`.
- **18b admission:** `host.py`; pending-peer/capacity regressions in
  `test_host_review.py`; this `CHANGES.md`.
- **18c spawn/recovery:** `spawn.py`; environment/stale-port/closing-host/failed
  bootstrap regressions in `server/tests/beat_runtime/test_spawn_review.py`;
  bounded retained-host test and test-process entry selection in `test_spawn.py`;
  this `CHANGES.md`.
- **18d platform/test boundary:** `spawn.py`; direct-interpreter and owned
  launcher cases in `test_spawn_platform.py`, Windows handle tests in
  `test_spawn_review.py`; `server/tests/beat_runtime/{conftest,fake_host_main,
  fake_host_worker}.py`; `registry.py`'s optional append flag for private logs;
  this `CHANGES.md`.

- **P1/A — fixed:** WorkerHost accepts an explicit engine_factory. Production
  main supplies a lazy factory that imports official beat_engine.EngineWorker;
  no production environment factory switch remains. Tests select a tests-only
  fake_host_main entry through the monkeypatchable spawn.HOST_MODULE constant.
  The spawner strips all WG2_BEAT_TEST_* environment names (case-insensitive).
  `test_production_spawn_ignores_test_worker_environment` exercises production
  launch/main with an import sentinel while builtins:dict is configured; no
  optional engine import or Julia launch occurs.
- **P2/B — fixed:** authentication refusals and refused/missing connections use
  cleanup_host under the held spawn lock. Its dead/reused-process proof remains
  the only pruning authority. Refused live hosts retry within the original
  start deadline; permanently refused live records remain. Host close removes
  its own record/socket before slow engine retirement, with successor checks;
  early record-removal failure still retires the engine and is logged.
  Regressions cover reused TCP listeners, both authentication EOF and connection
  refusal while a closing host remains alive, and replacement during blocked
  retirement. No stale registry PID is signalled.
- **P2/C — fixed:** accept a direct Popen child or a verified launcher/start link
  plus verified interpreter creation identity, independently of executable name.
  Regressions cover renamed Waveguide Generator.exe/pythonw and wg-python.exe
  names for both layouts, invalid links and a real separate-process wrapper.
- **P2/D — fixed:** failed startup stops the verified owned Windows interpreter
  as well as the Popen child. Open one Windows process handle, compare its
  creation time with the verified bootstrap identity, then terminate/wait on
  that same handle to avoid a PID reuse race. Unverified interpreters are never
  terminated. Popen wait TimeoutExpired escalates to kill and bounded reaping;
  record cleanup still runs and teardown errors cannot mask the startup error.
  Regressions cover both owned/unowned bootstrap, stub timeout, original-error
  preservation, scoped record cleanup and changed/unavailable creation identity.
- **P3 accept activity — fixed:** refresh the idle clock on every accept.
  A successful hello now gets a fresh admission window. **PR 20's manager must
  keep an admitted connection open** to hold the host alive; hello alone remains
  a short handshake, not a lifetime lease.
- **P3 absolute paths — fixed:** solver_script, julia_executable and non-null
  julia_project/julia_sysimage must be absolute. Empty optional path strings
  are refused; explicit null remains supported. Each field has a regression.
- **P3 empty interpreter — fixed:** report a clear sys.executable-is-empty error
  before Popen, with a no-child/no-record regression.
- **P3 host logs — fixed:** serving, idle exit, startup/connection/submission and
  retirement failures go to the private key log. Truncate at each new start;
  O_APPEND in the existing private-file helper prevents redirected child output
  and host diagnostics overwriting one another. Lifecycle/log regressions cover
  truncation, serving, idle exit, refusal and constructor failure.
- **P3 accept errors — fixed:** log non-timeout OSError and retry; five consecutive
  errors stop admission with a bounded 20 ms backoff. Successful accepts reset
  the budget. Regressions cover recovery and permanent accept failure.
- **P3 unauthenticated capacity — fixed:** eight separate pending slots with a
  0.5 s total pre-auth deadline; evict the oldest pending peer on overflow so
  silent peers cannot occupy all 32 authenticated slots. Enforce the authenticated
  cap at proof admission. Regressions cover a 32-silent-peer flood, hello without
  client proof expiry, and authenticated-cap refusal.
- **P3 shutdown replay note — fixed (note only):** replay of a captured shutdown request is
  DoS-only by a local sniffer with access to local IPC; it grants no solve access
  or token disclosure. No protocol change is requested in this round.
- **P3 macOS ps note — fixed (note only):** ps timeout under load yields None for start
  identity and therefore fails closed. The current registry bound is 0.5 s;
  consider 2 s during platform qualification. No identity policy change here.

Review-round deviations: official JWSound/BEAT_Engine remains the target. The
private-file helper gains an optional append flag solely to safely share logs
with redirected engine output; existing record/spec semantics stay unchanged.
The pending budget is separate from authenticated capacity, with oldest-peer
replacement allowing legitimate admission during silent-peer saturation.
Windows termination applies only to this launch's verified bootstrap interpreter,
not any process obtained solely from a stale registry record. Native Windows
kernel/Job Object and real Julia/installed qualification remain later gates.
No pins, requirements, existing callers, engine checkout or HBB directories change.
Changes remain uncommitted as explicitly requested.

PR 18 review-round validation: **608 passed in 57.69 s** with the requested
Python and `scripts/run_tests.py server/tests/beat_runtime
server/tests/test_solver_beat.py -q -p no:cacheprovider`, including all PR 19
cases and 30 new regressions (16 host review, 10 spawn review, 4 platform).
Ruff on runtime sources/tests and `git diff --check` passed. Both exact combined
commands were attempted: pytest refuses the absent `server/tests/beat_adapter`
directory (no tests ran); Ruff reports the absent `server/solver/beat_adapter`
and `server/tests/beat_adapter` directories. No Julia, downloads, full WG suite,
real user/HBB data directories or donor-checkout writes were used. Native
Windows kernel/Job Object qualification remains unrun; Windows branches use
fakes, with the distinct launcher/host process path also exercised on POSIX.
All changes remain uncommitted as requested.

**Review round 1 fixes (PR 19) and PR 18 fix regression:**

Files follow the existing review slices; this round fixes the host/client runtime
without switching production callers:
- **18c spawn/recovery:** `server/solver/beat_runtime/{spawn,cleanup}.py`;
  probe/pruning/deadline regressions in
  `server/tests/beat_runtime/test_submission_review.py`; pruning-mode assertion
  in `test_spawn_review.py`; this `CHANGES.md`.
- **19a/19d submission/retirement:** `server/solver/beat_runtime/host.py`;
  startup, request-input, cancel, reader and authentication regressions in
  `test_submission_review.py`; `fake_host_worker.py`'s post-start status and
  unexpected close error fixtures; this `CHANGES.md`.
- **19b/19c client/adoption:** `server/solver/beat_runtime/client.py`;
  delayed retirement, inline-size and replacement-report regressions in
  `test_submission_review.py`; this `CHANGES.md`.
- **18d/19 test boundary:** `server/tests/beat_runtime/conftest.py` and
  `test_host_review.py` (native fixture paths, revised optional-path expectations
  and authentication-expiry allowance); this `CHANGES.md`.

- **P2/A — fixed:** start_host uses cleanup_host(prune_only=True), including
  failed bootstrap cleanup. Recovery never sends authenticated shutdown.
  Cleanup's existing dead-PID/start-mismatch and refused-endpoint policy still
  controls pruning; authenticatable, live and uncertain hosts remain. Retry
  transient failures with increasing probe deadlines inside the original start
  deadline, including bare remaining_time TimeoutError. Regressions prove a
  slow healthy host continues another client's solve without shutdown, dead and
  reused hosts are pruned, even an authenticatable host with a gone-PID hint is
  retained, connection resets/connect timeouts retry, and a deadline race does
  not leak a bare TimeoutError. The closing-host test now models an endpoint
  refusing admission until exit; SIGTERM delivery can race successful
  authentication and is not proof that the listener already stopped.
- **P2/B — fixed:** cancellation without a stream marks the job cancelled and
  returns immediately. The FIFO runner retains ownership through startup and
  closes a returned stream before admitting the next job. Disconnected status
  callbacks cannot abort shared startup. Regressions hold fake startup beyond
  five seconds for both submit and ensure_started, with both disconnect and
  explicit cancel; the queued successor completes on the same host PID.
- **P2/C — fixed:** validate file readability, JSON and object shape at admission.
  Missing/unreadable/changed-file JSON failures from public submit also fail only
  that job, with a clear request-file error. Engine/stream retirement failures
  still stop admission. Regressions cover missing, unreadable, malformed and
  nonobject files plus file-read failures racing admission, followed by a
  successful submission on the same host PID. WG preserves file transport and
  delegates engine contract/operation negotiation to official EngineWorker.
- **P2/D — fixed:** client terminate waits RETIREMENT_TIMEOUT + CONTROL_TIMEOUT
  (seven seconds), matching the host's five-second retirement plus reply margin.
  A three-second successful fake termination keeps the lease and host PID.
- **P2/E — fixed:** fixture launch paths derive from tmp_path and are native
  absolute paths on every platform. A regression checks all four paths.
- **P3 client cancel — fixed:** unexpected close exceptions during explicit
  cancellation are logged and followed by bounded public engine.terminate while
  the FIFO ownership slot is still held. Successful retirement serves the next
  client on the same host; genuine termination failure still stops admission.
  The regression raises LookupError during close and verifies termination
  precedes the queued successor. Disconnect retirement-failure tests stay intact.
- **P3 stream writes — fixed:** stream admission explicitly sets a bounded
  ten-second send timeout. A socket with a small send buffer and a reader stalled
  beyond the control deadline still receives its complete result and terminal.
- **P3 authentication deadline — fixed:** hello retains the 0.5-second pre-auth
  window; authenticate gets a fresh one-second deadline after the hello reply.
  A regression spends most of the hello budget and delays proof past that
  original deadline, then authenticates successfully. Pending capacity remains
  bounded separately from authenticated clients.
- **P3 oversized inline request — fixed:** serialize/check the complete submit
  envelope against the 1 MiB control ceiling before adoption/connection, raising
  HostError with file-staging guidance. A regression proves no host is contacted.
- **P3 explicit Quit — fixed (documentation):** HostedWorker.shutdown() is the
  explicit-quit path: detach local admission and streams, then shut down the
  authenticated shared host. HostedWorker.detach() releases only this client;
  terminate() requests idle engine retirement and declines queued/active work.
- **P3 retirement overlap — fixed (documentation):** unpublication precedes
  engine retirement so a replacement host can become available while the old
  engine is still terminating. Brief engine overlap is possible during normal
  retirement. Host exit bounds the wait for public retirement methods; it does
  not prove an unresponsive old Julia child has died.
- **P3 replacement report — fixed:** compare prior host_pid in _connect and
  prior worker_instance/host_pid in _report. Expose sticky engine_replaced on
  HostedWorker and in adopt()'s returned report; first adoption is False and a
  later respawn is True. Clear stale negotiation metadata on host PID change.
  Regressions cover real fake-host respawn and a changed worker_instance report.
  This reports lost host-owned worker continuity, not a measured Julia PID.
- **P3 empty optional paths — fixed:** validate_key returns a normalized copy;
  empty julia_project/julia_sysimage strings become None before hashing,
  publication, adoption and engine construction. Regressions prove each field
  reaches the engine as None and shares the explicit-null host key.

Design deviations: official JWSound/BEAT_Engine remains the target. The key
validator now returns a normalized specification; runtime constructors/spawn use
that copy without mutating caller input. prune_only is an additive cleanup mode;
explicit shutdown's authenticated policy is unchanged. Engine PID continuity,
native Windows Job Objects/ACLs and real Julia/installed qualification remain
later gates. No engine-private fields or WG-specific engine behavior are assumed.
No Julia, downloads, real user/HBB directories, pins, requirements or existing
production callers are changed. Changes remain uncommitted as requested.

Review-round validation: **634 passed in 77.06 s** using the requested Python
with `scripts/run_tests.py server/tests/beat_runtime
server/tests/test_solver_beat.py -q -p no:cacheprovider`. This includes 28 new
mechanism regressions; two old empty-optional-path refusal cases are replaced
by normalization/reuse regressions. The focused spawn-review run passed
**10 tests in 2.61 s** after making its closing-endpoint fixture deterministic.
Ruff on runtime sources/tests and `git diff --check` passed. Both exact requested
combined commands were attempted: pytest ran no tests because
`server/tests/beat_adapter` is absent; Ruff reports the absent
`server/solver/beat_adapter` and `server/tests/beat_adapter` directories.
All test/check runs stayed under two minutes. No Julia, downloads or full WG
suite ran. Native Windows/installed-engine qualification was not run; tests
use fake engines, temporary directories and only recorded test-owned processes.
No requested fix remains unfinished; the missing adapter directories prevent
completion of the exact combined checks. Changes remain uncommitted.
- **PR 20a — manager identity/cache:** `server/solver/beat_runtime/manager.py`
  (`resolve_key`, `WorkerManager`, default manager);
  identity/cache/lifecycle tests in `server/tests/beat_runtime/test_manager.py`;
  this `CHANGES.md`.
  Resolve CPU/Metal assets and executable through WG discovery; hash executable,
  selected solver bytes, engine/project/sysimage and WG runtime/compiled-adapter
  policy. Resolve AUTO once through `threads.py`, and put that integer into both
  the key and `JULIA_NUM_THREADS`. Snapshot the effective environment, retaining
  caller Julia/BLAB choices and defaulting the depot into `paths.runtime_dir()`.
  Cache by the existing provider/protocol-scoped registry key. Host mode is the
  default; child mode and its injectable public engine factory are explicit.
- **PR 20b — manager admission/retirement:** `manager.py` (`ManagedWorker`,
  `WorkerLease`); host adoption/Quit tests in `test_manager.py` and ownership,
  startup, abandonment, failure and waiter tests in
  `server/tests/beat_runtime/test_session.py`; this `CHANGES.md`.
  Retain a local lease across startup, contract negotiation, public submission
  and cleanup, with `StreamOwnership` protecting the closeable event stream.
  Stale close/cancel never retires a successor. Child cancellation uses bounded
  public termination before releasing ownership, including a startup that
  returns after cancellation. Hosted cancellation closes only the owned stream
  or disconnects this client's startup lease; it never signals registry PIDs.
  Shutdown closes every cached client's admission before releasing any, and
  continues through teardown errors. Quit detaches idle hosts and terminates
  child mode; active abandoned streams are retired. Failed retirement condemns
  a client instead of allowing its next session to reuse it.
- **PR 20c — solve sessions:** `server/solver/beat_runtime/session.py`;
  staging/cancellation/backstop/partial-result tests in `test_session.py`;
  this `CHANGES.md`.
  Context-managed staging uses `dir=temporary_directory_root()`, under the
  process's swept WG session. Serialize before taking a worker lease. Own the
  request/cancel paths, a 50 ms cancellation monitor and stream cleanup. Write
  a cooperative marker first, then cancel the owned lease after a configurable
  250 ms grace period. Marker failure cannot disable the backstop. Preserve
  already emitted results and synthesize a cancelled terminal when the backstop
  interrupts a read. Startup/empty-result cancellation retains the callback's
  original exception. Closure stops/joins the monitor, releases the lease and
  removes staging even when another cleanup step fails.
- **PR 20d — managed prototype:** `server/solver/official_beat.py`;
  `server/tests/test_official_beat_bridge.py`; this `CHANGES.md`.
  The unregistered, opt-in prototype uses the manager and SolveSession instead
  of constructing/terminating an EngineWorker for each solve. Repeated solves
  and adaptive batches share one manager/client/key/thread policy; streams and
  request files still close after every batch. Negotiation precedes submission.
  Cancelled responses retain completed frequency rows and cancellation metadata.
  Tests inject an explicitly selected child WorkerManager, replacing the old
  one-worker factory seam, and isolate all staging in tmp_path.

Future callers use `get_manager().get_worker(backend, julia_executable=...,
julia_threads="auto"|positive_integer, julia_project=..., julia_sysimage=...,
solver_script=..., environment=...)`. These return a `ManagedWorker`; all solve
admission goes through `SolveSession.submit(client, payload, negotiate=...)`,
inside `with SolveSession(cancellation_callback=...) as session`. Stage mesh
files beneath `session.directory`; use `session.cancel_path` in compiled
requests and consume `session.events()`. `request_cancel()` permits orderly
partial-result cancellation. A terminal completes the stream; the enclosing
session releases the outer lease. Always close the context, even before the
first read. `WorkerManager.shutdown()` ends admission and releases all clients;
`detach()` implements Quit. Warm-up can use the same manager and session API;
app/warm-up hook wiring remains design PR 21.

Deviations and limits: official JWSound/BEAT_Engine replaces the design's fork;
only public EngineWorker methods and worker_info are used. No PID continuity
claim is added: the official public API still lacks an engine PID. The host's
existing worker_instance proves owned EngineWorker continuity in fake tests.
The key conservatively includes the complete effective environment, rather
than only Julia/BLAB variables, so inherited library configuration cannot differ
between key and launch; unrelated environment changes can reduce adoption reuse.
The selected solver's bytes and prototype compiled-policy bytes are also keyed.
Four review slices keep identity, ownership, sessions and adapter integration
separately reviewable. They are subdivisions of design PR 20, not production
routing, dependency/pin or launcher changes. Explicit child mode never becomes
an automatic fallback after a host authentication refusal. Host registry roots
stay outside swept staging. No Julia, downloads, CUDA/ROCm, HBB directory writes,
legacy state mirror or unauthenticated stale-PID signalling is introduced.
Changes remain uncommitted as requested.

PR 20 validation: **624 passed in 53.76 s** with the requested Python and
`scripts/run_tests.py server/tests/beat_runtime server/tests/test_solver_beat.py
-q -p no:cacheprovider`, including 16 new manager/session tests. The requested
additional command, `-m pytest -q -p no:cacheprovider
server/tests/test_temp_session.py server/tests/test_official_beat*.py`, passed
**69 tests with 1 skipped in 2.84 s**; the skip is the existing optional installed
beat-engine contract check, because that package is absent. Runtime/test and
prototype source/test Ruff passed, as did `git diff --check`. Both exact combined
checks were attempted: pytest runs no tests because `server/tests/beat_adapter`
is absent; Ruff reports absent `server/solver/beat_adapter` and
`server/tests/beat_adapter`. No requested PR 20 implementation remains unfinished.
Real Julia/installed and Windows Job Object qualification, and app/warm-up hook
integration, remain the later design gates; fake host subprocesses were used
only in the fast host-mode tests. All changes remain uncommitted as requested.

**Review round 1 fixes (PR 20)**

Files per existing review slice (shared files support more than one slice):
- **20a identity/cache:** `server/solver/beat_runtime/{manager,identity,spawn,client}.py`;
  `server/tests/beat_runtime/test_manager_review.py`; this `CHANGES.md`.
- **20b admission/retirement:** `server/solver/beat_runtime/{manager,host,client}.py`;
  `server/tests/beat_runtime/{test_manager_review,test_spawn_platform,fake_host_worker}.py`;
  this `CHANGES.md`. Host/client corrections also follow up design PR 19.
- **20c sessions:** `server/solver/beat_runtime/session.py`;
  `server/tests/beat_runtime/test_session.py`; this `CHANGES.md`.
- **20d prototype/adaptive:** `server/solver/{official_beat,adaptive_sweep}.py`;
  `server/tests/test_{official_beat_bridge,adaptive_sweep}.py`; this `CHANGES.md`.

- **P1/A — fixed:** adoption keys and all launch/host records retain only
  `JULIA_*`, `BLAB_*`, normalized absolute PATH entries, explicit absolute depot
  entries and resolved thread/path/content identities. Snapshot ambient launch
  settings separately, pass them through HostedWorker/start_host/Popen in memory,
  and merge the host's inherited environment with keyed engine settings. Ambient
  variables never enter key hashing or disk records. Named regressions cover
  unrelated launch changes, Julia/BLAB invalidation, unkeyed secret forwarding
  and record exclusion. Fake-engine logs now exclude ambient secrets too; the
  packaged-root test checks keyed values within the inherited launch environment.
- **P1/B — fixed:** every explicit depot entry (including inherited lists and
  symlinks), the selected project and an inherited JULIA_PROJECT pass
  `paths.checked_root` before any worker launch. Resolve entries absolutely and
  eliminate implicit empty depot expansions; an empty-only list selects the WG
  depot. Named regressions cover inherited mixed lists, projects and linked HBB
  destinations without creating anything in them. `provision.py` is absent on
  this branch; deduplicate these destination checks with its provision-branch
  helper when merging commit 194af3ca.
- **P2/C — fixed:** manager failed terminals retire hosted as well as child
  engines before releasing the outer solve lease. The host additionally retires
  an engine's failed terminal under its FIFO slot, protecting other remote
  clients; retirement failure stops admission. A hosted failure/successor
  regression proves a fresh engine startup precedes reuse on the same host.
- **P2/D — fixed:** manager cancellation sends a connection-scoped cancel with
  `retire_startup=True`, rather than only disconnecting ensure_started. Serialize
  startup-send/cancel ordering and wait for its bounded completion before
  condemning the client. Remote stream abandonment uses the same request for a
  submit that is still starting. The host retains FIFO ownership through public
  termination and late-start retirement, with one five-second budget; startup
  that will not return stops host admission so a successor can respawn. Existing
  PR 19 plain-cancel/disconnect semantics remain intact. Named slow-start and
  non-returning-start regressions prove successor completion, including replacement
  of the latter host. Admission EOF during closing-host handoff retries within
  the original start budget; wrong authentication proofs still fail closed.
- **P2/E — fixed:** a genuine zero-result cancelled terminal re-raises the
  cancellation monitor's recorded callback exception. Startup transport
  interruption after request_cancel also raises SessionCancelled. Session and
  prototype regressions assert the exact original callback exception object.
- **P2/F — fixed:** adaptive sweeps return acquired rows on cancelled shortened
  batches before full-grid validation or another cancellation callback. Preserve
  prior batches' complex/auxiliary rows and recompute SPL on their sorted actual
  grid. Prototype adaptive response metadata includes cancelled=True. Named
  regressions cover shortened initial batches and later cancellation after
  earlier acquisitions; normal malformed-grid validation is unchanged.
- **P2/G — fixed:** a waiter on a condemned managed client transparently calls
  its owning manager's get_worker once and acquires the fresh client. Negotiation
  uses that lease's worker_info. Actual manager shutdown still refuses admission.
  Named regressions cover hosted startup cancellation, a cancellation retirement
  error, fresh negotiation metadata and permanently closed manager admission.
- **P2/H — fixed:** resolve identities and construct/close clients outside the
  global manager lock. Pop condemned/content-stale clients under the lock before
  attempting teardown, and proceed with replacement even after old cleanup
  errors. Named blocked-resolution and blocked/raising-close regressions prove
  Quit completes without waiting and no unusable client remains cached.
- **P3 Windows staging cleanup — fixed:** TemporaryDirectory enables
  ignore_cleanup_errors (including its permission retry path), and a remaining
  open-file OSError defers removal to the swept application session root.
  A simulated Windows open-surface failure cannot mask a cancelled result.
- **P3 fingerprints/cache eviction — fixed:** resolve_key opts into bounded
  per-file digest caching by resolved (path, mtime_ns, size); tree enumeration
  and required-input stat checks still run each time. Identity APIs retain
  uncached defaults for other callers. Evict/retire a cached client when content
  identities change at the same configuration. The named regression counts
  actual reads and proves changed bytes invalidate the key and remove the client.
- **P3 compiled-request policy — fixed:** resolve_key/get_worker accept
  compiled_request_policy, documented with official_beat.py as the default.
  Selected-policy edits invalidate runtime identity in a named regression.
- **P3 optional runtime fallback — fixed:** the prototype maps
  JuliaDiscoveryError and AssetsUnavailable from initial worker lookup or waiter
  replacement to OfficialBeatUnavailable; both have named fallback regressions.
- **P3 default-manager lifetime — fixed:** document that shutdown/detach close
  the process default for good. PR 21 must instantiate a new manager for a new
  application lifetime (or explicitly implement reopening). Regressions cover
  both operations; get_manager does not silently recreate a closed manager.
- **P3 from PR 19 round-2, executable errors — fixed:** perform public
  ensure_started before submit's request preparation, separating cold executable
  FileNotFoundError/PermissionError from request-file failures. Deliver a clear
  Julia executable failure to the admitted client before stopping admission;
  request-file failures retain PR 19's recoverable behavior. Named missing and
  non-executable Julia regressions assert the failure frame and fail-stop state.

Design deviations: official JWSound/BEAT_Engine remains the target, with only
public worker methods. The small host/client extension makes startup retirement
explicit and bounded without changing PR 19's plain-cancel path or signalling
any registry PID. Empty depot expansions become explicit WG-owned destinations
so Julia cannot silently open an inherited HBB/default writable depot. The
provision-branch helper must be deduplicated on merge, as noted above. These are
additive review fixes: official_beat remains unregistered and OFF by default;
no existing callers, pins, requirements, CUDA/ROCm or donor checkouts change.
Changes remain uncommitted as requested.

Review-round validation: **673 passed in 101.94 s** with the requested Python
and `scripts/run_tests.py server/tests/beat_runtime
server/tests/test_solver_beat.py -q -p no:cacheprovider`. The additional focused
prototype/adaptive check, `scripts/run_tests.py
server/tests/test_official_beat_bridge.py server/tests/test_adaptive_sweep.py
-q -p no:cacheprovider`, passed **69 tests with 1 skipped in 3.09 s**. The skip
is the existing optional installed beat-engine contract check; the package is
absent as required. This round adds 29 mechanism regression cases (21 manager/
host review, 2 session, 5 prototype and 1 adaptive). Ruff on runtime sources/tests
plus both changed adapter/prototype sources and tests passed. `git diff --check`
passed. The exact requested pytest command ran no tests because
`server/tests/beat_adapter` is absent; the exact Ruff command reports absent
`server/solver/beat_adapter` and `server/tests/beat_adapter`. Both were attempted.
Every test/check invocation stayed below two minutes; no Julia, downloads or
full WG suite ran. Tests use fake engines, tmp_path staging/records and only
recorded test-owned processes. Native Windows open-file behavior is simulated;
real engine/platform/installed qualification remains a later design gate. No
requested fix is unfinished; only the missing adapter directories prevent the
exact combined checks. All changes remain uncommitted as explicitly requested.

PR 20 review round 2, fixed directly on the merged branch:
- PATH is no longer keyed (it is passed to the host unkeyed); the Julia
  executable is keyed by path and content, and keying PATH broke adoption
  across launches from different shells.
- resolve_key uses julia_steps.julia_environment, the same project and depot
  destination checks as provisioning (empty depot entries refused, relative
  entries absolute, HBB refused); the duplicate manager-side check is gone.
- JULIA_PROJECT named environments ("@.", "@v1.12") are kept as names.
- Accepted P3s: a cancelled adaptive sweep's partial result carries acquisition
  rows (including coverage frequencies), the last batch's solver_log/timings,
  and no frequency_status; the transparent retry reuses the first creation's
  environment snapshot.

- **PR 13a — readiness identity/verdicts:**
  `server/solver/beat_runtime/readiness.py` (identity and status APIs);
  `server/tests/beat_runtime/test_readiness.py` (identity/evidence/backend cases);
  this `CHANGES.md`.
  CPU and Metal verdicts match the current project, engine/runtime bytes,
  executable/version, resolved depot and effective JULIA_*/BLAB_* environment,
  resolved integer threads and compiled probe contract/fixture. Missing,
  corrupt, foreign, failed, interrupted and stale records have independent
  reasons. Hardware eligibility and the engine's static catalog never prove
  usability. CUDA/ROCm always report `not supported in this build`; a failed
  Metal setup leaves CPU state and readiness intact. Default managed Julia
  upgrades invalidate readiness, while explicit/configured older selections
  remain valid. Unsupported/custom sysimage evidence cannot match the default
  launch's null sysimage fields.
- **PR 13b — compiled CPU probe/reuse handoff:**
  `server/solver/beat_runtime/readiness.py` (provisioning entry points);
  `server/solver/beat_runtime/provision.py`;
  `server/tests/beat_runtime/test_readiness.py` (CPU wiring/reproof/notification cases);
  this `CHANGES.md`.
  Wire PR 11's default CPU probe through lazy public EngineWorker construction,
  passing the same resolved threads/project/environment as setup. Retire only
  that owned worker. Retain numerical result/terminal evidence in the PR 10
  completion envelope. Built-in CPU/Metal reuse now requires that complete
  evidence; an incomplete old built-in record is re-proved without force.
  Custom CPU probes use a custom: contract namespace, so they cannot supply
  default runtime readiness. The low-level shared provisioner remains injectable.
- **PR 13c — default-off facade and live registry integration:**
  `server/solver/beat_runtime/provider.py`, readiness.py's listener APIs;
  `server/solver/{beat_cpu_runtime,beat}.py`; `server/engines/registry.py`;
  `server/tests/beat_runtime/test_readiness_facade.py`; this `CHANGES.md`.
  `WG2_BEAT_PROVIDER=official` selects WG-owned readiness/preparation; unset,
  hbb or any other value retains the existing HBB paths. CPU then Metal setup
  uses the existing daemon/progress lifecycle, skip flags and once-per-build
  failure gate. CPU/Metal progress and terminal changes refresh EngineRegistry,
  even when HBB is absent. Registry listener removal also removes the new
  provider subscription. Guard listener exceptions and call outside locks.
  Presentation wrappers bypass HBB success caches under the selector. Solve
  imports and production routes remain HBB; this selector prepares a provider
  for later adapter/qualification slices, and does not switch numerical solves.

Future callers use readiness.backend_readiness(backend, directory=None,
**launch_options) -> BackendReadiness(ready, state, reason), backend_status,
beat_backend_statuses and beat_engine_status. launch_options include environ,
julia_executable, julia_project, julia_threads and depot. provision_cpu and
provision_metal return backend records and publish completion invalidation.
probe_cache_clear(notify=True) publishes manual invalidation; notify=False
avoids recursive refresh from presentation wrappers. Queries are deliberately
uncached and re-read records/hash current bytes, including external-process
state changes when queried. No Julia startup is needed for a readiness query.
Only in-process provisioning/invalidation publishes listener events; cross-process
notification delivery is not introduced here. Runtime capabilities beyond the
compiled readiness proof remain negotiated by later adapters; status version
is unknown and surface_traces is conservatively false.

- **PR 14 — CLI and optional bootstrap path:**
  `server/solver/beat_runtime/cli.py`; `scripts/bootstrap.py`;
  `server/tests/beat_runtime/test_cli.py`;
  `scripts/tests/test_bootstrap_official_beat.py`; this `CHANGES.md`.
  `python -m server.solver.beat_runtime.cli [provision|status|clear-cache]`
  supports the existing --backend cpu/auto/metal, --if-gpu,
  --if-nvidia-gpu, --force and --dir invocation shapes, plus explicit --retry,
  --julia, --project, --threads and --depot. Auto is Metal-only and never
  provisions CPU implicitly. GPU-less/unsupported gates exit 0 quietly;
  successful/skipped provisioning exits 0, failed/absent engine exits 1,
  invalid flags exit 2. CPU plus a GPU gate is refused. Status emits JSON and
  exits 0 even when unavailable; clear-cache publishes in-process invalidation,
  neither deleting proof records nor engine numerical caches. Absent engine
  provisioning does not create state or attempt an installation. Bootstrap
  selects this command only under the same explicit provider selector; its
  existing REPO_ROOT subprocess cwd makes the app module importable, tested
  from an unrelated caller cwd with a deliberately absent engine. Existing
  distribution validation and installed qualifier HBB invocations remain intact.

PR 13/14 deviations: official JWSound/BEAT_Engine supersedes the design's fork.
PR 13 uses three review slices to stay near the proposed review-size limit.
An uncached readiness query replaces a persistent success cache, so file/record
changes cannot retain stale evidence; explicit invalidation still notifies the
registry. Default CPU probe wiring completes the earlier injectable PR 10/11
handoff here. The small registry change is needed to refresh without requiring
an HBB package object. Bootstrap keeps its existing Windows/Linux CPU policy;
the application's alternative background facade prepares CPU on macOS too.
No cached-bundle/startup claim is inferred from a compiled solve. No engine
private API, WG-specific engine behavior, new dependency, pin/requirements
change, CUDA/ROCm implementation, legacy mirror or unauthenticated PID signal
is introduced. All source/reference checkouts and HBB/user data remain untouched;
changes remain uncommitted as requested.

PR 13/14 validation: available runtime plus existing solver tests passed
**921 tests in 95.87 s** with the requested Python and scripts/run_tests.py.
The final focused readiness/facade/CLI/bootstrap run passed **80 tests in
2.10 s** (53 PR 13 cases, 27 PR 14 cases), including the additional Metal
inventory-error isolation regression. The unchanged CPU/runtime/startup and
scripts/tests/test_bootstrap*.py command passed **157 tests in 5.73 s**.
The startup command initially had 137 passes and 20 failures because this
worktree lacks frontend/dist. Its successful reruns used a temporary symlink
to the existing main WG checkout's built frontend, read only, then removed it;
no frontend build or qualification is claimed. Available runtime/test and
changed-facade/bootstrap Ruff checks and git diff --check passed.

Both exact common checks were attempted: combined pytest ran no tests because
server/tests/beat_adapter is absent; combined Ruff reports only the absent
server/solver/beat_adapter and server/tests/beat_adapter directories. These
missing later-slice paths prevent completing the exact common commands. All
runs were unpiped and below two minutes. No Julia, real download, full WG suite,
real GPU solve, user/HBB data writes or reference-checkout mutation occurred.
Real engine/bundle/installed-device qualification remains intentionally unrun
under the task constraints. No requested implementation remains unfinished;
changes and this handoff remain uncommitted as explicitly requested.

**Review round 1 fixes (PRs 13-14):**
- **P1/A — fixed:** default-off imports/subscriptions/cache clearing are gated
  by the stdlib-only provider selector. The previously transitive threads import
  is gated too: beat_threads owns the shared stdlib core detector, and the
  official resolver imports that detector. A fresh-interpreter regression imports
  bootstrap, beat_cpu_runtime, beat and EngineRegistry, constructs the registry,
  clears caches and removes its listener; only provider loads below the inert
  beat_runtime package. Official events use a guarded bridge and cannot reach
  registry subscribers with the selector unset.
- **P1/B — fixed:** production beat_status/beat_backend_statuses and CPU registry
  readiness remain attached to HBB, including their remediation commands.
  Official readiness is published separately as EngineRegistry's
  official_runtime_statuses and diagnostics' officialBeatRuntime field. Official
  proof with absent HBB cannot enable beat-cpu/beat-metal; ready HBB with stale
  official proof keeps its production availability. This supersedes PR 13c's
  earlier presentation-wrapper/registry behavior; numerical adapters stay HBB.
- **P1/C — fixed:** raw CPU/Metal/legacy GPU stdout updates progress text and
  logging only. Guarded step_cb callbacks from shared provisioning publish
  instantiate/precompile/setup/probe transitions; start/finish publish lifecycle
  changes. In-flight official diagnostics use the cheap facade text and do not
  call readiness APIs. Settled snapshots compute CPU once. The 1,000-line burst
  regression produces three notifications and one CPU identity computation.
- **P2/D — fixed:** engine_fingerprint includes shared engine sources but only
  the selected backend's bundled project/manifest set (including versioned and
  bundle manifests), plus any explicitly selected project/sysimage. Provisioning
  and readiness pass the same backend. A Metal instantiate changes its manifest
  and fails; the CPU record and CPU readiness survive. Shared-source edits still
  revoke every backend's proof.
- **P2/E — fixed:** each capabilities request checks a cheap root + mtime_ns/size
  stamp for state-cpu.json, state-metal.json and julia.json. Changed stamps
  coalesce through the existing refresh revisions and refresh official diagnostics.
  CLI clear-cache touches existing records to publish cross-process invalidation;
  it neither deletes proof nor creates an absent runtime. Separate Python
  processes exercise terminal-state writes and clear-cache, while unchanged
  stamps cause no additional identity work. Production HBB rows stay independent.
- **P2/F — fixed:** the official remediation command names the current interpreter
  and uses -c/runpy with app_root inserted in sys.path, independent of cwd and
  compatible with WG2_APP_ROOT's packaged source layer. POSIX uses shlex.join;
  Windows uses list2cmdline. Tests cover spaces/quotes and an actual invocation
  from an unrelated cwd with a deliberately missing optional engine.
- **P3 GPU-only thread — fixed:** prepare_gpu requires reported eligible Metal
  hardware. A settled CPU on Linux/Windows with no Metal starts no thread and
  emits no notification. CPU provisioning still runs when needed, preserving
  the design's required CPU-only-host setup. Hardware detection failures skip
  GPU work without breaking startup.
- **P3 unsupported CLI — fixed:** explicit cuda/rocm without a GPU gate exits 1
  with "not supported in this build"; --if-gpu remains a quiet no-op.
- **P3 active provisioning — fixed:** provisioning_active checks the existing
  persistent kernel lock without creating files or trusting/signaling a holder
  PID. An in-progress record under a held lock reports provisioning before
  identity hashing; after release it can report interrupted. A separate-process
  lock regression exercises both readiness and cpu_runtime_readiness.
- **P3 selector — fixed:** accept exactly stripped "official"; warn once per
  distinct unknown non-empty value. Every application selection site reads the
  process environment, including launcher selection; injected provisioning env
  remains launch configuration and cannot select a different reporting provider.

Review-round files per PR (relative to repository root):
- **PR 13:** server/solver/beat_runtime/{provider,readiness,identity,locks,provision,
  threads}.py; server/solver/{beat_cpu_runtime,beat,beat_threads}.py;
  server/engines/registry.py; server/diagnostics/capabilities.py;
  server/tests/beat_runtime/test_{identity,threads,readiness_facade,review_round1}.py;
  this CHANGES.md. Shared step_cb is optional; stdout callbacks retain their API.
- **PR 14:** server/solver/beat_runtime/cli.py;
  server/tests/beat_runtime/test_cli.py; command/quoting changes in the shared
  beat_cpu_runtime.py and CLI/command cases in test_review_round1.py; this
  CHANGES.md. scripts/bootstrap.py needs no further edit: its provider helper
  now enforces the same exact selector and its subprocess cwd already works.

Review-round validation: the available common targets (beat_runtime plus
server/tests/test_solver_beat.py) passed **937 tests in 82.39 s**. The requested
CPU runtime, test_engines*.py and three bootstrap files passed **196 tests,
1 skipped in 4.62 s**. Focused facade/review regressions passed **26 tests in
2.31 s**; the final mechanism-only rerun (adding an assertion through the CPU
facade to the lock regression) passed **17 tests**. Tests used the requested
Python via scripts/run_tests.py, unpiped, each run below two minutes. Ruff passes
for all available runtime/tests and changed facade/registry/diagnostic/bootstrap
paths; git diff --check passes.

Both exact common commands were attempted. Pytest cannot collect because
server/tests/beat_adapter is absent; Ruff reports only absent
server/solver/beat_adapter and server/tests/beat_adapter. These later-slice
paths are not created by this review fix. test_startup_performance.py and the
full scripts/WG suites were not run. No Julia, downloads, real user/HBB data
writes, donor-checkout edits, pin/requirements changes, or commits occurred.

Design deviations: official JWSound/BEAT_Engine remains the target; backend-local
manifest identities supersede the design's all-backend hash to preserve CPU
proof during Metal setup. Official status is diagnostic until later solve
adapter slices; cross-process invalidation uses existing file stamps rather
than a watcher/thread or new dependency. All requested fixes are implemented;
missing beat_adapter directories prevent only the exact combined checks.
- **PR 21a — selected-provider warm-up:**
  `server/solver/beat_runtime/{provider,warmup}.py`;
  `server/solver/warmup.py`; warm-up/reuse/mode tests in
  `server/tests/beat_runtime/test_warmup.py` and selector tests in
  `server/tests/test_startup_performance.py`; compiled-engine support in
  `server/tests/beat_runtime/fake_host_worker.py`; this `CHANGES.md`.
  Only exact `WG2_BEAT_PROVIDER=official` enables the new hooks. Missing,
  legacy and differently cased values preserve the existing HBB tiny warm-up
  arguments and shutdown call. Official `off/worker/tiny` uses the process
  default manager (or an explicit injected manager), with identical backend,
  default key options and resolved integer thread policy to the prototype solve.
  Tiny warm-up adapts the existing compiled numerical probe to SolveSession;
  staged paths, negotiation metadata, cancellation and stream cleanup stay WG
  owned. No readiness module or engine-specific application behavior is needed.
  CPU/Metal fake-host tests prove warm-up and a following managed submission
  retain the same host, worker instance and one engine-start event.
- **PR 21b — final Quit and interruptible detach:** `server/app.py`;
  `server/solver/beat_runtime/{client,ipc}.py`; Quit/timeout tests in
  `server/tests/{test_startup_performance,beat_runtime/test_warmup}.py`;
  cancellation framing tests in `server/tests/beat_runtime/test_ipc.py`;
  the same compiled fake engine support; this `CHANGES.md`.
  Cancel/await the prewarm task within the existing five-second deadline,
  then run final manager.detach on the existing daemon cleanup thread. Idle
  hosted workers retain their independent host idle timeout; active sessions
  retire their engine, and child mode terminates. Admission stays closed after
  Quit, including cached clients. Completed warm-up and ordinary solve-session
  cleanup never close the manager. WG's normal new lifetime is a fresh server
  process; explicit in-process callers must construct a fresh WorkerManager,
  as in PR 20. The process default is never silently reopened.
  Integration tests exposed a closed socket's reader still waiting for the
  ten-second heartbeat deadline. An optional cancellation callback now polls
  streamed receives at 50 ms, preserving partial frame bytes and the original
  deadline. Ordinary control reads retain their existing behavior. Tests cover
  silent/partial headers and bodies, timeout continuation, the overall deadline,
  Quit during tiny warm-up/solve in both modes, idle host exit, and bounded
  app detachment even when a teardown function never returns.
- **PR 21c — session sweep and installed registry inspection:**
  `server/solver/beat_runtime/inspection.py`; `scripts/qualify_installed_cpu.py`;
  `server/tests/beat_runtime/test_inspection.py`; `server/tests/test_temp_session.py`;
  `scripts/tests/test_qualify_installed_cpu.py`; this `CHANGES.md`.
  The qualifier isolates WG runtime/worker override bases only under the same
  exact official selector. Its packaged-interpreter probe imports the installed
  app root, derives the provider child from paths.PROVIDER_ID, and refuses to
  inspect any registry other than paths.worker_dir(). Existing records undergo
  HMAC hello/client admission and host-PID confirmation via adopt without engine
  startup. Stop uses authenticated cleanup_host, never PID signalling, with
  changed/foreign/unverifiable records retained and reported as qualification
  failures. Tests prove another session's directory stays untouched, bad tokens
  and providers cannot stop a live host, missing roots are not created, and
  official records survive a swept WG session. No temp-session production code
  needed changing: paths already keeps runtime/worker roots outside sessions.

PR 21 subdivisions are review slices of the requested design row. The target
is official JWSound/BEAT_Engine; no pin, requirement, numerical implementation,
production solve route, readiness integration or CUDA/ROCm behavior changes.
Reusing compiled_probe avoids another miniature request/result implementation.
The client/IPC correction is the only extra mechanism change: required to make
concurrent Quit release the actual reader within the shutdown budget. Host idle
exit remains separate from the official engine's numerical-cache reclamation.

PR 21 validation (requested Python, unpiped, each below two minutes):
- `scripts/run_tests.py server/tests/beat_runtime server/tests/test_solver_beat.py
  -q -p no:cacheprovider`: **905 passed in 95.23 s**.
- `scripts/run_tests.py server/tests/test_temp_session.py
  server/tests/test_official_beat_bridge.py server/tests/test_startup_performance.py
  server/tests/beat_runtime/test_warmup.py -q -p no:cacheprovider`:
  **161 passed, 1 skipped in 10.54 s**. The existing optional installed-contract
  test skips because beat_engine is absent. New app-hook tests use tmp_path
  static roots. Existing app tests temporarily read the already-built frontend
  from the main WG checkout; the worktree symlink was removed after checks.
- `scripts/run_tests.py scripts/tests/test_qualify_installed_cpu.py
  scripts/tests/test_qualify_installed_quit.py -m 'not slow' -q -p no:cacheprovider`:
  **224 passed, 1 deselected in 19.35 s**. Excluded
  `test_the_gate_passes_against_this_checkout`: its real-app parked-mesher
  Quit/relaunch run needs built app assets and is outside this fake/light gate.
  No other scripts/tests ran.
- Ruff passed on runtime sources/tests and every other changed Python file;
  `git diff --check` passed. The exact requested combined pytest and Ruff
  commands were attempted but the beat_adapter source/test directories are
  absent in this branch, so the available runtime/solver targets ran instead.

These slices add 27 fake/temporary-directory regression cases. The reader
correction also passed a repeated Quit stress group: 90 passed in 14.61 s
before removing the temporary repeated parameters and diagnostics. No Julia,
downloads, frontend build, real user/HBB-directory writes or full WG suite ran.
All requested PR 21 work is complete and uncommitted. Exact adapter checks and
real installed/Julia/Windows Job Object qualification remain unavailable or
outside this row's fake-worker gate; no engine PID API is assumed.


- **Review round 1 fixes (PR 21)** — review slices 21a/21b/21c:
  - **P1/A — fixed:** inspection rechecks failed connections under the record's
    spawn lock. Disappeared records are skipped; dead or start-mismatched hosts
    are pruned through `cleanup_host(prune_only=True, lock=...)`. Live hosts with
    unknown/matching start identity and changed successor records remain refused.
    Regression cases cover ENOENT/ECONNREFUSED for each interleaving, a detached fake
    host's idle exit between record read and connect, and a killed fake host's
    stale record. No recorded PID is signalled by inspection or cleanup.
  - **P2/B — fixed:** every client receive in hello, authentication, control,
    startup and submission admission polls cancellation, including before a
    socket becomes the cached lifetime connection. Startup cancellation also
    wakes its caller; the lifetime lock orders the retirement request before
    the reader disconnects. A successor whose startup queues behind the retiring
    host retries startup EOF once; numerical submissions never replay. The
    existing recovery test retains its nine-second shared deadline, allowing
    the cancelled caller to return before the host retirement backstop. Silent
    socket regressions model closure failing to wake recv; slow fake startup proves Quit releases both tiny and worker
    warm-up callers before `ensure_started` finishes, within two seconds.
  - **P2/C — fixed:** all qualifier isolation, cleanup and report-path decisions
    call `provider.official_selected(environment)`; the installed probe uses the
    same selector. Lifecycle/readiness/bootstrap/qualifier regressions retain
    the shared strip/warning semantics: `" official "` selects official BEAT;
    `"Official"` is unknown, warns and retains HBB. The alias remains unchanged.
  - **P3 record enumeration — fixed:** validate stems with `registry.record_path`
    before reading JSON, so only canonical host records are inspected. Ready,
    launch-spec and unrelated JSON files are ignored and retained.
  - **P3 Quit documentation — fixed:** the application Quit hook and manager
    detach docstrings state that idle or completed-warm-up hosts keep Julia warm
    for relaunch until `DEFAULT_IDLE_TIMEOUT` (1800 s) by design. After active-
    solve or aborted-warm-up Quit the engine retires, while the host Python
    process remains until idle exit.
  - **P3 tiny probe cleanup — fixed:** retain the events generator and close it
    explicitly in a finally, including probe failure before any iteration.
    The session still owns and closes its underlying lease/stream.
  - **P3 cancellation exception — fixed:** cancelled client receives translate
    `ConnectionAbortedError` (and socket errors during cancellation) to
    `HostError`, including remote streams; no bare cancellation OSError escapes.
    Session-local cancellation continues to use `SessionCancelled`.

  Files per review slice:
  - **PR 21a:** `server/solver/beat_runtime/warmup.py`;
    `server/tests/beat_runtime/test_warmup.py`.
  - **PR 21b:** `server/app.py`; `server/solver/beat_runtime/{client,manager}.py`;
    `server/tests/beat_runtime/test_{client,manager_review,warmup}.py`.
  - **PR 21c:** `server/solver/beat_runtime/inspection.py`;
    `scripts/qualify_installed_cpu.py`; `server/tests/beat_runtime/test_inspection.py`;
    `scripts/tests/test_{qualify_installed_cpu,bootstrap_official_beat}.py`.
  This `CHANGES.md` records all three slices. No design deviations: all fixes
  adapt WG to official JWSound/BEAT_Engine through public, optional engine APIs.
  No pins, dependencies, production route switches, HBB writes or CUDA/ROCm
  changes. Changes remain uncommitted as requested. Real Julia/installed/Windows
  Job Object qualification is outside this fake-worker review-fix gate.


PR 21 review-round validation (requested interpreter, unpiped; pytest invoked
through the required targeted launcher; each check below two minutes):
- `scripts/run_tests.py server/tests/beat_runtime server/tests/beat_adapter
  server/tests/test_solver_beat.py -q -p no:cacheprovider`:
  **1353 passed, 1 skipped in 99.77 s**.
- `scripts/run_tests.py server/tests/beat_adapter server/tests/test_temp_session.py
  server/tests/test_beat_cpu_runtime.py scripts/tests/test_qualify_installed_cpu.py
  scripts/tests/test_bootstrap_official_beat.py -q -p no:cacheprovider`:
  **654 passed, 1 skipped in 28.41 s**.
- Requested runtime/adapter Ruff check and Ruff on every other changed Python
  file passed; `git diff --check` passed.
- Focused new-mechanism group: **37 passed in 3.78 s**; startup recovery/client/
  warm-up group after the recovery fix: **34 passed in 16.44 s**. The final
  common suite also covers the added bounded-startup-retry regression.

All review items are fixed. No requested fixes remain unfinished. No Julia,
downloads, real user/HBB-directory writes, frontend jobs, full WG suite, or
`server/tests/test_startup_performance.py` test run occurred. Existing optional
adapter coverage accounts for the skip in each overlapping test group.

PRs 13-14 review round 2, fixed directly:
- P2: the registry's cross-process state check is guarded; a bad runtime root
  logs a warning instead of failing every engine selection.
- Default path restored to db4d26e6 behaviour: capabilities carry
  officialBeatRuntime only when the provider is selected; provision_command
  keeps the old text and quoting; HBB status lines still record the step and
  notify. Only the official path suppresses per-line notifications.
- The state-file stamp includes st_ino, since records are replaced atomically.

## Hosted Windows/Linux CI follow-up (2026-10-06)

- Windows registry privacy accepts the token user, LocalSystem and
  TrustedInstaller as owners; an Administrators owner additionally requires
  enabled membership in the effective token (UAC deny-only membership is
  insufficient). DACL allow ACEs remain restricted to the user and those local
  privileged principals, including read access because records hold secrets.
  Null/unsupported ACLs, foreign owners and untrusted readers/writers remain
  refused. New roots retain protected, inheritable user-only DACLs. Unlike
  HBB's directory selection plus POSIX-only chmod, WG still checks Windows
  ownership and DACLs on roots and files through ctypes, without pywin32.
- Linux AF_UNIX accept-queue saturation returns EAGAIN immediately; endpoint
  connects now retry that specific condition within one original deadline,
  closing the socket on exhaustion. Pending-peer tests retain every socket for
  cleanup, including when a subsequent connection fails.
- Platform tests supply a simulated POSIX uid/token branch, compare path
  components, use native Windows command-line parsing and model owned Windows
  Julia trees. The installed-probe fixture uses the real qualification profile
  environment, preserving Windows USERPROFILE/LOCALAPPDATA discovery.
- Adaptive tie ordering and the pre-switch golden replay are documented in
  `docs/reference/adaptive-frequency-sampling.md` and the adapter fixtures
  README. Float tolerance applies only to adaptive numerical roundoff; plain
  cases remain exact and the ZIP creator-OS field is canonicalized for hashes.
