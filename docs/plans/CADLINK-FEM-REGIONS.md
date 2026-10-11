# Coupled FEM/BEM solves of CAD Link geometry

Status: proposed, 2026-10-11. Slices F1 to F5 are implemented on topic
branches and tested. F6 ingest and F7 geometry preparation are implemented;
engine routing and live F7 acoustic qualification remain pending. Nothing is
routed to a solver yet, and `femRegions` is advertised only when `WG2_FEM_REGIONS=1`.

The same flag gates bundle admission and the isolated `fem_region` task. When
unset, WG refuses `fem-regions-v1` as an unknown required feature before STEP
inspection or meshing; it never silently solves only the exterior. The shared
protocol remains flag-independent. The frontend has no FEM capability feed or
result route yet, so `FemFieldView` has no production importer or mount, even
in an opted-in backend. F9 owns that wiring; the public API remains unchanged.

The aim is Boundary Lab's interior capability for geometry drawn in Fusion.
Bounded air (a rear chamber, a port, a phase plug, a compression driver's
front and rear cavities) is solved with tetrahedral FEM, coupled to WG's BEM
exterior, and the interior field is shown on a slice plane the way Boundary Lab
shows its interior observation planes.

Two existing tools set the shape of the work:

- **The Fusion-to-MSH add-in** (`JWSound/fusiontomsh`) has an *Export FEM MSH*
  dialog. It takes one solid body, a default size and any number of named
  surface groups, each with a local size and its faces, and validates live so
  the export button stays disabled until the groups are complete. It remembers
  a body's groups through entity tokens. It matches Fusion faces to gmsh
  surfaces by area, centroid and bounding box, because STEP has no stable face
  identity.
- **Boundary Lab** (`m3gnus/boundary-lab`) compiles a physical system of
  bounded and unbounded air regions, tagged boundaries, interfaces with
  conforming topology maps, and components that own moving boundaries. It
  solves that on the BEAT engine. After a solve it draws walls flat-coloured
  by role. The field is shown only on observation planes, where interior
  values are P1-interpolated FEM nodal pressure and a ghost of the clipped
  half-volume gives context.

## 1. Decisions taken in this plan

1. **Fusion declares and WG meshes.** WGLink stays standard-library-only and
   never runs gmsh. That is unlike Fusion2Msh, which bundles a gmsh wheel. The
   reason is the ownership rule the protocol already enforces: physical tags
   belong to WG (`tag`, `tags` and `tag_map` are forbidden CAD keys). Fusion
   sends one STEP per region plus a geometric description of each grouped
   face, and WG matches those descriptions to its own surfaces.
2. **One solid body per region, hidden at Send.** Fusion exports STEP by
   visibility, so a visible air body would also land in `assembly.step` as
   exterior geometry. WGLink refuses that by name. It exports each region by
   copying the body into a temporary root component, which it deletes again.
3. **Groups are boundary conditions, not tags.** The four kinds are:
   - `moving`: a diaphragm, with a drive channel shared with painted sources
     of the same role;
   - `interface`: coupling to the exterior air;
   - `plane-wave-termination`;
   - `rigid`: a wall that needs local sizing.

   Every face in no group is a rigid wall.
4. **A lost face refuses; it never becomes a wall.** This holds on both sides:
   a group whose faces all disappeared in Fusion, and a declared face that
   matches no surface, or more than one, in WG. A lost diaphragm would solve
   to silence, and a lost interface would seal the chamber.
5. **The engine is the official BEAT worker,** through the contract-2
   `compiled_system` that Boundary Lab also uses. Both faces of one diaphragm
   belong to one ideal velocity source per drive channel.
6. **The display slices exactly.** Boundary Lab samples a grid and locates
   points in tetrahedra. WG cuts each crossed tetrahedron and interpolates
   along its edges, which is exact for P1 elements and leaves no gaps. Smooth
   and element modes, the ghost half-volume, hiding walls while cutting, and
   the role palette follow Boundary Lab.

## 2. The `fem-regions-v1` contract

`scope.fem_regions[]` is present exactly when the feature is required. Each
entry has:

| Field | Meaning |
|---|---|
| `region_id` | `[a-z0-9-]`, at most 32 bytes, unique |
| `name`, `object_id` | display name; the Fusion body; one region per body |
| `file` | a `files` member with purpose `fem-region`, holding exactly one solid |
| `n_bodies_expected` | always 1 |
| `mesh_size_mm` | region size; finer group sizes blend back over three region sizes |
| `medium` | `sound_speed_m_per_s`, `density_kg_per_m3`, `bulk_loss_factor` in 0..1 |
| `surface_groups[]` | `group_id`, `name`, `kind`, `mesh_size_mm`, `faces[]`, and `drive_channel_id` for `moving` only |
| `faces[]` | `area_mm2`, `centroid_mm`, `normal` (unit, planes only) or null, `bbox_mm` |

