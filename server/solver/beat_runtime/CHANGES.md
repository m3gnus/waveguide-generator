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
