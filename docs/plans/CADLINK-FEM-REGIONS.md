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
Job admission separately requires an available official BEAT engine;
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
   one ideal velocity source per drive channel. Two FEM moving groups on an
   ideal channel are refused in 0.3.6 because positive FEM load weights would
   make both chambers breathe rather than move as a piston. An electrodynamic
   channel uses the rigid translation axis and may own moving faces in two
   separate meshes; their absolute net projections are averaged for Sd.
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
| `lining` | Optional on a rigid group: `model` (`miki`), `thickness_mm`, `flow_resistivity_pa_s_per_m2` |
| `relative_level_db` | Optional on a moving group: relative motion level in dB; defaults to 0 |
| `faces[]` | `area_mm2`, `centroid_mm`, `normal` (unit, planes only) or null, `bbox_mm` |

Both validation profiles call the shared `validate_fem_regions`. A return
whose only driver is a FEM diaphragm is drivable without painted sources.

Until the lining and motion-weight slices land, this WG build refuses any
declared `lining` with `fem_lining_unsupported` and any nonzero
`relative_level_db` with `fem_motion_weight_unsupported`, before meshing or
solve admission. An explicit relative level of 0 dB is accepted. The refusal
names every offending region and group: remove the lining (or set the relative
level to 0 dB) on these groups in WGLink and send again.

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
| F8 | waveguide-generator | CPU and GPU routes implemented; GPU qualification pending | official CPU float64 and Metal/CUDA/ROCm float32 coupled jobs (`coupled_fem_bem_lem` / `interior_fem`), multi-channel results and FEM field sidecars; declared regions solve by default; explicit kill switch remains |
| F9 | waveguide-generator | interior field implemented | result route `/api/results/{id}/fem-field`, then mount `FemFieldView` with the field-plane gizmo, a frequency slider, system or channel response, phase animation, interior slice plane (combined exterior display remains follow-up) |
| F10 | both | full-model drivers implemented; symmetry pending | electrodynamic drivers on FEM diaphragms (voltage ports, T/S parameters), aligned with [DRIVER-REAR-LOAD](DRIVER-REAR-LOAD.md)'s meshed front chambers; candidate: coupled front and rear chambers sharing one moving diaphragm |
| F11 | waveguide-generator | CPU float64 reference validated; GPU qualification pending | validation: sealed box against the lumped model; Boundary Lab's Simple_Sealed and compression_driver solved from the same meshes |
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

## Symmetry

The existing exterior contract is the symmetry authority. WGLink's
`reduced-domain-v1` declares `assembly.domain` with `cut_planes` (`x0`, `y0`
or both), a matching half/quarter kind and `declared_by`. An author-cut return
is verified as an open exterior skin in that domain. For full returns, WG's
`auto_cut_occ_geometry` mirror-tests both geometry and surface roles and
intersects the accepted positive half-spaces. The stored
`symmetry.domain_planes` includes author and WG cuts; `cut_planes` alone only
lists WG cuts. Mesh verification permits free edges only on those planes.

`beat_adapter/request.py::_image_frame` maps WG `yz` to BEAT `x`, `yz+xz` to
`xy`, and rotates a WG `xz` (y-only) half to BEAT `x` by a proper rotation.
Mesh coordinates and observation points use the same image frame. BEAT
evaluates reflected exterior images itself; neither pressure nor SPL receives
an additional factor of two or four. FEM previously refused any reduced domain
in intake, job admission and `coupled_request.py`.

