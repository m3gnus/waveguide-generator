# Headless command-line contract

Status: canonical integration contract, version 1. Verified against `server/cli/`
and its contract tests on 2026-08-20.

The installer places `wg` inside the repository-local environment. From the checkout:

```text
.venv/bin/wg --help                 # macOS and Linux
.venv\Scripts\wg.cmd --help         # Windows
```

`python -m server.cli` remains an equivalent contributor invocation. The installed
command is repository-aware, so it works from directories outside the checkout.

## Inputs and commands

`wg validate DESIGN` and `wg solve DESIGN` accept `.mwg` or `.cfg` design text. For
language-neutral automation, use `--request REQUEST.json`; the file is the exact strict
`SolveRequest` object accepted by `POST /api/solve`, not a second CLI-only schema.

```text
wg validate --request request.json --json --no-mesh
wg solve --request request.json --events ndjson --output run-001
wg export-package JOB_ID --output retained-traces.zip
wg export-package --verify retained-traces.zip
```

Exactly one positional design or `--request` is required. A solve-options `--overlay`
may be applied to either input. Settings precedence is design/request, overlay, then
the explicit `--engine` flag. Overlay documents have `schemaVersion: 1`, an `options`
object, and reject unknown fields after merging.

Validation JSON has `schemaVersion: 1`, retains the compatibility `refusals` string
array, and adds stable `errors` with `code`, `stage`, `message`, `retryable`, and
`details`. Unless `--no-mesh` is set, validation compiles the real solver mesh without
creating a durable job.

## Event and terminal contract

`--events text` is the human default. `--events ndjson` and the compatibility spelling
`--json-events` stream Jobs Protocol v1 records followed by exactly one terminal
`kind: "outcome"`, `schema_version: 1` record. The outcome status is `complete`,
`refused`, `failed`, `cancelled`, or `interrupted`; failures carry the same structured
error shape as the HTTP adapter. On success, `result_sha256` identifies the exact
stored result bytes returned by the HTTP results endpoint whether or not `--output`
was requested. When output was written, `artifacts` separately reports each file's
exact-byte SHA-256 (excluding the self-describing `summary.json`).

Process exits are:

| Code | Meaning |
|---|---|
| `0` | validation or solve completed successfully |
| `1` | input refusal, unavailable engine, solve failure, or output failure |
| `2` | command-line usage error, or the selected application data directory is owned by another WG runtime |
| `130` | interrupted by SIGINT after cooperative cancellation began |

## Solve output

`--output DIR` is the persistence boundary for headless automation. It creates a new,
caller-owned directory and refuses to overwrite an existing one. A completed solve
writes:

| Artifact | Contract |
|---|---|
| `request.json` | canonical submitted `SolveRequest` |
| `effective-request.json` | normalized request durably stored after AUTO resolution and backend defaults |
| `execution-request.json` | request shape passed to the solver, including the resolved symmetry domain |
| `results.json` | the versioned solver result contract |
| `mesh.msh` | retained solver mesh, when available |
| `job.log` | complete job log |
| `summary.json` | schema version, timings, result identity, provenance, conventions, and SHA-256 digests |

JSON and text artifacts are written as explicit UTF-8 bytes, so every digest is
platform-independent and hashes exactly the file on disk. The artifact entries for
the three request documents also carry `canonical_sha256`, which hashes the parsed JSON
rather than its pretty-printed bytes. `summary.json.requestIdentity` names all three
stages.

Result provenance has `request_identity: "execution"`; its explicit `execution_*`
hashes match `execution-request.json`, including symmetry-domain resolution. The
explicit `effective_*` hashes match `effective-request.json`. The original unqualified
hash names remain backward-compatible aliases for the execution hashes. A caller can
cache or audit an evaluation without guessing whether AUTO, a backend default, or
symmetry resolution changed the request.

This bundle is intentionally separate from the GUI's design-grouped Workspace run
archive. The archive is frontend automation and adds `run.json`, `design.json`, and
human-facing JSON/CSV exports. A CLI solve is not automatically added to it. If
`--output` is omitted, results remain only in the WG job database and are subject to
its retention policy; a successful command warns about that state on stderr. NDJSON
stdout remains protocol-only.

## Scheduling boundary

One WG runtime executes a one-worker FIFO queue. Separate `--data-dir` values isolate
job stores; they are not a promise that multiple solver processes are safe or faster on
one machine. In particular, GPU and dense CPU solves can exhaust shared resources.
Version 1 supports serial execution. Long-running clients should normally submit work
to one persistent HTTP runtime rather than restart the CLI process for every request.
