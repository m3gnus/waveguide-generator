# Review slices

All paths are relative to the WG repository root. Changes are additive; no
production caller adopts beat-engine, no pins change, and HBB state is untouched.

- **PR 2 — paths/assets:** `server/solver/beat_runtime/{__init__,paths,assets}.py`;
  `server/tests/beat_runtime/test_{paths,assets}.py`; this `CHANGES.md`.
  WG overrides select bases with `wg-beat-engine` appended. Asset discovery is
  lazy, uses beat-engine's public API, and reports missing package/wheel assets.
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
- **PR 8 — downloads/checksums/disk:** `server/solver/beat_runtime/installer.py`
  (release matrix, fetch/checksum helpers and disk budgets);
  `server/tests/beat_runtime/test_installer_downloads.py`; this `CHANGES.md`.
  Julia 1.12.7 covers macOS arm64/x86_64, Windows x86_64 and Linux
  x86_64/aarch64. Injectable fetchers write `.part`; SHA-256 verification
  precedes atomic publication. CPU requires 2 GiB and GPU budgets remain 6 GiB.
  Offline/interrupted downloads and checksum failures preserve prior archives.
- **PR 9 — extraction/recovery:** `server/solver/beat_runtime/installer.py`
  (extraction, ownership/recovery and executable selection);
  `server/tests/beat_runtime/test_installer_extraction.py`; this `CHANGES.md`.
  Validate ZIP/tar staging before publishing a version/platform tree, recover
  interrupted staging/replacement, and preserve older WG installs for workers.
  External/custom executables and explicit older Julia remain usable; legacy
  managed hints trigger a new WG download. Unowned trees and linked staging
  paths are refused; no external or legacy install is deleted. External choices
  are recorded without stamping the requested portable version onto them.

PR 7–9 scope notes: the target is official JWSound/BEAT_Engine (`beat-engine` /
`beat_engine`); these modules need no engine import or WG-specific engine API.
`julia.json` uses provider/state_schema plus julia_executable, julia_version,
origin and julia_identity (SHA-256 of executable bytes), never backend readiness.
The temporary atomic record helpers in discovery.py have a TODO to delegate to
state.py after the parallel state PR lands. Installer callers must hold the
provisioning lock; lock/orchestration integration belongs to later PRs.
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
