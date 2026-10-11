# Headless command-line contract

Status: canonical integration contract, version 1. Verified against `server/cli/`
and its contract tests on 2026-10-11.

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

A text design or JSON request with no sweep start uses 50 Hz. Omitted sweep
end keeps the headless default of 20 kHz; an omitted count uses 32 points. New editor
designs state 50 Hz–16 kHz with 32 points. Stated design values, overlay range
and count, and explicit frequency lists follow the precedence above.

## Event and terminal contract

`--events text` is the human default. `--events ndjson` and the compatibility spelling
`--json-events` stream Jobs Protocol v1 records followed by exactly one terminal
`kind: "outcome"`, `schema_version: 1` record. The outcome status is `complete`,
`refused`, `failed`, `cancelled`, or `interrupted`; failures carry the same structured
error shape as the HTTP adapter. On success, `result_sha256` identifies the exact
stored result bytes returned by the HTTP results endpoint whether or not `--output`
was requested. When output was written, `artifacts` separately reports each file's
exact-byte SHA-256 (excluding the self-describing `summary.json`).
Once a job is accepted, its outcome includes `job_url`,
`http://127.0.0.1:<port>/?job=<id>`, which opens that run in WG. Human output
prints the same link on completion.

Process exits are:

| Code | Meaning |
|---|---|
| `0` | validation or solve completed successfully |
| `1` | input refusal, unavailable engine, solve/output failure, or other backend failure |
| `2` | command-line usage error |
| `3` | backend data-folder mismatch (`backend_data_dir_mismatch`) |
| `4` | backend startup deadline expired (`backend_start_timeout`) |
| `5` | backend could not be spawned or exited during startup (`backend_spawn_failed`) |
| `130` | SIGINT before submission, after this job was stopped, or during result/output work |

Backend errors explain the installation, folder or log to check. The CLI never
retries into another folder. SIGINT calls `POST /api/stop/{id}` for this job;
the backend and its other queued jobs stay running. Before submission, SIGINT exits
without creating a job. If completion races cancellation, the job's HTTP terminal
state wins; exit 130 for cooperative cancellation requires a cancelled job. Once
HTTP reports terminal, normal SIGINT handling resumes for result downloads and output.

Submission key conflicts retain the CLI code `submission_conflict` and the
instruction to choose a new `clientRequestId`. Other HTTP error envelopes retain
their backend codes. HTTP refusals without an error envelope use
`backend_http_error`, with `details.http_status`; only 5xx responses are retryable.
Transport failures use `backend_connection_failed` and are retryable.

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
The effective and execution documents come from
`GET /api/jobs/{id}/requests`; result, mesh and log bytes use their existing HTTP
routes. The serializer and bundle contents are unchanged from in-process solves.

Result provenance has `request_identity: "execution"`; its explicit `execution_*`
hashes match `execution-request.json`, including symmetry-domain resolution. The
explicit `effective_*` hashes match `effective-request.json`. The original unqualified
hash names remain backward-compatible aliases for the execution hashes. A caller can
cache or audit an evaluation without guessing whether AUTO, a backend default, or
symmetry resolution changed the request.

This bundle is intentionally separate from the GUI's design-grouped Workspace run
archive. The archive is frontend automation and adds `run.json`, `design.json`, and
human-facing JSON/CSV exports. Every CLI solve is visible in the backend's WG job
history, even if no frontend was open during the solve. An open WG frontend can
archive eligible CLI runs according to its archive preference; the CLI itself
writes only its requested bundle. If `--output` is omitted,
results remain in that shared job database and are subject to its retention policy;
a successful command warns about that state on stderr. NDJSON
stdout remains protocol-only.

## Scheduling boundary

The CLI discovers the backend holding the selected data folder's `InstanceLock`
and submits the existing request to `POST /api/solve`. With no healthy owner,
`ensure_backend` serialises startup and launches `launch/serve.py` on that exact
folder with `--no-browser`. The small argv builder is
`launch/backend_argv.py:headless_backend_argv`, matching the status launcher's
server entry point and working outside the checkout directory. Concurrent CLI
commands reach one backend; its database ownership guard remains in force.
Only an explicitly supplied `--data-dir` is forwarded as an argument;
`WG2_DATA_DIR` is inherited. With neither override, the backend uses the same
Documents runs default and legacy workspace adoption as a GUI start. Discovery
still verifies that the backend owns the selected data folder.

Progress uses the existing `/ws/jobs` stream with pinned `websockets`, preserving
Jobs Protocol v1 records and partial results. HTTP status checks determine the
terminal job state. After a disconnect, the relay reconnects using the protocol's
cursor resume while HTTP polling continues. Stream loss cannot fail an accepted
job; after HTTP reports terminal the CLI waits at most 0.5 seconds for its terminal
event, then uses HTTP's result even if that event was dropped. Snapshots include
the shared job history; individual events and partial results are filtered to this
command's job. HTTP uses stdlib `urllib`; both transports send no Origin
header. The CLI closes its relay before printing the last outcome line.

One WG backend executes a one-worker FIFO queue. Separate `--data-dir` values isolate
job stores; they are not a promise that multiple solver processes are safe or faster on
one machine. In particular, GPU and dense CPU solves can exhaust shared resources.
Version 1 supports serial execution. The backend remains running after the CLI exits.
To stop a backend started by the CLI, read its `pid` from `GET /health` at the
printed job URL's host and terminate that process: `kill -TERM PID` on macOS/Linux,
or `Stop-Process -Id PID` in Windows PowerShell. SIGTERM uses the launcher's graceful
shutdown on macOS/Linux; Windows process termination is immediate. Closing the
browser or a status window that adopted this backend does not stop it. Terminating
the backend also ends its active work; CLI SIGINT stops only the submitted job.

Runtime unit tests may explicitly pass the Python-only keyword
`_internal_in_process=True` to `server.cli.args.main` or `server.cli.solve.solve_path`.
It is disabled by default and has no command-line option. Normal solves never
construct `JobRuntime` in the CLI.
