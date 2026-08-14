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
