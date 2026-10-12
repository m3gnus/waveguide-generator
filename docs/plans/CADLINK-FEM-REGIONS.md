# Coupled FEM/BEM solves of CAD Link geometry

Status: implemented through F9, 2026-10-12. Declared FEM regions and the
interior field view are visible by default in the Fusion CAD Link workflow,
without an Experimental label. Live acoustic qualification remains pending.

`WG2_FEM_REGIONS=0` is the operator kill switch; `false`, `no` and `off`
(case-insensitive, with surrounding whitespace ignored) also disable the feature.
Unset or empty enables it. The shared flag gates WGLink's static `femRegions`
protocol capability, bundle admission, isolated `fem_region`/`coupled_mesh` tasks,
engine eligibility, the public field route and the frontend's region list and
field marker. A disabled receiver refuses declared regions before inspecting
or meshing STEP. It never silently drops FEM physics. The protocol stays
flag-independent, and Onshape and Fusion document capture do not control FEM.
Job admission separately requires an available official BEAT CPU engine;
unsupported providers and unavailable engines refuse clearly.

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
   `compiled_system` that Boundary Lab also uses. One painted exterior front and one FEM rear diaphragm may share
   one ideal velocity source per drive channel. Two FEM moving groups on the
   same channel are refused in 0.3.6 because positive FEM load weights would
   make both chambers breathe rather than move as a piston.
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
| F3 | waveguide-generator | done | protocol mirror, bundle reader, gmsh tetrahedral mesher with face matching, isolated child task, default-on capability with kill switch |
| F4 | waveguide-generator | done | coupled `compiled_system` builder, validated by `beat_engine.beat_contract` |
| F5 | waveguide-generator | done | frontend exact slice, element mode, ghost, role colours, `wg-fem-field-v1` decoder, `FemFieldView` |
| F6 | waveguide-generator | done | ingest: mesh each region in the child at stage 7, retain region meshes, record `evidence.fem_regions`, list regions in the CAD Link panel |
| F7 | waveguide-generator | preparation implemented; live qualification pending | exterior closure: mesh the exterior with each interface patch conforming to the FEM interface, by fragmenting the shell with the region's interface face so OCC shares the rim |
| F8 | waveguide-generator | CPU route implemented; qualification pending | official CPU float64 coupled jobs (`coupled_fem_bem_lem` / `interior_fem`), multi-channel results and FEM field sidecars; declared regions solve by default; explicit kill switch remains |
| F9 | waveguide-generator | interior field implemented | result route `/api/results/{id}/fem-field`, then mount `FemFieldView` with the field-plane gizmo, a frequency slider, system or channel response, phase animation, interior slice plane (combined exterior display remains follow-up) |
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

Plane-wave terminations are interior-only. A termination combined with a
painted exterior source or an interface in any region is a blocking ingest
refusal before meshing. Job admission also refuses when setup drive channels
introduce an exterior; request construction enforces the same backstop. Each
refusal names the termination's region and group and asks to remove painted
sources and interfaces, or model the horn as exterior. An interior-only tube
with no painted sources, setup drive channels or interfaces remains supported.

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

## F8 engine and field sidecar contract

By default, declared regions use the official BEAT CPU backend
with float64 and contract 2. Auto skips other engines for this feature, including
in Accurate. Explicit exterior-only solves retain the original surface and
solver route. Legacy required `fem_air_volumes` remain refused unless explicitly
excluded. Ideal normal-velocity FEM diaphragms use their declared channel ids;
a diaphragm still produces its own channel only when the design has no painted
exterior sources; next to painted sources it must use one of their channels.
`validation_diagnostics` is sent as false, which in the pinned engine also selects
the statically condensed (Schur) solver; interior nodal pressure is still
reconstructed.
Reduced domains, ground planes, HBB and other backends refuse.

**0.3.6 limitation:** the contract-2 coupled route supports ideal normal-velocity
drives only. Any `exterior_transducer`, legacy driver, patch weights, axial
motion or passive-cardioid campaign refuses, including painted exterior
transducers on channels separate from the FEM diaphragm. Painted ideal exterior
sources keep working; painted electrodynamic exterior transducers require an
explicit exterior-only solve until a coupled electrodynamic route is supported.
With painted sources present, each FEM diaphragm channel must exist in the
setup; renaming, regrouping or skipping the front source cannot silently create
a separate rear driver. Designs without painted sources retain FEM-only channel
ids. Combine output ids must differ from all compiled drive channel ids.

The coupled route shares the exterior route's fixed regular quadrature and
order 4 regular/singular integration. Native `validation_diagnostics` is
explicitly false for production jobs: its additional validation matrices are
costly and belong to qualification requests; result identity, precision and
field layout checks remain enabled.

