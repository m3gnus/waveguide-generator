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
error shape as the HTTP adapter.

Process exits are:

| Code | Meaning |
|---|---|
| `0` | validation or solve completed successfully |
| `1` | input refusal, unavailable engine, solve failure, or output failure |
| `2` | the selected application data directory is owned by another WG runtime |
| `130` | interrupted by SIGINT after cooperative cancellation began |

## Solve output

`--output DIR` creates a new directory and refuses to overwrite an existing one. A
completed solve writes:

| Artifact | Contract |
|---|---|
| `request.json` | canonical submitted `SolveRequest` |
| `results.json` | the versioned solver result contract |
| `mesh.msh` | retained solver mesh, when available |
| `job.log` | complete job log |
| `summary.json` | schema version, timings, result identity, provenance, conventions, and SHA-256 digests |

The result payload also contains request, geometry, and solve-options hashes. A caller
can therefore cache or audit an evaluation without interpreting filenames.

## Scheduling boundary

One WG runtime executes a one-worker FIFO queue. Separate `--data-dir` values isolate
job stores; they are not a promise that multiple solver processes are safe or faster on
one machine. In particular, GPU and dense CPU solves can exhaust shared resources.
Version 1 supports serial execution. Long-running clients should normally submit work
to one persistent HTTP runtime rather than restart the CLI process for every request.
