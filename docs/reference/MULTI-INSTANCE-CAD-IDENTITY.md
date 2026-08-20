# Multi-instance CAD identity

Status: implemented first slice in the Waveguide Generator server and browser
contract, 2026-08-20. The remaining cross-repository work is listed below.

## Addressing rule

`design_id` and `export_id` identify generated content, not a placement. Two
copies of one waveguide in a CAD assembly may share both values. The durable
placement join key is `instance_id`.

The current return contract keeps the rest of the addressing graph explicit:

| Concern | Address in a `.wgreturn` | Lifetime |
|---|---|---|
| Linked placement | `instances[].instance_id` | Minted by CAD at insert; stable for that managed link |
| Managed body | `scope.included[].object_id` plus `wglink_instance_id` | Native object identity within this return; never inferred from design or export ID |
| Placement transform | `instances[].assembly_from_link`, owned by that instance | Live placement state in this return |
| Drivable patch | `sources[].id` plus `instance_id` | Stable result address within the return |
| Default drive channel | `sources[].default_drive_channel_id` | Initial job/result address; one value may not span linked instances |

Body fingerprints and transform hashes are state evidence, not replacement
identities. Cross-export face identity remains out of scope.

## Fusion status selection

`POST /api/cadlink/fusion-status` accepts optional `instanceId`. The response
returns every matching active-document link as `matchingLinks` and the resolved
`selectedInstanceId`.

- Zero matches reports `not_linked`.
- One match remains automatic for compatibility.
- More than one matching placement with no exact selection reports
  `instance_selection_required`, returns no singular `link`, and enables no
  update or return action.
- A stale or duplicated selected ID also reports `instance_selection_required`.
- Returned body evidence is joined by exact `instance_id`; repeated copies of
  one design are never compared with the first body carrying that design ID.

The Geometry rail shows the matching instance IDs and posts the user's choice
on every later status request. The selected ID also travels in return requests
and the Fusion handoff marker (`expectedInstanceId`).

## Return listing and ingestion

The cheap return listing exposes `solverAnchorInstanceId`, per-instance body
object IDs, body-fingerprint and transform hashes, source IDs, and default drive
channel IDs. Source summaries retain their owning `instanceId`.

Ingestion accepts `expectedInstanceId`. A return containing repeated instances
of the expected design is refused when that value is absent. An explicit value
must occur exactly once, belong to the expected design, and equal the return's
solver anchor. The immutable ingest record retains the full identity graph and
the per-instance placement matrix under `identity`.

Validated manifests also require unique included-body object IDs, require every
linked body owner to name an `instances[]` record, and refuse a default drive
channel reused across different linked instances. Existing single-instance and
unlinked returns keep their old behavior.

## Solve, result, and export provenance

New ingestion records mark the identity inventory as `schema_version: 1`.
Imported-job submission resolves the immutable inventory against the submitted
drive channels and persists a `cad_source.identity` graph with the job. The
graph retains the selected and solver-anchor instance IDs, each instance's
native body object IDs and placement matrix, source IDs, default drive-channel
IDs, and the actual submitted drive-channel-to-source-to-instance mapping.

An older ingestion with no identity inventory remains solvable and reports no
identity; WG does not manufacture one from a design ID, name, body fingerprint,
or source order. If an ingestion does claim identity but a source owner is
unknown, duplicated, or contradictory, submission refuses it and requires a
new ingest.

Completed imported results expose the same graph at
`provenance.cad_identity`, `metadata.cad_identity`, and in each result channel's
metadata. Permanent `run.json` files retain it under `cad.identity`. Manual and
automatic result bundles also contain `<run>_cad_identity.json`, a versioned
sidecar that keeps non-JSON curve and image exports independently auditable.
Existing run records and parametric results are byte-compatible: absent CAD
identity is omitted rather than replaced with invented or null addresses.

## Remaining work

The WG status/read/ingest/UI and downstream result/export paths no longer use
first-match placement behavior. Exact-instance Fusion updates are also pinned
in the packaged WGLink source. The workspace TODO still has these adapter gaps:

1. The add-in heartbeat should eventually publish native managed-body and live
   transform identities directly. Today WG receives body fingerprint state in
   the heartbeat and the full body/transform graph in `.wgreturn`.
2. Onshape needs an equivalent instance-addressed status/update contract before
   it can host repeated generated parts as independently selectable placements.
3. A future cross-export body/entity identity, if needed, must be authored by
   CAD. WG must not derive it from names, face order, or fingerprints.