`JobStore.store_fem_field_arrays` streams compression from the stacked pressure
array into staging on a worker thread, alongside channel-basis serialization.
`JobStore.store_fem_field` also accepts existing packed bytes. Both publish `fem-field.npz` atomically in the job's
field artifact directory. `get_fem_field` reads its bytes. The existing
`task_metadata_json` carries `has_fem_field_artifact` and
`fem_field_artifact_bytes`; neither database schema changes. Failure,
cancellation, interrupted-job recovery, job deletion and result retention remove
the sidecar. Rated results retain it, and disabling automatic result cleanup
retains it with completed results. F9 owns the public route and display.

Older retained sidecars may include an `experimental` scalar; readers ignore
it. New sidecars and public field/result metadata omit that label.

The NPZ is readable with `numpy.load(..., allow_pickle=False)`:

- `schema`: scalar string `wg-fem-field-store-v1`;
- `phasor_convention`: scalar string `exp(-i omega t)`;
- `drive_normalization`: scalar string `unit_acceleration_m_per_s2`;
- `frequencies_hz`: float64 frequency axis in execution order;
- `channel_ids`: string drive-channel ids, in excitation order;
- `region_ids`: string region ids, in compiled FEM-domain order;
- `pressure`: complex128, shape `(frequency, channel, total_fem_nodes)`, Pa
  per unit acceleration. Nodes concatenate in `region_ids` order;
- `points_mm_<r>`: float64 `(node, 3)` coordinates for region index `r`;
- `tetrahedra_<r>`: integer `(tetrahedron, 4)`, zero-based local node indices;
- `surface_names_<r>`: physical names in surface order;
- `surface_triangles_<r>_<s>`: integer `(triangle, 3)`, zero-based local node
  indices for surface index `s` in region `r`.

To make `wg-fem-field-v1`, F9 selects a frequency and channel, slices `pressure`
using cumulative region node counts, and encodes the selected domain coordinates,
indices, and pressure real/imaginary parts as the existing little-endian
float32/uint32 decoder expects. The sidecar preserves float64/complex128;
conversion to float32 belongs only to the display payload. Exterior observation
pressure and FEM pressure share the same acceleration scaling. Exterior field
traces are not retained for coupled results.

An exterior-only choice saved in a setup or frontend profile records the
excluded region ids. Binding a later snapshot with a different FEM set resets
that choice; old unbound choices also reset when regions are present. Snapshots
without regions keep their existing exterior-only behavior.

The opt-in managed CPU bridge qualification runs through the compute broker:

```bash
WG2_RUN_LIVE=1 WG2_FEM_REGIONS=1 WG2_BEAT_PROVIDER=official python scripts/run_tests.py \
  server/tests/test_fem_coupled_beat_live.py::test_f8_managed_cpu_bridge_maps_f7_fields \
  -m live -q
```

It requires a WG-provisioned CPU runtime and checks the F7 skin through managed
session admission, contract-2 output mapping, polar pressure against the
monopole within 5%, and duct-scale interior pressure. The broker owns native
solver qualification.

## F9 field-view request costs and follow-up

The FEM frequency/channel selection waits for 250 ms of idle time before
requesting a new field. The slider updates immediately; superseded timers and
browser requests are cancelled, and obsolete responses cannot become the
displayed selection. Browser cancellation cannot stop server decompression
that has already started.

Each request decompresses the selected pressure slice from the compressed NPZ
sidecar. For C-order storage, skipping to a later slice still inflates all
earlier bytes in that ZIP member; it saves memory, not decompression work.
A 600 MB field can cost roughly 1–3 seconds of CPU per request, depending on
the selected slice and compression. Every frequency/channel response also
resends the full domain topology (coordinates, tetrahedra and surface
triangles), although it is unchanged between selections.

A follow-up should split topology from pressure so the frontend can retain
the topology once per artifact, and use seekable per-slice pressure storage
to read a selection without inflating earlier frequencies or channels.

## F-T exterior driver options and F10a rear loading

Exterior transducer v2 accepts optional positive `power_w` and `xmax_mm`
ratings for the existing power and excursion limits. BEAT Pmax channels use
constant nominal V²/Re′ when semi-inductance is enabled (V²/Re otherwise),
instead of per-frequency real terminal power. The impedance plot still allows
real terminal power to be calculated. Ratings stay in result metadata and do
not enter the engine's driver parameters; absent options retain v1 bytes.

Source tags currently have no front/rear label. Connected moving patches
are treated as diaphragm sides: local back-facing folds subtract from each
patch's net signed projected area. Single-sided Sd is its absolute net
projection. The shared area helper averages only explicitly supplied front
and rear sides and warns when they differ by more than 10%. Exterior patches
with opposing net projections radiate both sides and refuse an enabled lumped
sealed rear chamber: a lumped rear chamber replaces the rear radiation;
untag the rear faces or disable the chamber. A front-only diaphragm on a
closed body may use this unmeshed rear load.

F10a must refuse an enabled lumped rear chamber when a FEM rear region is
attached to the same diaphragm. The FEM rear load and the lumped stiffness
must never be applied together.
