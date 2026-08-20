# STEP parser isolation decision

Status: accepted design gate for issue #29, 2026-08-13.

## Decision

Every STEP payload returned by Onshape, Fusion, or a user-authored CAD tool is
untrusted input. HTTPS transport, an authenticated Onshape account, and a checksum in a
`wgreturn` bundle prove delivery or immutability; they do not make the CAD bytes safe to
parse. WG-generated STEP that has never crossed an external editor may remain on the
current in-process export path.

Broad cloud-return ingestion is blocked until external STEP inspection and meshing run
in fresh child processes. The existing persistent Gmsh thread is a serialization and
signal-ownership mechanism, not a security boundary: an OCC crash, runaway allocation,
or hang there still takes down or wedges the server.

Use two disposable process invocations for a returned artifact:

1. **Inspect** parses STEP text and OCC topology and returns only bounded, schema-checked
   evidence needed to build `wgreturn`.
2. **Mesh** reopens the checksum-bound STEP, resolves source roles, and writes staged
   mesh/viewport artifacts. It does not receive database credentials or the application
   data directory.

Neither child is reused. The parent owns the registry, cache publication, and final
atomic moves.

## Initial hard limits

These are refusal limits, not targets to allocate eagerly. They are deliberately far
above current single-waveguide artifacts and can be raised only with measured fixtures
and an explicit contract revision.

| Boundary | Limit |
| --- | ---: |
| Downloaded STEP body | 64 MiB |
| `wgreturn.json` | 1 MiB |
| STEP entity records | 1,000,000 |
| One STEP record | 8 MiB |
| One decoded STEP label | 4 KiB |
| Inspect wall time | 60 s |
| Inspect resident memory | 2 GiB |
| Mesh wall time | 10 min |
| Mesh resident memory | 4 GiB |
| Child structured result | 8 MiB |
| One staged mesh or viewport artifact | 512 MiB |
| Concurrent external-STEP children | 1 |

The downloader must enforce the 64 MiB limit while streaming and reject an excessive
`Content-Length` before reading. Reading an unbounded response into `bytes` and checking
afterward does not satisfy the gate. The STEP text parser must scan the file once under
the record and label limits; the current repeated `Path.read_text()` calls are not the
isolated implementation.

The mesh child remains subject to the existing triangle, dense-solver-memory, source
resolution, body inventory, and healing gates. A parser budget does not weaken any
geometric or solver budget.

## Child authority

Each child receives a read-only copy of one checksum-verified STEP and a new empty
staging directory. It must have:

- no inherited Onshape credentials, API tokens, database handles, listening sockets, or
  unrelated environment variables;
- no network access;
- a private working directory and no writable path outside its staging directory;
- inherited file descriptors closed;
- process-tree termination on timeout, cancellation, memory excess, or parent exit;
- platform enforcement: job objects on Windows and process/resource limits on macOS and
  Linux. Container or `seccomp` hardening may be added, but is not a substitute for the
  cross-platform process boundary.

The parent accepts only a versioned JSON result and explicitly named staged artifacts.
It rejects oversized output, absolute or traversing paths, symlinks, unexpected members,
non-finite values, checksum/size mismatches, and any child exit that is not a clean
success. Native stderr is diagnostic text with a bounded retained tail, never a verdict.

## Failure policy

A timeout, crash, resource-limit kill, malformed result, or failed artifact verification
is an ordinary stage-labelled ingest refusal. It must not partially publish a return,
poison a cache index, retry with looser limits, or silently fall back to the in-process
Gmsh worker. Temporary input and output are removed after the parent records the
refusal; an already immutable source bundle may be retained according to normal CAD-link
retention policy.

## Acceptance tests before enablement

- A response with an excessive declared or streamed size is refused without buffering
  the complete body.
- Over-limit record counts, record lengths, labels, result JSON, and artifacts fail at
  their stated boundaries.
- A deliberately hanging parser and its descendants are killed at the deadline, after
  which a normal import succeeds.
- A native crash in OCC/Gmsh becomes a refusal while the server and job runtime remain
  healthy.
- Memory exhaustion is contained on Windows, macOS, and Linux.
- Child output cannot escape staging through absolute paths, traversal, or symlinks.
- The real Part Studio → STEP → `wgreturn` → ingest smoke test passes through both fresh
  process boundaries with identical source-role and geometry evidence.

Until those tests pass on all three operating systems, Onshape return can remain an
explicit development path but must not become an automatic or broadly advertised ingest
route.

## Implementation status, 2026-08-20

The boundary is built and the limits above are enforced in code. Where the
implementation departs from the wording of this document, it says so here
rather than quietly.

| Limit | Enforced in |
| --- | --- |
| Downloaded STEP body, 64 MiB | `server/cadlink/onshape/client.py` — streamed, with `Content-Length` refused before the first read |
| `wgreturn.json`, 1 MiB | `server/cadlink/wgreturn.py`, from `stat()` before the file is read |
| Records, record length, decoded label | `server/cadlink/step_text.py`, one streaming scan, run in both children |
| Wall time, resident memory, result and artifact size, concurrency | `server/cadlink/isolation.py` |

Inspect runs `server/cadlink/step_evidence.py`; mesh and viewport run
`server/mesh/imported.py`. Both go through `server/cadlink/child_main.py`,
launched once per artifact by `server/cadlink/isolation.py` and never reused.
The parent still owns the registry, the cache index, and every atomic move.

Two deliberate departures:

* **Resident memory is sampled by the parent, not capped with `RLIMIT_AS`.** A
  Python process with gmsh imported reserves about 435 GB of address space on
  macOS against 52 MB resident, so an address-space cap set at the resident
  figure in the table would refuse every legitimate import. The parent samples
  the child's process group with `ps` and kills the tree when it passes the
  budget, which is the number this table actually names. Windows gets the same
  containment from the job object's commit limit. The child still sets
  `RLIMIT_CORE` and `RLIMIT_FSIZE`, where a limit means the same thing
  everywhere.
* **"No writable path outside its staging directory" is approximated.** The
  child's cwd, `TMPDIR`, and `HOME` are inside staging, it inherits no data
  directory, and anything it leaves outside `staging/out` is discarded and
  refused rather than published. Genuinely revoking write access to the rest of
  the filesystem needs the container or `seccomp` hardening this document
  already lists as optional; the process boundary, which is the required part,
  is in place. Network access is blocked at the Python layer only, for the same
  reason.

Acceptance tests live in `server/tests/test_cadlink_isolation.py` (harness,
driven by a deliberately misbehaving child in `server/tests/isolation_doubles.py`),
`server/tests/test_step_text.py` (text budgets),
`server/tests/test_onshape_adapter.py` (streaming download), and
`server/tests/test_onshape_return.py` (the real Part Studio → STEP → wgreturn →
ingest smoke test, asserting both crossings and identical evidence).

**Not yet demonstrated: macOS only.** The suite has been run on macOS. The
Linux and Windows rows of "memory exhaustion is contained on Windows, macOS,
and Linux" are unverified, and the Windows job-object path has never executed.
Until it has, the enablement condition at the end of this document still holds.
