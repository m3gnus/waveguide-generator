"""Public pressure-basis export from a retained recombination artifact.

The internal channel-bases NPZ deliberately keeps Metal's raw ``exp(+ikr)``
phasors because post-solve recombination applies engineering filter weights at
one explicit convention boundary.  The public artifact follows the Fusion
pipeline's pressure-basis contract instead: ``pressure_complex`` is conjugated
to engineering ``exp(+j omega t)`` and the convention is tagged in the file.

Surface-average pressure is not part of WG's retained channel-bases artifact,
so this exporter does not fabricate it.  Source tags and areas are included
when the solve recorded them; aggregated channels use ``source_tag = -1`` and
carry the complete ``source_ids``/``source_tags`` arrays.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from server.contracts.conventions import (
    ENGINEERING_PHASE_CONVENTION,
    solver_to_engineering,
)

from .combine import deserialize_channel_bases
from .sampling_provenance import sampling_provenance
from .imported import LEGACY_AXIAL_CONTRACT


PRESSURE_BASIS_VERSION = 1
PRESSURE_PHASE_CONVENTION = ENGINEERING_PHASE_CONVENTION


@dataclass(frozen=True)
class PressureBasisExport:
    channel_id: str
    content: bytes


def _channel_result_metadata(results: Mapping[str, Any], channel_id: str) -> Mapping[str, Any]:
    channels = results.get("channels")
    if not isinstance(channels, Mapping):
        return {}
    channel = channels.get(channel_id)
    if not isinstance(channel, Mapping):
        return {}
    metadata = channel.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _stored_metadata(data: bytes, channel_id: str) -> dict[str, Any]:
    with np.load(io.BytesIO(data), allow_pickle=False) as archive:
        key = f"metadata::{channel_id}"
        if key not in archive:
            return {}
        try:
            decoded = json.loads(str(np.asarray(archive[key]).item()))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return decoded if isinstance(decoded, dict) else {}


def export_pressure_basis(
    bases_npz: bytes,
    results: Mapping[str, Any],
    channel_id: str | None = None,
    request_motions: Mapping[str, str] | None = None,
) -> PressureBasisExport:
    """Return one add-in-compatible engineering-convention pressure basis.

    ``request_motions`` is each drive channel's motion from the archived
    request. It recovers the motion of a basis that did not store one; when no
    source establishes it, the export is refused rather than guessed as normal.
    """

    bundle = deserialize_channel_bases(bases_npz)
    channel_ids = list(bundle["channel_ids"])
    selected = str(channel_id or "").strip()
    if selected:
        if selected not in channel_ids:
            raise ValueError(
                f"pressure basis channel {selected!r} is not available; "
                f"choose one of {', '.join(channel_ids)}"
            )
    elif len(channel_ids) == 1:
        selected = channel_ids[0]
    else:
        raise ValueError(
            "choose a pressure-basis channel: " + ", ".join(channel_ids)
        )

    result = bundle["results_by_id"][selected]
    stored = _stored_metadata(bases_npz, selected)
    public_metadata = _channel_result_metadata(results, selected)
    source_ids = [
        str(value)
        for value in stored.get("source_ids", public_metadata.get("source_ids", []))
        if str(value)
    ]
    source_tags = [int(value) for value in stored.get("source_tags", [])]
    source_areas = [float(value) for value in stored.get("source_areas_m2", [])]
    normalization = str(stored.get("source_normalization") or "")
    if not normalization:
        normalization = (
            "voltage_driven_driver_lem"
            if public_metadata.get("impedance_drive") == "voltage"
            else "unit_normal_acceleration"
        )

    arrays: dict[str, np.ndarray] = {
        "format_version": np.asarray(PRESSURE_BASIS_VERSION, dtype=np.int32),
        "source_name": np.asarray(selected),
        "channel_id": np.asarray(selected),
        "source_tag": np.asarray(
            source_tags[0] if len(source_tags) == 1 else -1, dtype=np.int32
        ),
        "source_ids": np.asarray(source_ids, dtype=str),
        "source_tags": np.asarray(source_tags, dtype=np.int32),
        "source_areas_m2": np.asarray(source_areas, dtype=np.float64),
        "frequencies_hz": np.asarray(result.frequencies_hz, dtype=np.float64),
        "observation_angles_deg": np.asarray(
            result.observation_angles_deg, dtype=np.float64
        ),
        "observation_planes": np.asarray(result.observation_planes, dtype=str),
        "pressure_complex": solver_to_engineering(
            np.asarray(result.pressure_complex, dtype=np.complex128)
        ),
        "phase_convention": np.asarray(PRESSURE_PHASE_CONVENTION),
        "source_normalization": np.asarray(normalization),
        "surface_pressure_avg_available": np.asarray(False),
    }
    flags = getattr(result, "frequency_status", None)
    if flags is not None:
        sampling = sampling_provenance({"frequencies": result.frequencies_hz, "frequency_status": flags})
        arrays.update({key: np.asarray(value) for key, value in sampling.items()})
    motion = str(
        stored.get("source_motion")
        or public_metadata.get("source_motion")
        or (request_motions or {}).get(selected)
        or ""
    )
    if not motion:
        raise ValueError(
            f"the source motion of pressure basis channel {selected!r} cannot be "
            "established from the stored basis, the results or the archived request"
        )
    if motion == "axial":
        # A basis solved before the per-source-axis contract recorded no
        # version: it moved sources along the observation frame's axis with a
        # sign vote, which is a different excitation.
        arrays["axial_contract"] = np.asarray(
            str(
                stored.get("axial_contract")
                or public_metadata.get("axial_contract")
                or LEGACY_AXIAL_CONTRACT
            )
        )
        recorded_axes = stored.get("source_axes") or public_metadata.get("source_axes") or []
        source_axes = [item for item in recorded_axes if isinstance(item, Mapping)]
        if source_axes:
            arrays["source_axes"] = np.asarray(
                [item["axis"] for item in source_axes], dtype=np.float64
            )
            arrays["source_axes_raw"] = np.asarray(
                [item["raw_axis"] for item in source_axes], dtype=np.float64
            )
    if len(source_areas) == 1:
        arrays["source_area_m2"] = np.asarray(source_areas[0], dtype=np.float64)
    sphere = getattr(result, "sphere_pressure_complex", None)
    if sphere is not None:
        arrays.update(
            {
                "sphere_pressure_complex": solver_to_engineering(
                    np.asarray(sphere, dtype=np.complex128)
                ),
                "sphere_theta_deg": np.asarray(result.sphere_theta_deg, dtype=np.float64),
                "sphere_phi_deg": np.asarray(result.sphere_phi_deg, dtype=np.float64),
            }
        )

    arrays["source_motion"] = np.asarray(motion)

    output = io.BytesIO()
    np.savez_compressed(output, **arrays)
    return PressureBasisExport(channel_id=selected, content=output.getvalue())


__all__ = [
    "PRESSURE_BASIS_VERSION",
    "PRESSURE_PHASE_CONVENTION",
    "PressureBasisExport",
    "export_pressure_basis",
]