Both validation profiles call the shared `validate_fem_regions`. A return
whose only driver is a FEM diaphragm is drivable without painted sources.

WG names its physical groups as follows:

- each grouped surface: `wg-fem-v1|region=<id>|group=<id>|kind=<kind>`;
- the unclaimed walls: `group=_walls|kind=rigid` (the underscore is outside the protocol id pattern, so user groups cannot collide);
- the air volume: `wg-fem-v1|region=<id>|volume`.

Region artifacts are stored as `fem/region-<id>.msh`, retaining the declared id
in the record while avoiding reserved device filenames. The supervised child
measures group areas and triangle counts, checks every declared kind, and
requires named triangles to cover the complete tetrahedral boundary exactly
once. All regions in one ingest share a 600-second wall-clock budget; region
count and mesh density remain user-selected.

## 3. Slices

| Slice | Repository | State | What |
|---|---|---|---|
| F1 | wglink | done | `fem-regions-v1` protocol, shared validator, `wglink_fem.py` decisions |
| F2 | wglink | done | Declare FEM Region dialog, attributes, Send export, pre-flight, return-state hash |
| F3 | waveguide-generator | done | protocol mirror, bundle reader, gmsh tetrahedral mesher with face matching, isolated child task, opt-in capability |
| F4 | waveguide-generator | done | coupled `compiled_system` builder, validated by `beat_engine.beat_contract` |
| F5 | waveguide-generator | done | frontend exact slice, element mode, ghost, role colours, `wg-fem-field-v1` decoder, `FemFieldView` |
| F6 | waveguide-generator | open | ingest: mesh each region in the child at stage 7, retain region meshes, record `evidence.fem_regions`, list regions in the CAD Link panel |
| F7 | waveguide-generator | preparation implemented; live qualification pending | exterior closure: mesh the exterior with each interface patch conforming to the FEM interface, by fragmenting the shell with the region's interface face so OCC shares the rim |
| F8 | waveguide-generator | open | engine route: a coupled job through the official BEAT worker (`coupled_fem_bem_lem` / `interior_fem`), outputs mapped to the multi-channel result contract, the `fem_required` refusal lifted for declared regions, `femRegions` advertised by default |
| F9 | waveguide-generator | open | result route `/api/results/{id}/fem-field`, then mount `FemFieldView` with the field-plane gizmo, a frequency slider, system or channel response, phase animation, and a combined interior and exterior plane |
| F10 | both | open | electrodynamic drivers on FEM diaphragms (voltage ports, T/S parameters), aligned with [DRIVER-REAR-LOAD](DRIVER-REAR-LOAD.md)'s meshed front chambers; candidate: coupled front and rear chambers sharing one moving diaphragm |
| F11 | waveguide-generator | open | validation: sealed box against the lumped model; Boundary Lab's Simple_Sealed and compression_driver solved from the same meshes |
| F12 | wglink | open | live Fusion verification of the dialog and the temporary-component export in a parametric timeline; regions under reduced domains and non-root frames |

F6 to F9 must land in order. F10 and F11 can follow F8.

## 4. Open questions

1. Should `fem-air-volume-v1` (the `FEM_MF_AIR` component convention) be
   retired in favour of `fem-regions-v1`, or migrated by reading such a
   component as one region with no groups?
2. Does copying a hidden body into a temporary component leave timeline
   entries in a parametric design? If it does, export the region from a
   temporarily shown body instead. This needs a live Fusion measurement (F12).
3. Should F7 conform the exterior to the FEM interface (as written here), or
   remesh the interface patch and conform the FEM side? Boundary Lab treats
   the FEM facets as authoritative.
4. Should `femRegions` ship enabled in the release that includes F8, or only
   after F11's validation?

## F7 preparation and F8 artifact contract

F7 retains the existing exterior-only mesh at the record's top-level
`mesh_store_path`, with its existing cache, tags and digest. It does not replace
that mesh. Returns without regions take the same preparation path and produce
the same mesh bytes with the FEM flag on or off.

A return with interface groups additionally retains `record.coupled_exterior`:

- `mesh_store_path`, `mesh_cache_key`, `mesh_lookup_key`,
  `mesh_content_sha256` and `mesh_size_bytes` bind an immutable, independently
  verified `coupled/exterior.msh` artifact;
- `physical_names` maps surface names to their tags, entities, boundary `kind`
  and, for moving exterior patches, `drive_channel_id`;
- `normalisation_matrix` is the complete exterior solver transform, including
  vertical recentring; `stats.open_edges` is zero.

