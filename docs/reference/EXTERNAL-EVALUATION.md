# External evaluation API

Status: canonical integration contract, version 1. Verified against the FastAPI,
headless CLI, parameter-catalog, and result-contract tests on 2026-08-20.

WG exposes a transport-neutral evaluation boundary: a strict design and solve request
goes in; validation, geometry, engine selection, lifecycle, and versioned acoustic
results remain WG responsibilities. External parameter-search, experiment, or analysis
software remains a peer client and does not import WG, mesher, or solver internals.

## Discovery

| Resource | Purpose |
|---|---|
| `GET /api/capabilities` | engines and fast paths available on this machine |
| `GET /api/integration/v1/parameters` | stable parameter IDs, JSON paths, families, units, editor bounds, enums, expression support, and declarative conditions |
| `GET /api/integration/v1/design-schema` | JSON Schema for the discriminated design family |
| `GET /openapi.json` | live HTTP schema |
| `docs/reference/openapi.v1.json` | release snapshot checked for drift in the repository |

Catalog `editor_bounds` describe the editor, not hard validity or recommended search
ranges. The strict request/design schema and WG validation are authoritative. Search
ranges belong to the calling study.

## HTTP lifecycle

1. Submit a strict `SolveRequest` to `POST /api/solve`.
2. Retain the returned `job_id`; `client_request_id` is echoed when supplied.
3. Observe `GET /api/status/{job_id}` or Jobs WebSocket v1 at `/ws/jobs`.
4. On completion, read `GET /api/results/{job_id}` and optionally the mesh or log.
5. Use `POST /api/stop/{job_id}` for cooperative cancellation.

`SolveRequest.geometry` is discriminated by `type`: `parametric` carries the canonical
design, while `imported` references a verified CAD ingestion. The legacy top-level
`design` spelling remains accepted, but new clients should send `geometry` explicitly.

`client_request_id` is an optional trimmed string up to 200 characters.
`client_metadata` is a finite JSON object limited to 16 KiB. Both are stored with the
job and echoed into final results. Use them instead of design-text passthrough blocks.

## Results, identity, and caching

Every final result has top-level `result_kind` and `result_contract_version`:

| Kind | Version | Shape |
|---|---:|---|
| `parametric` | `1` | one frequency/result envelope |
| `multi_channel` | `2` | `channels`, `channel_order`, and shared metadata |

`provenance.schema_version: 1` contains the WG version, pinned dependency SHAs,
resolved engine, and canonical SHA-256 hashes for the request, geometry, and solve
options. `GET /api/results/{job_id}` adds `ETag` and `X-WG-Results-SHA256` for the exact
stored result bytes. Clients should key caches with these identities rather than an
engine name or a hand-built parameter string.

## Errors and compatibility

Submission refusals preserve the human-readable `detail` string and add:

```json
{
  "error": {
    "schema_version": 1,
    "code": "engine_unavailable",
    "stage": "submission",
    "message": "...",
    "retryable": false,
    "details": {},
    "client_request_id": "study-17"
  }
}
```

HTTP status still expresses the broad class. Code and stage are the stable programmatic
fields; message is explanatory text. Pydantic request-shape failures retain FastAPI's
standard machine-readable 422 detail list.

WG follows additive compatibility within a published contract version. A breaking
request, error, result, or parameter-catalog change requires a new version. Clients
must reject result kinds or versions they do not understand and must tolerate new
metadata fields.

The runtime is a one-worker FIFO scheduler. Version 1 promises durable asynchronous
submission and serial execution, not process-level parallelism. Multiple data
directories are isolation tools, not a resource-scheduling contract.

The standard-library example at `examples/external_evaluator.py` exercises discovery,
submission, polling, structured errors, result retrieval, and digest verification.
