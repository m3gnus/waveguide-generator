# Portable mesh replay v1

`.wgreplay` is a UTF-8 JSON input package for a completed standalone file-mesh
run. Runs offers **Mesh replay** under Export; permanent file-run archives also
include it. **Open mesh replay…** prepares the model in another workspace.
The user presses Solve explicitly. Opening creates no job and does not restore
an old completed job or results into the new database.

The package contains:

- `schema_version: 1`, `kind: "wg-file-mesh-replay"`, the numerical contract
  `imported-free-space-v1` and the source-motion contract;
- original Gmsh text with filename, units, forward/up axes and driven groups;
- exact canonical solver mesh text and SHA-256 digests of both meshes;
- the complete backend-persisted resolved solve request, including channel
  assignments, resolved driver numbers, frequencies, polar settings, accuracy
  and explicit engine;
- historical result provenance, where retained, and a content checksum over
  the canonical JSON of every field except `content_sha256`.

Digests use the `sha256:` prefix. Canonical JSON uses
`server.contracts.canonical.canonical_utf8_strict_json`. Checksums detect changed
content; they are not signatures or evidence that a package is trusted.
Numerical outputs depend on the installed solver stack. Historical dependency
provenance is informational and does not install or authorize dependencies.

## Validation and ownership

`GET /api/jobs/{id}/mesh-replay` reads one retention-consistent job snapshot.
The admitted import ID and hashes must match its immutable file record. A
present job mesh is cross-checked; if retention removed that copy, the verified
retained import remains sufficient. Original bytes must still exist, match
their digest and reconstruct exactly the solver mesh. Historical axial results
must positively establish the current per-source motion contract.

`POST /api/mesh-replay` validates the schema, versions, content/mesh checksums,
mesh topology, units, axes, source assignments and saved physics. It reconstructs
the solver mesh from original bytes and passes ordinary file registration.
Setup validation precedes publication. The ingestion row atomically records
the package digest, so repeated opens and lost-response retries reuse that
import. No second job ledger or package registry is introduced.

Local ingestion IDs and hashes are rebound server-side; old parent-job and
submission IDs are cleared. Paths, producer records, CAD identities and frame
approvals are not package fields. Solve uses existing admission and a new
durable submission ID. An unavailable explicit engine is refused by that path.

The UI preserves the exact request through Save, restart and unanswered-request
recovery. Automatic settings hydration cannot replace it. Actual setup edits
end exact replay, and the recorded-setup summary goes; until then the panel shows
both the recorded setup and controls for a new ordinary mesh setup.

## Limits and existing archives

The request/package limit is 64 MiB; original meshes retain the ordinary
16 MiB import limit. V1 supports full-domain standalone ASCII Gmsh 2.2 linear
surface meshes without a mesh ladder. CAD bundles, reduced domains, FEM volumes
and native source identities need a typed CAD replay extension. Existing CAD
reruns, preparation, native downloads and archives remain available.

Missing or changed replay inputs produce an explicit reason. Permanent result
archives can still be written and record `artifacts.meshReplay.filename: null`
with that reason in `run.json`; transport/server failures retry archiving.
Existing `run.json`, charts or an isolated `.msh` are not portable replay
packages. No existing user folder is moved or rewritten automatically.
