"""Recompute a completed job's combined channel from persisted bases.

The solve-time combine (``server/solver/metal.py``) and this module produce
contract-identical channels; this one trades the live solver state for the
``simulation_channel_bases`` NPZ, so changing a crossover after the solve is
a millisecond-scale numpy recombination instead of a re-solve.
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
from .combine import combine_drive_channels, deserialize_channel_bases
from .context import SolverContext
from .result_mapping import build_solver_response


class RecombineError(ValueError):
    """A user-addressable recombination refusal (maps to HTTP 422)."""


_IMPEDANCE_OMITTED = (
    "combined channel: member drives differ; no single impedance exists"
)


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
        value for value in spec.crossovers_hz if value < band[0] or value > band[1]
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
    validity = validity if isinstance(validity, Mapping) else {}
    channels_by_id = {channel.id: channel for channel in geometry.drive_channels}
    member_validity_hz: dict[str, float] = {}
    for member in spec.members:
        channel = channels_by_id.get(member)
        if channel is None:
            continue
        limits = [
            float(item["effective_max_valid_frequency_hz"])
            for source_id in channel.source_ids
            if isinstance(item := validity.get(source_id), Mapping)
            and item.get("effective_max_valid_frequency_hz") is not None
        ]
        if limits:
            member_validity_hz[member] = min(limits)

    combined_result, combine_payload = combine_drive_channels(
        bundle["results_by_id"],
        members=list(spec.members),
        crossovers_hz=list(spec.crossovers_hz),
        level_match=spec.level_match,
        align=spec.align,
        member_validity_hz=member_validity_hz,
    )

    motions = {channel.motion for channel in geometry.drive_channels}
    config_motion = next(iter(motions)) if len(motions) == 1 else "normal"
    quadrants_raw = member_metadata.get("quadrants")
    quadrants = int(quadrants_raw) if isinstance(quadrants_raw, (int, float)) else 1234
    context = SolverContext.from_imported_request(
        request, quadrants=quadrants, source_motion=config_motion
    )
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

    source_ids = [
        source_id
        for member in spec.members
        for source_id in (
            channels_by_id[member].source_ids if member in channels_by_id else []
        )
    ]
    metadata: dict[str, Any] = {
        "solver_backend": member_metadata.get("solver_backend", "metal"),
        "solver_mode": member_metadata.get("solver_mode", "full_3d"),
        "geometry_type": "imported",
        "drive_channel_id": spec.id,
        "derived_from_channels": list(spec.members),
        "source_ids": source_ids,
        "engine": member_metadata.get("engine", "hornlab-metal-bem"),
        "phase_time_convention": "exp(+ikr)",
        "combine": combine_payload,
        "recombined": True,
        "performance": {},
    }
    for copied in ("device_interface", "mesh_validation", "metal"):
        value = member_metadata.get(copied)
        if isinstance(value, Mapping):
            metadata[copied] = dict(value)

    response = build_solver_response(
        result=combined_result,
        config=config,
        context=context,
        start_time=time.time(),
        metadata=metadata,
        sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_metal_bem"),
    )
    response.pop("impedance", None)
    response["metadata"]["impedance_omitted"] = _IMPEDANCE_OMITTED

    updated = dict(results)
    channels[spec.id] = response
    updated["channels"] = channels
    order = [str(name) for name in results.get("channel_order") or []]
    if spec.id not in order:
        order.append(spec.id)
    updated["channel_order"] = order
    return updated
