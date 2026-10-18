"""Recompute a completed job's combined channel from persisted bases.

The solve-time combine and this module package the channel through the same
``package_combined_channel`` step (``combined_channel.py``), so they produce
contract-identical channels; this one trades the live solver state for the
``simulation_channel_bases`` NPZ, so changing a crossover after the solve is a
millisecond-scale numpy recombination instead of a re-solve.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, Mapping

from server.jobs.models import (
    ChannelCombineSpec,
    ImportedGeometrySource,
    SolveRequest,
)

from .acoustics import solver_sound_speed_m_per_s
from .combine import deserialize_channel_bases
from .combined_channel import package_combined_channel
from .errors import RecombineError
from .power_qualification import annotate_results


def recombine_stored_results(
    results: Mapping[str, Any],
    bases_npz: bytes,
    spec: ChannelCombineSpec,
    request: SolveRequest,
) -> dict[str, Any]:
    """Return a copy of ``results`` with the ``spec.id`` channel recomputed."""

    geometry = request.geometry
    if not isinstance(geometry, ImportedGeometrySource):
        raise RecombineError("recombination applies to imported multi-channel jobs")
    bundle = deserialize_channel_bases(bases_npz)
    channel_ids = bundle["channel_ids"]
    unknown = [name for name in spec.members if name not in channel_ids]
    if unknown:
        raise RecombineError(f"combine members name unknown drive channels: {unknown}")
    if spec.id in channel_ids:
        raise RecombineError(
            f"combine id {spec.id!r} collides with a solved drive channel id"
        )
    frequencies = bundle["frequencies_hz"]
    band = (float(frequencies[0]), float(frequencies[-1]))
    outside = [
        value
        for value in spec.corner_frequencies_hz()
        if value < band[0] or value > band[1]
    ]
    if outside:
        raise RecombineError(
            f"combine crossovers_hz {outside} lie outside the solved band "
            f"[{band[0]:g}, {band[1]:g}] Hz"
        )

    channels = dict(results.get("channels") or {})
    member_channel = channels.get(spec.members[0])
    member_metadata: Mapping[str, Any] = (
        member_channel.get("metadata") if isinstance(member_channel, Mapping) else None
    ) or {}

    envelope_metadata = results.get("metadata")
    envelope_metadata = envelope_metadata if isinstance(envelope_metadata, Mapping) else {}
    validity = envelope_metadata.get("per_source_frequency_validity")
    member_roles: dict[str, str | None] = {}
    for member in spec.members:
        stored = channels.get(member)
        stored_metadata = stored.get("metadata") if isinstance(stored, Mapping) else None
        member_roles[member] = (
            stored_metadata.get("role") if isinstance(stored_metadata, Mapping) else None
        )

    motions = {channel.motion for channel in geometry.drive_channels}
    config_motion = next(iter(motions)) if len(motions) == 1 else "normal"
    quadrants_raw = member_metadata.get("quadrants")
    quadrants = int(quadrants_raw) if isinstance(quadrants_raw, (int, float)) else 1234
    observation = member_metadata.get("observation")
    observation = observation if isinstance(observation, Mapping) else {}
    distance_raw = observation.get("requested_distance_m")
    distance = (
        float(distance_raw)
        if isinstance(distance_raw, (int, float))
        else float(request.options.polar_config.distance)
    )
    origin = str(observation.get("observation_origin") or "throat")
    config = SimpleNamespace(
        observation=SimpleNamespace(
            distance_m=distance,
            origin=origin,
            sphere_grid=object() if bundle["has_balloon"] else None,
        )
    )
    # The solve recorded the c its engine ran at; a member channel solved by
    # BEAT or BEMPP must not be renormalized against Metal's constant.
    speed_raw = observation.get("sound_speed_m_per_s")
    sound_speed = (
        float(speed_raw)
        if isinstance(speed_raw, (int, float))
        else solver_sound_speed_m_per_s("hornlab_metal_bem")
    )

    def stored_metadata(_combined_result: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "solver_backend": member_metadata.get("solver_backend", "metal"),
            "solver_mode": member_metadata.get("solver_mode", "full_3d"),
            "engine": member_metadata.get("engine", "hornlab-metal-bem"),
            "recombined": True,
            "performance": {},
        }
        # These describe the solve that produced the bases, including the
        # runtime's per-channel enrichment. Recombination cannot reconstruct
        # its frame/config.
        for copied in (
            "device_interface", "mesh_validation", "metal", "beat", "bempp",
            "solver_engine", "observation_frame_basis", "solve_execution", "cad_identity",
        ):
            value = member_metadata.get(copied)
            if isinstance(value, Mapping):
                metadata[copied] = dict(value)
        # Live combined builders omit fields describing just one member's drive.
        for backend, member_fields in (
            ("metal", ("solver_log",)),
            ("beat", ("merged_source_tags", "axially_reversed_source_tags", "solver_log")),
            ("bempp", ("velocity_source_tags", "source_motion", "solver_log")),
        ):
            for field in member_fields:
                if backend in metadata:
                    metadata[backend].pop(field, None)
        return metadata

    channels[spec.id] = package_combined_channel(
        spec=spec,
        drive_channels=geometry.drive_channels,
        results_by_id=bundle["results_by_id"],
        member_channels=channels,
        per_source_validity=validity if isinstance(validity, Mapping) else {},
        member_roles=member_roles,
        max_drive_voltage_v=geometry.max_drive_voltage_v,
        frequencies_hz=frequencies,
        request=request,
        quadrants=quadrants,
        config_motion=config_motion,
        config=config,
        start_time=time.time(),
        sound_speed_m_per_s=sound_speed,
        backend_metadata=stored_metadata,
    )

    updated = dict(results)
    updated["channels"] = channels
    order = [str(name) for name in results.get("channel_order") or []]
    if spec.id not in order:
        order.append(spec.id)
    updated["channel_order"] = order
    # The reply is drawn at once, so it carries the same power-qualification
    # flags the store persists: the new sum inherits its members' failures.
    return annotate_results(updated)
