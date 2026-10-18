"""Package a combined drive channel: the one step every imported backend shares.

Metal, BEAT and BEMPP solves build their combined channel from live solver
results, and ``recombine`` rebuilds it after the solve from the persisted
bases. All four go through ``package_combined_channel`` so the validity
limits, maximum-output ceilings, combine payload and response shape cannot
drift between backends or between a solve and a later crossover change. Each
caller supplies only what genuinely differs: its backend's metadata block and
the sound speed its solver ran at.

The coupled FEM envelope (``beat_adapter/results.py``) combines without
validity or output limits and is not routed here yet.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from server.contracts.conventions import PHASE_TIME_CONVENTION

from .combine import combine_drive_channels
from .context import SolverContext
from .driver_limits import MemberLimits, member_limits_from_channel
from .result_mapping import build_solver_response


IMPEDANCE_OMITTED = "combined channel: member drives differ; no single impedance exists"


def package_combined_channel(
    *,
    spec: Any,
    drive_channels: Sequence[Any],
    results_by_id: Mapping[str, Any],
    member_channels: Mapping[str, Any],
    per_source_validity: Mapping[str, Any],
    member_roles: Mapping[str, str | None],
    max_drive_voltage_v: float | None,
    frequencies_hz: Any,
    request: Any,
    quadrants: int,
    config_motion: str,
    config: Any,
    start_time: float | None,
    sound_speed_m_per_s: float,
    backend_metadata: Callable[[Any], Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the contract-shaped combined channel for ``spec``.

    ``spec`` is the request's ``ChannelCombineSpec``, ``drive_channels`` its
    ``DriveChannel`` list and ``request`` the ``SolveRequest``; they are typed
    loosely so this module does not import ``server.jobs``.

    ``results_by_id`` holds the member results ``combine_drive_channels``
    reads (live solver results or deserialized bases). ``member_channels``
    holds the already-built member responses, whose driver model gives the
    maximum-output ceilings without a second LEM pass. ``backend_metadata``
    receives the combined result and returns the backend-specific metadata
    (``solver_backend``, ``engine``, device and backend blocks, frame basis),
    which is merged over the shared fields.
    """

    channels_by_id = {channel.id: channel for channel in drive_channels}
    member_validity_hz: dict[str, float] = {}
    for member in spec.members:
        channel = channels_by_id.get(member)
        if channel is None:
            continue
        limits = [
            float(item["effective_max_valid_frequency_hz"])
            for source_id in channel.source_ids
            if isinstance(item := per_source_validity.get(source_id), Mapping)
            and item.get("effective_max_valid_frequency_hz") is not None
        ]
        if limits:
            member_validity_hz[member] = min(limits)

    frequencies = np.asarray(frequencies_hz, dtype=np.float64).reshape(-1)
    member_limits: dict[str, MemberLimits] = {}
    for member in spec.members:
        limits = member_limits_from_channel(
            member_channels.get(member),
            frequencies_hz=frequencies,
            max_voltage_v=max_drive_voltage_v,
        )
        if limits is not None:
            member_limits[member] = limits

    resolved = spec.resolved()
    combined_result, combine_payload = combine_drive_channels(
        results_by_id,
        members=list(spec.members),
        channels=resolved["channels"],
        reference=resolved["reference"],
        member_validity_hz=member_validity_hz,
        member_roles={member: member_roles.get(member) for member in spec.members},
        member_limits=member_limits,
    )

    metadata: dict[str, Any] = {
        "solver_mode": "full_3d",
        "geometry_type": "imported",
        "drive_channel_id": spec.id,
        "derived_from_channels": list(spec.members),
        "source_ids": [
            source_id
            for member in spec.members
            for source_id in (
                channels_by_id[member].source_ids if member in channels_by_id else ()
            )
        ],
        "phase_time_convention": PHASE_TIME_CONVENTION,
        "combine": combine_payload,
    }
    metadata.update(backend_metadata(combined_result))

    context = SolverContext.from_imported_request(
        request, quadrants=quadrants, source_motion=config_motion
    )
    response = build_solver_response(
        result=combined_result,
        config=config,
        context=context,
        # ``None`` times only the response build, as recombine always has: the
        # live solves pass their solve's start instead.
        start_time=time.time() if start_time is None else start_time,
        metadata=metadata,
        sound_speed_m_per_s=sound_speed_m_per_s,
    )
    response.pop("impedance", None)
    response["metadata"]["impedance_omitted"] = IMPEDANCE_OMITTED
    return response
