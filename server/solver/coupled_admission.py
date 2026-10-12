"""Cheap declaration checks shared by FEM admission and request construction."""
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
            moving_regions.setdefault(channel, set()).add(region['region_id'])
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
    (required declaration, double rear load, invalid geometry). Callers with
    already compiled boundaries can omit setup channels; their boundary-level
    coverage and Sd are checked by the coupled system builder.
    """
    from server.solver.imported import imported_domain_planes
    from server.solver.beat_adapter.coupled_transducers import FEM_DRIVER_SYMMETRY_MESSAGE, channel_problem

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
    if planes and (has_transducers or any(getattr(c, "exterior_transducer", None) is not None for c in channels or ())):
        return "fem_domain_unsupported", FEM_DRIVER_SYMMETRY_MESSAGE
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
        if planes and problem and problem[0] == "fem_multiple_diaphragms_unsupported":
            return problem[0], f"{problem[1]}. {FEM_DRIVER_SYMMETRY_MESSAGE}"
        if problem:
            return problem
        return driver_sd_problem(record, channels, moving_meshes=moving_meshes,
                                 inspect_geometry=inspect_sd_geometry)
    return None


def driver_sd_problem(record: Mapping[str, Any], channels: Sequence[Any], *,
                      moving_meshes: Mapping[str, Any] | None = None,
                      exterior_msh_text: str | None = None,
                      inspect_geometry: bool = True) -> tuple[str, str] | None:
    """Require declared chamber Sd; inspect geometry only for enabled chambers."""
    from server.solver.beat_adapter.transducers import (
        DriverSdRequired, LumpedChamberDoubleRearLoad, chamber_sd_problem, resolve_driver_sd)

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
            resolve_driver_sd(raw, meshes, spec.motion_axis, channel.id)
        except (DriverSdRequired, LumpedChamberDoubleRearLoad) as exc:
            return exc.reason_code, exc.message
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return "driver_sd_geometry_invalid", (
                f"Channel '{channel.id}': cannot determine moving-face geometry: {exc}")
    return None


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
        meshes[f"fem:{region['region_id']}"] = boundaries
    if channel.source_ids:
        if record.get("coupled_exterior"):
            points, groups = read_coupled_exterior(read_verified_coupled_exterior_mesh(record))
            boundaries = [(faces, points[faces] * .001) for name, faces in _oriented_exterior_surfaces(points, groups)
                          if any(f"source_id={source}" in name.split("|") for source in channel.source_ids)]
        else:
            mesh = read_surface(exterior_msh_text if exterior_msh_text is not None else read_verified_import_mesh(record))
            tags = [record["source_tags"][source] for source in channel.source_ids]
            faces = mesh.faces[np.isin(mesh.tags, tags)]
            boundaries = [(faces, mesh.points_m[faces])]
        if boundaries:
            meshes["exterior"] = boundaries
    return meshes
