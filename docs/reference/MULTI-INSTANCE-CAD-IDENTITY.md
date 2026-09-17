# Multi-instance CAD identity

Status: implemented for Fusion placements, managed Onshape Part Studio links,
WG ingestion, result provenance, and exports as of 2026-08-20. The remaining
Assembly/cross-export boundaries are listed below.

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
| Drivable patch | `sources[].id` plus `instance_id` | Stable result address within the return; with `source-identity-v1`, also the CAD-authored identity of that source across exports |
| Default drive channel | `sources[].default_drive_channel_id` | Initial job/result address; one value may not span linked instances |

Body fingerprints and transform hashes are state evidence, not replacement
identities. Cross-export source identity is opt-in (below); cross-export body identity
remains out of scope.

## Cross-export source identity (`source-identity-v1`)

A return that lists `source-identity-v1` in `required_features` gives the existing
`sources[].id` a stronger meaning. There is no new field.

- **Meaning.** Each id is the CAD-authored identity of that logical source: authored in
  CAD metadata, resolved to faces at export, and the same in every later export of that
  source. `instance_id` keeps its meaning.
- **Every source participates.** The return has at least one source, and every source
  has an id.
- **Form.** An opaque string: non-empty, with no leading or trailing whitespace, and at
  most 25 UTF-8 bytes. WG does not parse it. Ids remain unique across the whole return,
  not only within an instance.
- **The whole mesh name must fit, and WG checks it when it reads the bundle.** Ingestion
  writes each source into the mesh's physical name
  `wg-import-v1|tag=<tag>|source_id=<id>|instance_id=<instance>|role=<role>` (`null` for
  an unlinked source), and gmsh writes at most 128 UTF-8 bytes of a physical name,
  silently cutting the rest; a cut mesh would be refused only after meshing. So WG
  refuses the source at validation when that name, with the largest possible tag 9999
  (tags start at 101, and a fifth digit needs more sources than a 1 MiB manifest holds),
  exceeds 128 UTF-8 bytes -- whether the id, the instance id or the role is too long.
- **Why 25 bytes for the id.** It is the id's share of that budget with WGLink's own
  values: 47 bytes of fixed text, 4 tag digits, a 36-byte instance id (WGLink mints
  UUIDs) and 16 for `PASSIVE_CARDIOID`, its longest role, leave 25. A writer can size
  its identities from this alone; the whole-name check still applies. Both limits count
  bytes because gmsh does.
- **Refused by the writer, never chosen by WG.** When a source's authored identity is
  missing, or resolves to no face, to split faces, or to faces another source claims, the
  CAD writer refuses to write the bundle and asks for reassignment. WG cannot tell from an
  opaque string which face was meant, so it never remaps or picks one. What WG checks is
  what it can see: a duplicate, empty, untrimmed, over-long or non-string id refuses the
  whole bundle.
- **CAD-private.** Fusion entity tokens and face-UID stamps stay inside the add-in; they
  never enter the manifest.
- **Negotiated.** WG advertises `"sourceIdentity": 1` in `wg-capabilities.json`
  (`docs/architecture/CAD-OPERATIONS.md`, "Capability file"). A WG that does not advertise it
  refuses the feature as unknown.
- **Absent means unchanged.** Without the feature a source id keeps every earlier rule
  (unique, non-empty; no trimming or length rule), and project-setup inventory keys are
  byte-identical to earlier releases.

The project-setup inventory is keyed by source id, canonical role and `required`
("Project setups" in `CAD-OPERATIONS.md`), so a setup follows sources whose identity is
stable and is not inherited by a reassigned one.

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
channel IDs. Source summaries retain their owning `instanceId`. Each item also
carries `documentNativeId`, the manifest's `document.native_id` (null when the
adapter did not record one). A parked Fusion solve request is superseded only by
a newer return of that same document; a return of another document, or of an
unnamed one, leaves the request and the selection it is preparing in place.

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

## Onshape managed-link selection

WG assigns every registry-owned Onshape link a durable opaque `wgo_…`
instance ID. Schema-7 links receive one during the schema-8 migration and keep
it across updates. `POST /api/cadlink/onshape/status` accepts optional
`instanceId` and returns `matchingLinks` plus `selectedInstanceId`; one lineage
link remains automatic, while multiple links or a stale explicit ID produce
`instance_selection_required` with no singular link or action.

The same optional ID is carried by send, return, and unlink. Those actions
resolve exactly one lineage/account link before an Onshape document mutation or
STEP translation begins. The return writes that ID into its sole
`instances[].instance_id`, source selector, and solver anchor, then passes it as
the expected instance to ingestion.

This identity currently names WG's managed Onshape **document/Part Studio
link**, not an occurrence in an Onshape Assembly. The adapter does not read an
Assembly, occurrence paths, or occurrence transforms, so it cannot honestly
distinguish two Assembly placements of one generated Part Studio. Supporting
those placements requires both an Onshape Assembly API implementation and a
product decision about whether WG updates the shared Part Studio definition or
one occurrence's independently copied definition. Until then, the dedicated
Part Studio is the atomic Onshape link and WG does not infer placement identity
from document names, element IDs, recency, or geometry fingerprints.

## Remaining work

The WG status/read/ingest/UI and downstream result/export paths no longer use
first-match placement behavior. Exact-instance Fusion updates and strict
heartbeat body/transform/source/drive identity are pinned in the packaged
WGLink source. Two deliberate boundaries remain:

1. Onshape's local status/update/return contract selects exact managed Part
   Studio links. True repeated-placement parity remains blocked on Assembly
   occurrence discovery/transform support and the shared-definition product
   decision described above.
2. Cross-export source identity is `source-identity-v1`, authored by CAD. A future
   cross-export body identity, if needed, must be authored by CAD as well. WG must not
   derive either from names, face order, or fingerprints.
