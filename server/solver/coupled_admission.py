"""Cheap declaration checks shared by FEM admission and request construction."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


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
                      combine_id: str | None = None) -> tuple[str, str] | None:
    moving: dict[str, list[str]] = {}
    for region in regions:
        for group in region.get("surface_groups", ()):
            if group.get("kind") != "moving":
                continue
            diaphragm = f"{region['region_id']}/{group['group_id']}"
            channel = group.get("drive_channel_id")
            if not channel:
                return "fem_missing_channel", f"FEM diaphragm {diaphragm!r} names no drive channel"
            moving.setdefault(channel, []).append(diaphragm)
    for channel, diaphragms in moving.items():
        if len(diaphragms) > 1:
            return "fem_multiple_diaphragms_unsupported", (
                f"Channel {channel!r}, regions/diaphragms {', '.join(diaphragms)}: "
                "more than one FEM diaphragm on one drive channel is not supported yet")
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
                        has_transducers: bool = False) -> tuple[str, str] | None:
    """One ordered declaration refusal for admission and request compilation.

    Feature availability, termination/exterior conflicts, domain restrictions,
    channel physics, passive synthesis, then drive coverage/media. Callers with
    already compiled boundaries can omit setup channels; their boundary-level
    coverage is checked by the coupled system builder.
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
        return fem_drive_problem(record.get("fem_regions", ()), {c.id: c.source_ids for c in channels},
                                 painted_sources=sources, combine_id=combine_id)
    return None