The supplied parity research cites Boundary Lab's
[Coupled Solver documentation](https://github.com/m3gnus/boundary-lab/blob/HEAD/docs/Coupled%20Solver.md)
and [Interior FEM Solver documentation](https://github.com/m3gnus/boundary-lab/blob/HEAD/docs/advanced/Interior%20FEM%20Solver.md):
both support off/x/xy, with symmetry-aware interfaces. Completion and driver
orbit are inferred from moving-surface perimeters on the planes. A fresh
read-only `gh api` documentation lookup was attempted for this slice but failed
because GitHub was unreachable; these Boundary Lab findings are therefore
from the supplied documentation summary, not independently reverified here.
No Boundary Lab implementation was read or copied.

The pinned BEAT Python contract and `julia_local/BeatEngineCompiledDriver.jl`
support off/x/xy for `coupled_fem_bem_lem` and `interior_fem`. They snap and
validate every FEM and BEM vertex in the positive fundamental domain. FEM
symmetry faces require no load: rigid boundaries give natural Neumann
conditions. Coupled interfaces retain a one-to-one facet and vertex map,
including vertices on the planes. Electrodynamic completion is separately
checked: factors and orbit counts are 1, 2 or 4; completion is
`2**len(fractional_symmetry_axes)` and completion times orbit must equal the
image count. The motion axis lies in every cutting plane. F-S2 owns those
driver rules; F-S1 keeps ideal normal-velocity sources unchanged.

FEM reuses the exterior's accepted planes and positive half-space OCC cut.
Material, air volumes and interface faces are fragmented in one OCC session,
then only the fundamental domain is meshed. Generated FEM plane faces are
`group=_symmetry|kind=rigid`, distinct from `_walls`. The BEM skin excludes
plane caps and may have open edges only on active planes. Full region geometry
must mirror with its boundary roles before a WG cut; author-cut regions must
already lie in the positive domain. Ground planes and infinite baffles remain
unsupported for FEM in 0.3.6.

The lossless FEM sidecar retains the solved half/quarter topology and scalar
pressure in WG coordinates. The field response completes that topology by
reflection for display (with reversed tetrahedron/triangle winding for each
reflection); pressure is mirrored without rescaling. The F9 viewer thus shows
the complete symmetric field while solves retain reduced meshes. Exterior
results use the existing acceleration scaling and SPL convention unchanged.

The opt-in full-versus-reduced CPU far-field qualification is written but must
be run through the compute broker (complex pressure within 1% for x and xy):

```bash
WG2_RUN_LIVE=1 WG2_FEM_REGIONS=1 python scripts/run_tests.py \
  server/tests/test_fem_symmetry.py::test_live_full_and_reduced_box_far_field_agree_within_one_percent \
  -m live -q
```


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
  vertical recentring; `stats.open_edges` is zero in full domains and may count
  only symmetry-plane edges in reduced domains.

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

Declared regions follow the applied exterior x0/y0 reduced domain as described
in Symmetry above. The region boundary roles must mirror on WG-cut planes;
author-cut regions must already lie in the positive fundamental domain.
Ingest does not receive infinite-baffle or ground-plane solve settings;
their domain refusal is enforced at F8 job admission. A finite, explicitly
modelled baffle is supported. The solve uses the stored mesh's symmetry,
including the same observation frame and image mode as exterior-only solves.

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
broker**, on the CPU float64 reference:

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

Declared regions use the official BEAT compiled-system contract 2. Auto selects
BEAT CPU float64 in both Fast and Accurate. Explicit BEAT CPU also uses float64
for every drive; explicit Metal, CUDA and ROCm use float32. Interior-only systems
always run on CPU float64, recording the requested engine and the reroute reason.
Explicit exterior-only solves retain the original surface and solver route.
Legacy required `fem_air_volumes` remain refused unless explicitly excluded.
Ideal normal-velocity FEM diaphragms use their declared channel ids, including
FEM-only channels alongside painted sources. `validation_diagnostics` is sent
as false, which in the pinned engine also selects the statically condensed
(Schur) solver; interior nodal pressure is still reconstructed.
Ground planes and infinite baffles refuse. Ideal drives accept off/x/xy symmetry
on the official BEAT backends.

Electrodynamic voltage drives are supported on full models, including FEM
and painted exterior diaphragms and mixed ideal/voltage channels. Driver symmetry
is pending and reduced models with drivers refuse clearly. Legacy driver models,
patch weights, ideal axial motion and passive-cardioid campaigns remain unsupported.

Interface outputs include area-weighted complex normal velocity and radiated
pressure on the polar cuts, per drive channel. Radiation phasors use lossless
base64 complex128 arrays (little endian, C order, frequency by observation);
observation points are shared by all sources in the ports block. The represented
area is the reduced patch area. `full_model_area_m2` is the area of that port
and all its symmetry images, not the area of the whole model. The symmetry
multiplier is recorded beside it, and port radiation includes symmetry images,
including off-plane ports. Packed arrays require base64, complex128, little
endian, C order and exactly frequencies × angles × 16 decoded bytes; an invalid
array suppresses the complete ports block with a one-line display note.

Port pressure storage scales as frequencies × planes × angles × sources,
with 16 raw bytes per complex128 sample and 4/3 overhead for base64. At 721
angles × 3 planes × 200 frequencies, each source column uses about 9.2 MB
of base64 text. One interface, other exterior sources and total pressure are
three columns, about 28 MB per drive channel (before JSON overhead). More
interfaces add a column each.
Other exterior radiation is shown only when the engine supplies that source
column. Combined results refer users to the individual drive channels for ports.
Voltage drives retain RMS input power and current, with the reference voltage
in the result metadata. The normal exterior sphere pressure still supplies DI
and balloon views; per-source interface radiation does not replay the sphere.
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

- `schema`: scalar string `wg-fem-field-store-v2`; older v1 ideal-drive
  sidecars remain readable;
- `phasor_convention`: scalar string `exp(-i omega t)`;
- `drive_normalization`: per-channel string array, `unit_acceleration` or
  `voltage_rms`;
- `transducer_reference_voltage_v`: optional scalar RMS reference voltage
  for electrodynamic drives;
- `frequencies_hz`: float64 frequency axis in execution order;
- `channel_ids`: string drive-channel ids, in excitation order;
- `region_ids`: string region ids, in compiled FEM-domain order;
- `symmetry_planes`: optional string array of WG `x0`/`y0` planes; absent in
  older full-domain sidecars. Display mirrors these planes in WG coordinates;
- `pressure`: complex128 after drive normalization, shape
  `(frequency, channel, total_fem_nodes)`, Pa per unit acceleration for ideal
  drives or at the reference RMS voltage for electrodynamic drives. Nodes
  concatenate in `region_ids` order;
- `points_mm_<r>`: float64 `(node, 3)` coordinates for region index `r`;
- `tetrahedra_<r>`: integer `(tetrahedron, 4)`, zero-based local node indices;
- `surface_names_<r>`: physical names in surface order;
- `surface_triangles_<r>_<s>`: integer `(triangle, 3)`, zero-based local node
  indices for surface index `s` in region `r`.

To make `wg-fem-field-v1`, F9 selects a frequency and channel, slices `pressure`
using cumulative region node counts, and encodes the selected domain coordinates,
indices, and pressure real/imaginary parts as the existing little-endian
float32/uint32 decoder expects. The sidecar stores normalized complex128 pressure
and a float64 frequency axis; native GPU outputs are complex64 and the display
payload uses float32. Exterior
observation and FEM pressure share the same drive normalization. Exterior field
traces are not retained for coupled results.

An exterior-only choice saved in a setup or frontend profile records the
excluded region ids. Binding a later snapshot with a different FEM set resets
that choice; old unbound choices also reset when regions are present. Snapshots
without regions keep their existing exterior-only behavior.

The opt-in managed CPU bridge qualification runs through the compute broker:

```bash
WG2_RUN_LIVE=1 WG2_FEM_REGIONS=1 python scripts/run_tests.py \
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

Exterior transducer v2 accepts optional positive `sd_m2`, `power_w` and `xmax_mm`
ratings for the existing power and excursion limits. BEAT Pmax channels use
constant nominal V²/Re′ when semi-inductance is enabled (V²/Re otherwise),
instead of per-frequency real terminal power. The impedance plot still allows
real terminal power to be calculated. Ratings stay in result metadata and do
not enter the engine's driver parameters; absent options retain v1 bytes.

Driver options expose **Sd** in cm² and store it as `sd_m2` in SI m². The
selected library driver's `Sd_cm2` prefills the field; users may edit it. A
spec declaring Sd requires v2; absent options preserve v1 serialization.
Declared Sd alone controls sealed-chamber stiffness Kbox = ρc²Sd²/V and
acoustic impedance normalization Z/(ρcSd). Results record `sd_m2`,
`sd_source` (`declared` or `geometry`), `geometric_sd_m2` and
`geometry_ambiguous` per channel. An undetermined Sd has null value/source.

Geometry is a prefill/fallback and cross-check, never a front/rear classifier.
All the channel's moving faces in each mesh contribute their signed net
projection; Sd is its absolute value. Separate FEM regions, or FEM plus
exterior, retain separate meshes and use the mean of their absolute nets,
with a per-channel warning above 10% difference. Connected surround folds
subtract without being reclassified as another side. Declared Sd remains in
use even for ambiguous geometry; a geometric difference above 10% of the
declared value produces a per-channel result warning.

Without declared Sd, geometry is ambiguous if any mesh's absolute net is
less than 20% of its sum of absolute face projections, or a minority
opposite-sign connected patch has at least 5% of the majority patch's
absolute net. Numerically neutral rim faces do not join independent skins.
This catches thick cones, shelled domes, closed moving skins and partly
tagged rear faces. An enabled lumped chamber refuses at admission and
compilation with `driver_sd_required` and asks for Sd for the named channel.
With no chamber the solve continues, omits acoustic impedance and records
“Sd not determined; enter Sd to get acoustic impedance”.

F10a must refuse an enabled lumped rear chamber when a FEM rear region is
attached to the same diaphragm. The FEM rear load and the lumped stiffness
must never be applied together.
