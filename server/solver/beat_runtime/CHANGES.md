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
  Length-prefixed UTF-8 JSON objects retain HBB's 512 MiB frame ceiling.
  Clean EOF is distinct from truncated headers/bodies; invalid JSON, nonfinite
  constants, nonobjects and oversized frames are refused. Endpoints are Unix
  sockets or IPv4 loopback only; encoded Unix paths above 100 bytes fall back
  to TCP, including an explicit Unix preference. Binding never removes an
  existing socket or enables address reuse. Bound sockets are private on POSIX.
- **PR 16 — registry/cleanup:** `server/solver/beat_runtime/{registry,cleanup}.py`;
  `server/tests/beat_runtime/test_{registry,cleanup}.py`; this `CHANGES.md`.
  Reuse `paths.worker_dir()` outside swept sessions. Provider/protocol-scoped
  keys, strict records, unique atomic temporary files, 0700 roots and 0600
  record/spec/lock files replace HBB's permissive record handling. Corrupt,
  foreign, linked and nonprivate records raise `RecordRefused` and remain.
  Persistent advisory locks cover recheck/publish and cleanup; kernel release
  on owner death and fixed Windows byte zero preserve spawn exclusion.
  Cleanup verifies provider/key/token and the host's returned PID before live
  shutdown, using the authenticated connection instead of PID signals. An
  unavailable endpoint plus a proven-dead PID permits scoped orphan pruning;
  any answering peer must authenticate even when the recorded PID is dead.
  Unverified live hosts and changed/successor records are retained and reported.
  No unauthenticated HBB stale-PID termination path is carried over.

PR 16 review subdivisions (each below the design's roughly 400-line ceiling):
16a records/private publication (`registry.py` through record/spec/token APIs,
record tests); 16b spawn exclusion/liveness (remaining `registry.py`, process-race,
owner-death, timeout and Windows-fake tests); 16c authenticated cleanup
(`cleanup.py`, `test_cleanup.py`). These are review slices of the same requested
PR 16 scope; no host/client/ownership implementation is included.

Future host/spawn callers use `host_key`, `key_id`, `new_token`, `HostRecord`,
`write_record`, `write_launch_spec`, `launch_spec_path`, `log_path`,
`spawn_lock_path` and `SpawnLock`. Supply the complete launch identity to
`host_key` (backend, executable/content identities, engine/runtime fingerprints,
resolved assets/project/sysimage, integer threads and effective environment);
the key helper adds WG's provider/protocol namespace. Hold spawn exclusion from
the second `read_record` through publication. Future clients use
`validate_record`, `connect_authenticated`, `send_frame` and `receive_frame`;
hosts must validate `hello_message` fields and return matching provider,
protocol/version, key/id, token and their own `host_pid` in `hello_ok`.
`cleanup_host` owns its own spawn exclusion and requests `shutdown_ok` followed
by host exit. `pid_alive` is a conservative query, never authorization to signal.

Deviations: the engine target is official JWSound/BEAT_Engine, as instructed;
this layer imports neither engine nor HBB. Cleanup has no PID-termination API:
authenticated IPC shutdown avoids PID-reuse races between authentication and
signalling. Missing records return None; malformed/foreign records raise rather
than masquerading as missing and allowing replacement. PR 16 is subdivided for
review size. Windows locking/liveness are fake-tested; real host lifecycle,
installed qualification and Windows Job Objects remain later PR gates.
