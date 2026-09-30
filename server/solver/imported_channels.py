"""Shared channel metadata and driver scaling for imported CAD solves."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from server.cadlink.roles import canonical_source_role
from server.jobs.models import ImportedGeometrySource

from .driver_lem import channel_drive_scaling
from .imported import mesh_frequency_validation
from .result_mapping import json_safe_native_value

# Ingest source roles that name a driver band on a result channel, lowest first.
# Keep this ranking in sync with ROLE_BAND_RANK in frontend/src/stores/cadReturn.ts.
_BAND_ROLE_RANK = {"LF": 0, "MF": 1, "HF": 2}
_BAND_ROLES = frozenset(_BAND_ROLE_RANK)


def imported_validity_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    validation = mesh_frequency_validation(record)
    per_source_raw = validation.get("per_source")
    per_source_raw = per_source_raw if isinstance(per_source_raw, Mapping) else {}
    per_source: dict[str, Any] = {}
    for source_id, item in per_source_raw.items():
        if not isinstance(item, Mapping):
            continue
        per_source[str(source_id)] = {
            key: json_safe_native_value(value)
            for key, value in item.items()
            if key not in {"tag", "name"}
        }
    return per_source


def record_source_area_m2(record: Mapping[str, Any], source_id: str) -> float:
    """The source's full physical area from the ingestion record, in m²."""

    for source in record.get("sources") or []:
        if not isinstance(source, Mapping) or str(source.get("id")) != source_id:
            continue
        observed = source.get("observed")
        observed = observed if isinstance(observed, Mapping) else {}
        area_mm2 = observed.get("total_area_mm2")
        if isinstance(area_mm2, (int, float)) and float(area_mm2) > 0.0:
            return float(area_mm2) * 1.0e-6
    raise ValueError(
        f"driver coupling needs a recorded positive area for source {source_id!r}"
    )


def channel_source_identity(
    geometry: ImportedGeometrySource,
    record: Mapping[str, Any],
    axial_identity: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Name each drive channel's driver band and sources from the record.

    Only band roles name a driver: the record also carries structural roles
    (the rigid shell, port apertures) that no result may present as one. A
    channel spanning several roles takes the lowest band, independent of the
    source order authored by CAD.
    """

    roles: dict[str, str] = {}
    labels: dict[str, str] = {}
    for source in record.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        source_id = str(source.get("id") or "")
        if not source_id:
            continue
        role = canonical_source_role(str(source.get("role") or ""))
        if role in _BAND_ROLES:
            roles[source_id] = role
        label = source.get("label") or source.get("name")
        if isinstance(label, str) and label.strip():
            labels[source_id] = label.strip()
    identity: dict[str, dict[str, Any]] = {}
    for channel in geometry.drive_channels:
        source_ids = list(channel.source_ids)
        entry: dict[str, Any] = {
            "role": min(
                (roles[source_id] for source_id in source_ids if source_id in roles),
                key=_BAND_ROLE_RANK.__getitem__,
                default=None,
            )
        }
        if any(source_id in labels for source_id in source_ids):
            entry["source_labels"] = [
                labels.get(source_id, source_id) for source_id in source_ids
            ]
        if axial_identity and channel.id in axial_identity:
            entry.update(axial_identity[channel.id])
        identity[channel.id] = entry
    return identity


def channel_basis_metadata(
    geometry: ImportedGeometrySource,
    record: Mapping[str, Any],
    source_tags: Mapping[str, Any],
    driver_payloads: Mapping[str, Any],
    axial_identity: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Describe the drive domain retained beside each complex channel basis.

    An axial channel also records its contract version and per-tag axes
    (``axial_identity``): a basis solved under another axial contract is a
    different excitation and must not be mistaken for this one.
    """

    metadata: dict[str, dict[str, Any]] = {}
    for channel in geometry.drive_channels:
        source_ids = list(channel.source_ids)
        entry: dict[str, Any] = {
            "source_ids": source_ids,
            "source_tags": [int(source_tags[source_id]) for source_id in source_ids],
            "source_motion": str(channel.motion),
            "source_normalization": (
                "voltage_driven_driver_lem"
                if channel.id in driver_payloads
                else "unit_normal_acceleration"
            ),
        }
        if axial_identity and channel.id in axial_identity:
            entry.update(axial_identity[channel.id])
        try:
            entry["source_areas_m2"] = [
                record_source_area_m2(record, source_id) for source_id in source_ids
            ]
        except ValueError:
            # Area is optional for pressure-grid postprocessing. Its absence
            # must not turn a successful solve into an export failure.
            pass
        metadata[channel.id] = entry
    return metadata


def apply_channel_driver(
    channel: Any,
    result: Any,
    record: Mapping[str, Any],
    source_tags: Mapping[str, Any],
    *,
    drive_voltage_v: float,
    rg_ohm: float,
) -> dict[str, Any]:
    """Scale one channel's raw fields to the voltage-driven driver output."""

    source_id = channel.source_ids[0]
    area_m2 = record_source_area_m2(record, source_id)
    tag = int(source_tags[source_id])
    surface_avg = getattr(result, "surface_pressure_avg", None)
    p_avg = surface_avg.get(tag) if isinstance(surface_avg, Mapping) else None
    if p_avg is None:
        # Per-channel results from the multi-RHS solve report the driven
        # tag's area-weighted average surface pressure as ``impedance``.
        p_avg = result.impedance
    scale_raw, payload = channel_drive_scaling(
        np.asarray(result.frequencies_hz, dtype=np.float64).reshape(-1),
        np.asarray(p_avg, dtype=np.complex128),
        area_m2,
        channel.driver,
        drive_voltage_v=drive_voltage_v,
        rg_ohm=rg_ohm,
    )
    result.pressure_complex = (
        np.asarray(result.pressure_complex, dtype=np.complex128)
        * scale_raw[:, None, None]
    )
    sphere = getattr(result, "sphere_pressure_complex", None)
    if sphere is not None:
        result.sphere_pressure_complex = (
            np.asarray(sphere, dtype=np.complex128) * scale_raw[:, None]
        )
    surface_pressure = getattr(result, "surface_pressure_complex", None)
    if surface_pressure is not None:
        result.surface_pressure_complex = (
            np.asarray(surface_pressure, dtype=np.complex128) * scale_raw[:, None]
        )
    surface_neumann = getattr(result, "surface_neumann_complex", None)
    if surface_neumann is not None:
        result.surface_neumann_complex = (
            np.asarray(surface_neumann, dtype=np.complex128) * scale_raw[:, None]
        )
    power_scale = np.square(np.abs(scale_raw))
    for field in ("radiated_power_surface_w", "radiated_power_sphere_w"):
        radiated_power = getattr(result, field, None)
        if radiated_power is not None:
            setattr(
                result,
                field,
                np.asarray(radiated_power, dtype=np.float64) * power_scale,
            )
    payload["source_id"] = source_id
    payload["source_area_m2"] = area_m2
    return payload
