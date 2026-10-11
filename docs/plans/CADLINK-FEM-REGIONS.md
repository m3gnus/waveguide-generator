# Coupled FEM/BEM solves of CAD Link geometry

Status: proposed, 2026-10-11. Slices F1 to F5 are implemented on topic
branches and tested; F6 onwards are open. Nothing is routed to a solver yet,
and `femRegions` is advertised only when `WG2_FEM_REGIONS=1`.

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
- the unclaimed walls: `group=walls|kind=rigid`;
- the air volume: `wg-fem-v1|region=<id>|volume`.

## 3. Slices

| Slice | Repository | State | What |
|---|---|---|---|
| F1 | wglink | done | `fem-regions-v1` protocol, shared validator, `wglink_fem.py` decisions |
| F2 | wglink | done | Declare FEM Region dialog, attributes, Send export, pre-flight, return-state hash |
| F3 | waveguide-generator | done | protocol mirror, bundle reader, gmsh tetrahedral mesher with face matching, isolated child task, opt-in capability |
| F4 | waveguide-generator | done | coupled `compiled_system` builder, validated by `beat_engine.beat_contract` |
| F5 | waveguide-generator | done | frontend exact slice, element mode, ghost, role colours, `wg-fem-field-v1` decoder, `FemFieldView` |
| F6 | waveguide-generator | open | ingest: mesh each region in the child at stage 7, retain region meshes, record `evidence.fem_regions`, list regions in the CAD Link panel |
| F7 | waveguide-generator | open | exterior closure: mesh the exterior with each interface patch conforming to the FEM interface, by fragmenting the shell with the region's interface face so OCC shares the rim |
| F8 | waveguide-generator | open | engine route: a coupled job through the official BEAT worker (`coupled_fem_bem_lem` / `interior_fem`), outputs mapped to the multi-channel result contract, the `fem_required` refusal lifted for declared regions, `femRegions` advertised by default |
| F9 | waveguide-generator | open | result route `/api/results/{id}/fem-field`, then mount `FemFieldView` with the field-plane gizmo, a frequency slider, system or channel response, phase animation, and a combined interior and exterior plane |
| F10 | both | open | electrodynamic drivers on FEM diaphragms (voltage ports, T/S parameters), aligned with [DRIVER-REAR-LOAD](DRIVER-REAR-LOAD.md)'s meshed front chambers |
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