F8 reads this surface with `read_verified_coupled_exterior_mesh(record)` and
`read_coupled_exterior(text)`. The latter returns points in **millimetres** and
triangle groups, suitable for `ExteriorSurface(points, groups,
record["coupled_exterior"]["physical_names"])`. The original top-level mesh
continues to serve explicit exterior-only solves and existing viewport loading.
The solve-model digest includes the coupled surface digest as well as every
region mesh digest, medium and group declaration. No API or database schema
changes are needed.

For interface regions, `record.fem_regions[]` retains the F6 artifact fields
and the same complete `normalisation_matrix`. Their FEM meshes are extracted
from the coupled child, rather than independently meshed. Regions without an
interface continue through the F6 child and retain its mesh contract.

The isolated `coupled_mesh` task stages the assembly and every participating
region STEP as read-only inputs (including interior-only regions for overlap
and touching-boundary checks), verifies each member's size and digest on
both sides of the process boundary, and scans each STEP under existing budgets.
The child replays the exterior's winning OCC healing recipe and verifies its
geometry fingerprint. Region faces are matched in the export frame before
applying the same rigid rotation, translation and recentring operations as the
assembly. A missing recipe, fingerprint mismatch or transform mismatch refuses.

BooleanFragments supplies surface ancestry, including split region faces.
Painted assembly faces are copied as explicit fragment tools to preserve their
ancestry when OCC otherwise omits constituent-face maps. The exterior skin is
the boundary of material plus declared air: material/air interior faces are
excluded, exposed interface faces are included under their FEM physical names,
and free assembly wall sheets are retained. A rigid sheet coinciding with an
interface refuses rather than losing its closure. Nested closed skin components
refuse with their location and area: a sealed chamber does not border unbounded
exterior air and requires an explicit modelling decision. Touching regions
refuse naming both regions rather than acquiring a silent rigid partition.
In 0.3.6, two regions claiming their shared face as a `moving` group also
refuse, with a specific message naming both chambers and the diaphragm:
solving both sides of one diaphragm is not supported yet; declare one of the
two chambers. Supporting this compression-driver front/rear configuration is
a candidate for F10. A region fragmented into pieces by another body refuses
with a single-region message asking for one closed solid.
Painted sources covered wholly or partly by a region refuse; when a diaphragm
group owns the same faces, unpaint the source because the region's diaphragm
group drives it. Thus an aperture's OCC face is
shared by the FEM boundary and the BEM skin; its rim is joined to the material
by fragmentation. A buried interface, missing shared face, overlapping regions,
uncovered undeclared air face, open/nonmanifold skin or wrong interface winding
is a blocking stage-7 refusal naming the participating region and interface
group. The implicit `walls` group id may be declared only as rigid; declared
rigid walls merge with unclaimed walls rather than losing triangles. No independent interface triangulation or coordinate welding is used.

The child generates 2D once, then generates 3D with only air volumes visible.
It explicitly checks that no material volume gained tetrahedra. Extracted
meshes use first-order tagged Gmsh 2.2 ASCII with compact vertex arrays; retained
F6 meshes remain Gmsh 4.1. FEM triangles point away from their tetrahedra; each
connected exterior skin points into exterior air. Interface winding must agree
on both sides, with the same triangles and coordinates. Region and group sizes
are forwarded without mesh-size limits; existing child resource budgets apply.
The coupled skin uses the region/group mesh sizes and exterior rigid/source
sizes, without the exterior-only mesh's transition grading (`transition_mm`).
Finer region groups still blend back to their region size over three region sizes.

Any declared region under an applied or declared reduced domain refuses in
ingest. Ingest does not receive infinite-baffle or ground-plane solve settings;
their domain refusal is enforced at F8 job admission. A finite, explicitly
modelled baffle is supported. F8 must also refuse reduced domains at admission,
since a later solve setup can introduce them after ingest.

Qualification covers synthetic ported and finite-baffle boxes, closure,
interface topology and winding, the compiled-system contract, verified storage,
painted source ancestry, multiple interface regions, rotated/translated/recentred
frames, flag-off admission and named refusals.
The acoustic qualification remains opt-in and is run **through the compute
broker**, on CPU float64:

```bash
WG2_RUN_LIVE=1 WG2_FEM_REGIONS=1 python scripts/run_tests.py \
  server/tests/test_fem_coupled_beat_live.py::test_f7_open_box_radiates_the_pistons_monopole \
  -m live -q
```

Use the pinned verification environment and its qualified BEAT/Julia runtime.
For a manually provisioned runtime, the existing `WG2_TEST_BEAT_JULIA` and
`WG2_TEST_BEAT_DEPOT` overrides still apply. The test checks both observation
points against the monopole within 5%, confirms interface normal signs are +1,
and requires interior peak pressure between 1 and 100 Pa at 100 Hz for a
1 m/s diaphragm. This live test has not been run as part of F7 production.
