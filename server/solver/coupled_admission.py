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
                    "Remove the painted sources and interfaces, or model the horn as exterior.")
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
