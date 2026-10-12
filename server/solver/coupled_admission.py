"""Drive declaration and geometry checks shared by admission and compilation."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def fem_group_options_problem(regions: Sequence[Mapping[str, Any]]) -> tuple[str, str] | None:
    """Validate options again for retained records and direct solver callers."""
    from server.mesh.fem_regions import validate_group_options

    for region in regions:
        for group in region.get("surface_groups", ()):
            label = (f"FEM region {region.get('name') or region['region_id']!r} "
                     f"({region['region_id']!r}), group {group.get('name') or group['group_id']!r} "
                     f"({group['group_id']!r})")
            try:
                validate_group_options(group, label)
            except ValueError as exc:
                return "fem_group_options_invalid", str(exc)
    return None


def fem_termination_problem(regions: Sequence[Mapping[str, Any]], *,
                            has_exterior: bool = False) -> tuple[str, str] | None:
    """Refuse interior-only terminations when any region or source needs BEM."""
    if not has_exterior and not any(
        group.get("kind") == "interface"
        for region in regions for group in region.get("surface_groups", ())
    ):
        return None
    for region in regions:
        for group in region.get("surface_groups", ()):
            if group.get("kind") == "plane-wave-termination":
                return "fem_termination_with_exterior", (
                    f"FEM region {region.get('name') or region['region_id']!r} "
                    f"({region['region_id']!r}), group {group.get('name') or group['group_id']!r} "
                    f"({group['group_id']!r}): "
                    "A plane-wave termination is supported only in an interior-only solve. "
                    "Remove the termination group (make it rigid) to solve the chamber coupled, "
                    "or remove the painted sources and interfaces to solve the region interior-only, "
                    "or choose exterior-only.")
    return None


def fem_drive_problem(regions: Sequence[Mapping[str, Any]],
                      channels: Mapping[str, Sequence[str]], *,
                      painted_sources: set[str],
                      electrodynamic_channels: set[str] | frozenset[str] = frozenset(),
                      combine_id: str | None = None) -> tuple[str, str] | None:
    problem = fem_group_options_problem(regions)
    if problem:
        return problem
    moving: dict[str, list[str]] = {}
    moving_regions: dict[str, set[str]] = {}
    for region in regions:
        for group in region.get("surface_groups", ()):
            if group.get("kind") != "moving":
                continue
            diaphragm = f"{region['region_id']}/{group['group_id']}"
            channel = group.get("drive_channel_id")
            if not channel:
                return "fem_missing_channel", f"FEM diaphragm {diaphragm!r} names no drive channel"
            moving.setdefault(channel, []).append(diaphragm)
            moving_regions.setdefault(channel, set()).add(region["region_id"])
    for channel, diaphragms in moving.items():
        if len(moving_regions[channel]) > 1 and channel not in electrodynamic_channels:
            return "fem_multiple_diaphragms_unsupported", (
                f"Channel {channel!r}, regions/diaphragms {', '.join(diaphragms)}: "
                "an ideal source cannot drive FEM moving groups in more than one region; "
                "give this channel an electrodynamic driver to share a diaphragm between regions")
    driven = {channel for channel, sources in channels.items()
              if painted_sources.intersection(sources)} | set(moving)
    if combine_id in driven:
        return "fem_combine_channel_collision", f"Combine id {combine_id!r} collides with a compiled drive channel id"
    for channel, diaphragms in moving.items():
        if painted_sources and channel not in channels:
            return "fem_channel_mismatch", (
                f"FEM diaphragm {diaphragms[0]!r} uses channel {channel!r}, "
                f"which is absent from setup channels {sorted(channels)}")
    if not driven:
        return "fem_no_driven_surface", "The coupled system has no driven surface: no painted source or FEM diaphragm"
    missing = sorted(set(channels) - driven)
    if missing:
        return "fem_missing_channels", f"Coupled system has no moving surface for setup channels {missing}"
    media = {(r["medium"]["sound_speed_m_per_s"], r["medium"]["density_kg_per_m3"])
             for r in regions if "medium" in r}
    if len(media) > 1:
        return "fem_mixed_media", "Every FEM region must use the same speed of sound and density"
    return None


def painted_source_ids(record: Mapping[str, Any]) -> set[str]:
    sources = set(record.get("source_tags") or ())
    sources.update(s["id"] for s in record.get("sources", ()) if "id" in s)
    for name in (record.get("coupled_exterior") or {}).get("physical_names", ()):
        sources.update(part.split("=", 1)[1] for part in name.split("|")
                       if part.startswith("source_id="))
    return sources


def fem_request_problem(record: Mapping[str, Any], channels: Sequence[Any] | None = None, *,
                        ground: bool = False, passive_cardioid: bool = False,
                        combine_id: str | None = None, enabled: bool | None = None,
                        has_exterior: bool | None = None,
                        has_transducers: bool = False,
                        moving_meshes: Mapping[str, Any] | None = None,
                        inspect_sd_geometry: bool = True) -> tuple[str, str] | None:
    """One ordered declaration refusal for admission and request compilation.

    Feature availability, termination/exterior conflicts, domain restrictions,
    channel physics, passive synthesis, drive coverage/media, then chamber Sd
    (required declaration, double rear load, invalid geometry), then moving
    patch connectivity and cut-axis checks. Callers with
    already compiled boundaries can omit setup channels; their boundary-level
    coverage and Sd are checked by the coupled system builder.
    """
    from server.solver.imported import imported_domain_planes
    from server.solver.beat_adapter.coupled_transducers import channel_problem

    from server.platform.feature_flags import fem_regions_enabled

    if enabled is None:
        enabled = fem_regions_enabled()
    if not enabled:
        return "fem_required", "FEM regions are disabled by WG2_FEM_REGIONS; remove the override to enable them"
    sources = painted_source_ids(record)
    channels = tuple(channels) if channels is not None else None
    if has_exterior is None:
        has_exterior = bool(sources or any(c.source_ids for c in channels or ()))
    problem = fem_termination_problem(record.get("fem_regions", ()), has_exterior=has_exterior)
    if problem:
        return problem
    planes = imported_domain_planes(record)
    if any(p not in {"x0", "y0"} for p in planes):
        return "fem_domain_unsupported", "FEM regions support only x0 and y0 symmetry planes"
    if ground or record.get("infinite_baffle"):
        return "fem_domain_unsupported", "FEM regions require free space without a ground plane"
    for channel in channels or ():
        problem = channel_problem(channel)
        if problem:
            return problem
    if passive_cardioid:
        return "fem_drive_unsupported", ("FEM solves support ideal normal-velocity and electrodynamic "
                                         "voltage drives; passive-cardioid synthesis is unsupported")
    if channels is not None:
        problem = fem_drive_problem(record.get("fem_regions", ()), {c.id: c.source_ids for c in channels},
                                    painted_sources=sources, combine_id=combine_id,
                                    electrodynamic_channels={c.id for c in channels
                                                         if getattr(c, "exterior_transducer", None) is not None})
        if problem:
            return problem
        problem = driver_sd_problem(record, channels, moving_meshes=moving_meshes,
                                    inspect_geometry=inspect_sd_geometry)
        if problem:
            return problem
        if inspect_sd_geometry:
            return driver_surface_problem(record, channels)
    return None


def driver_surface_problem(record: Mapping[str, Any], channels: Sequence[Any], *,
                           exterior_only: bool = False, msh_text: str | None = None
                           ) -> tuple[str, str] | None:
    """Apply compilation's connected-patch and cut-axis rules at admission.

    Declaration-only callers have no retained meshes yet; compilation validates
    their concrete inputs. Retained artifacts are verified before inspection.
    Welded edges or coincident motion footprints identify one diaphragm across meshes.
    """
    import numpy as np

    from server.mesh.artifact import (ImportedMeshArtifactError, read_verified_coupled_exterior_mesh,
                                      read_verified_fem_region_mesh, read_verified_import_mesh)
    from server.mesh.fem_coupling import read_coupled_exterior
    from server.mesh.fem_regions import parse_physical_name, read_fem_region_domain
    from .beat_adapter.mesh import read_surface
    from .component_symmetry import DriverSymmetryError, infer_component_symmetry
    from .imported import imported_domain_planes

    drivers = {c.id: c.exterior_transducer for c in channels
               if getattr(c, "exterior_transducer", None) is not None}
    if not drivers:
        return None
    meshes: dict[str, dict[str, list]] = {channel: {} for channel in drivers}
    geometry_points = []

    def add(channel, mesh, points, faces):
        if channel in meshes and not len(faces):
            raise ValueError(f"group {mesh!r} has no faces in the prepared mesh")
        if channel in meshes and len(faces):
            meshes[channel].setdefault(mesh, []).append((faces, points[faces]))

    try:
        if not exterior_only:
            for region in record.get("fem_regions", ()):
                if not region.get("mesh_store_path"):
                    continue
                region_id = region["region_id"]
                domain = read_fem_region_domain(read_verified_fem_region_mesh(record, region_id), region_id)
                geometry_points.append(domain.points_mm * .001)
                assignments = {g["group_id"]: g.get("drive_channel_id")
                               for g in region.get("surface_groups", ()) if g.get("kind") == "moving"}
                present = {fields['group'] for name, faces in domain.surfaces.items()
                           if len(faces) and (fields := parse_physical_name(name))}
                for group in sorted(set(assignments) - present):
                    raise ValueError(f"group {group!r} has no faces in the prepared mesh")
                for name, faces in domain.surfaces.items():
                    fields = parse_physical_name(name) or {}
                    add(assignments.get(fields.get("group")), f"mesh:fem:{region_id}",
                        domain.points_mm * .001, faces)
        # An undriven exterior still contributes to coupled snapping tolerance.
        exterior_record = record.get("coupled_exterior") or {}
        if not exterior_only and exterior_record.get("mesh_store_path"):
            exterior_points, exterior_groups = read_coupled_exterior(read_verified_coupled_exterior_mesh(record))
            geometry_points.append(exterior_points * .001)
        else:
            exterior_points = exterior_groups = None
        sources = {source: c.id for c in channels for source in c.source_ids}
        if sources:
            if not exterior_only and record.get("coupled_exterior"):
                if exterior_points is None:
                    points, groups = read_coupled_exterior(read_verified_coupled_exterior_mesh(record))
                    geometry_points.append(points * .001)
                else:
                    points, groups = exterior_points, exterior_groups
                for name, faces in groups.items():
                    source = next((part.split("=", 1)[1] for part in name.split("|")
                                   if part.startswith("source_id=")), None)
                    add(sources.get(source), "exterior", points * .001, faces)
            elif msh_text is not None or record.get("mesh_store_path"):
                mesh = read_surface(msh_text if msh_text is not None else read_verified_import_mesh(record))
                geometry_points.append(mesh.points_m)
                for source, channel in sources.items():
                    tag = (record.get("source_tags") or {}).get(source)
                    add(channel, "exterior", mesh.points_m, mesh.faces[mesh.tags == tag])
        planes = imported_domain_planes(record)
        if any(p not in {"x0", "y0"} for p in planes):
            return "driver_symmetry_unsupported", "Drivers support only x0 and y0 symmetry planes"
        axes = tuple({"x0": 0, "y0": 1}[p] for p in planes)
        if geometry_points:
            points = np.concatenate(geometry_points)
            tolerance = max(1e-9, float(np.linalg.norm(np.ptp(points, axis=0))) * 1e-6)
            if not exterior_only:
                tolerance = min(1e-7, tolerance)
            for channel, selected in meshes.items():
                if not selected:
                    continue
                # Match compilation's plane snapping before inspecting edges.
                snapped = {}
                for mesh_id, groups in selected.items():
                    snapped[mesh_id] = []
                    for faces, corners in groups:
                        corners = corners.copy()
                        for axis in axes:
                            corners[:, :, axis][np.abs(corners[:, :, axis]) <= tolerance] = 0.
                        snapped[mesh_id].append((faces, corners))
                infer_component_symmetry(snapped, drivers[channel].motion_axis, axes,
                                         channel_id=channel)
    except ImportedMeshArtifactError as exc:
        return "ingest_mesh_unavailable", str(exc)
    except DriverSymmetryError as exc:
        return exc.code, str(exc)
    except ValueError as exc:
        return "driver_surface_invalid", str(exc)
    return None


def driver_sd_problem(record: Mapping[str, Any], channels: Sequence[Any], *,
                      moving_meshes: Mapping[str, Any] | None = None,
                      exterior_msh_text: str | None = None,
                      inspect_geometry: bool = True) -> tuple[str, str] | None:
    """Require declared chamber Sd; inspect geometry only for enabled chambers."""
    from server.mesh.artifact import ImportedMeshArtifactError
    from server.solver.beat_adapter.transducers import (
        DriverSdRequired, LumpedChamberDoubleRearLoad, chamber_sd_problem)

    for channel in channels:
        spec = getattr(channel, "exterior_transducer", None)
        chamber = getattr(spec, "lumped_sealed_rear_chamber", None)
        if spec is None or not getattr(chamber, "enabled", False):
            continue
        raw = spec.model_dump(mode="json")
        try:
            chamber_sd_problem(raw, channel.id)
            # Counting moving meshes needs only declarations. Exterior-only
            # geometry is checked during compilation, without an admission read.
            if not inspect_geometry or (moving_meshes is None and not record.get("fem_regions")):
                continue
            if moving_meshes is None and not _channel_moving_mesh_ids(record, channel):
                continue
            meshes = (moving_meshes[channel.id] if moving_meshes is not None
                      else _channel_moving_meshes(record, channel, exterior_msh_text=exterior_msh_text))
            validate_driver_sd_geometry(raw, meshes, spec.motion_axis, channel.id)
        except ImportedMeshArtifactError as exc:
            return "ingest_mesh_unavailable", str(exc)
        except (DriverSdRequired, LumpedChamberDoubleRearLoad) as exc:
            return exc.reason_code, exc.message
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return "driver_sd_geometry_invalid", (
                f"Channel '{channel.id}': cannot determine moving-face geometry: {exc}")
    return None


def validate_driver_sd_geometry(raw, meshes, axis, channel_id):
    """Apply chamber refusals before connectivity in every concrete mesh route.

    Completion changes only the later geometric cross-check. Chamber admission
    depends on declared Sd and unweighted projection signs, so it needs no
    symmetry inference (which also checks disconnected moving patches).
    """
    from server.solver.beat_adapter.transducers import chamber_sd_problem, resolve_driver_sd

    chamber_sd_problem(raw, channel_id)
    for mesh_id, boundaries in meshes.items():
        if not boundaries or any(not len(faces) for faces, _ in boundaries):
            raise ValueError(f"group {mesh_id!r} has no faces in the prepared mesh")
    if (raw.get("lumped_sealed_rear_chamber") or {}).get("enabled", False):
        resolve_driver_sd(raw, meshes, axis, channel_id)


def _channel_moving_mesh_ids(record, channel) -> set[str]:
    """Count moving meshes from declarations without reading or orienting them."""
    meshes = {f"fem:{region['region_id']}" for region in record.get("fem_regions", ())
              if any(g.get("kind") == "moving" and g.get("drive_channel_id") == channel.id
                     for g in region.get("surface_groups", ()))}
    if channel.source_ids:
        meshes.add("exterior")
    return meshes


def _channel_moving_meshes(record, channel, *, exterior_msh_text=None):
    """Read the same verified boundaries and tetrahedral normals as compilation."""
    import numpy as np
    from server.mesh.artifact import (read_verified_fem_region_mesh,
                                      read_verified_coupled_exterior_mesh, read_verified_import_mesh)
    from server.mesh.fem_regions import read_fem_region_domain, parse_physical_name
    from server.mesh.fem_coupling import read_coupled_exterior
    from server.solver.coupled_system import _fem_driver_triangles, _oriented_exterior_surfaces
    from server.solver.beat_adapter.mesh import read_surface

    from server.solver.imported import imported_domain_planes

    axes = tuple({"x0": 0, "y0": 1}[p] for p in imported_domain_planes(record))
    meshes = {}
    for region in record.get("fem_regions", ()):
        groups = {g["group_id"] for g in region.get("surface_groups", ())
                  if g.get("kind") == "moving" and g.get("drive_channel_id") == channel.id}
        if not groups:
            continue
        domain = read_fem_region_domain(read_verified_fem_region_mesh(record, region["region_id"]),
                                        region["region_id"])
        oriented = _fem_driver_triangles(domain)
        boundaries = []
        for name, faces in domain.surfaces.items():
            fields = parse_physical_name(name)
            if fields and fields.get("group") in groups:
                try:
                    triangles = np.asarray([oriented[tuple(sorted(face))] for face in faces])
                except KeyError as exc:
                    from server.solver.coupled_system import CoupledSystemError

                    raise CoupledSystemError(f"Diaphragm {name!r} is not on the tetrahedral boundary") from exc
                boundaries.append((faces, triangles))
        present = {fields['group'] for name, faces in domain.surfaces.items()
                   if len(faces) and (fields := parse_physical_name(name))}
        for group in sorted(groups - present):
            raise ValueError(f"group {group!r} has no faces in the prepared mesh")
        meshes[f"fem:{region['region_id']}"] = boundaries
    if channel.source_ids:
        if record.get("coupled_exterior"):
            points, groups = read_coupled_exterior(read_verified_coupled_exterior_mesh(record))
            for source in channel.source_ids:
                if not any(len(faces) and f"source_id={source}" in name.split('|') for name, faces in groups.items()):
                    raise ValueError(f"group {source!r} has no faces in the prepared mesh")
            boundaries = [(faces, points[faces] * .001) for name, faces in _oriented_exterior_surfaces(points, groups, symmetry_plane_axes=axes)
                          if any(f"source_id={source}" in name.split("|") for source in channel.source_ids)]
        else:
            mesh = read_surface(exterior_msh_text if exterior_msh_text is not None else read_verified_import_mesh(record))
            for source in channel.source_ids:
                if not np.any(mesh.tags == record['source_tags'][source]):
                    raise ValueError(f"group {source!r} has no faces in the prepared mesh")
            tags = [record["source_tags"][source] for source in channel.source_ids]
            faces = mesh.faces[np.isin(mesh.tags, tags)]
            boundaries = [(faces, mesh.points_m[faces])]
        if boundaries:
            meshes["exterior"] = boundaries
    return meshes
