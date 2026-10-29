"""Shared channel metadata and driver scaling for imported CAD solves."""

from __future__ import annotations

from typing import Any, Mapping
from types import SimpleNamespace

import numpy as np

from server.cadlink.roles import normalize_source_role
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
        role = normalize_source_role(str(source.get("role") or ""), mode="band")
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
        if channel.patch_weights is not None:
            entry["patch_weights"] = dict(channel.patch_weights)
            entry["physical_source_id"] = channel.physical_source_id
            entry["excitation_contract"] = "native-source-contour-v1"
            entry["excitation_sha256"] = record.get("native_source", {}).get("excitation_sha256")
            native = record.get("native_source", {})
            if "native-shared-horn-woofer-v1" in native.get("required_features", []):
                entry["excitation_contract"] = "native-shared-horn-woofer-v1"
                entry["source_frame"] = native["source_frames"][channel.physical_source_id]
                entry["observation_frame"] = native["observation_frame"]
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
                else "native_rms_velocity" if geometry.design_assembly else "unit_normal_acceleration"
            ),
        }
        if channel.patch_weights is not None:
            entry["patch_weights"] = dict(channel.patch_weights)
            entry["physical_source_id"] = channel.physical_source_id
            entry["excitation_sha256"] = record.get("native_source", {}).get("excitation_sha256")
            native = record.get("native_source", {})
            if "native-shared-horn-woofer-v1" in native.get("required_features", []):
                entry["source_frame"] = native["source_frames"][channel.physical_source_id]
                entry["observation_frame"] = native["observation_frame"]
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
    scale_channel_result(result, scale_raw)
    payload["source_id"] = source_id
    payload["source_area_m2"] = area_m2
    return payload


def scale_channel_result(result: Any, scale: np.ndarray) -> None:
    """Scale retained complex fields and physical power together, before packaging."""

    for name in ("pressure_complex", "sphere_pressure_complex", "surface_pressure_complex", "surface_neumann_complex"):
        value = getattr(result, name, None)
        if value is not None:
            array = np.asarray(value, dtype=np.complex128)
            setattr(result, name, array * scale.reshape((-1,) + (1,) * (array.ndim - 1)))
    for name in ("spl_db",):
        value = getattr(result, name, None)
        if value is not None:
            array = np.asarray(value, dtype=float)
            with np.errstate(divide="ignore"):
                setattr(result, name, array + (20 * np.log10(np.abs(scale))).reshape((-1,) + (1,) * (array.ndim - 1)))
    power_scale = np.square(np.abs(scale))
    for field in ("radiated_power_surface_w", "radiated_power_sphere_w"):
        radiated_power = getattr(result, field, None)
        if radiated_power is not None:
            setattr(
                result,
                field,
                np.asarray(radiated_power, dtype=np.float64) * power_scale,
            )


def normalize_assembly_prescribed_result(result: Any, geometry: ImportedGeometrySource) -> None:
    """Assembly prescribed channels use 1 m/s RMS on every backend and driver route.

    The legacy imported solvers return pressure per unit acceleration under
    exp(-jwt). Undo that velocity factor once; ordinary CAD/file bases keep
    their established acceleration normalization.
    """

    if not geometry.design_assembly:
        return
    scale = -2j * np.pi * np.asarray(result.frequencies_hz, dtype=float)
    scale_channel_result(result, scale)
    impedance = getattr(result, "impedance", None)
    if impedance is not None:
        result.impedance = np.asarray(impedance) * scale


def assembly_prescribed_entry(entry: Mapping[str, Any], frequency_hz: float,
                              geometry: ImportedGeometrySource) -> dict[str, Any]:
    """Apply the same basis conversion to a provisional single-frequency row."""

    if not geometry.design_assembly:
        return dict(entry)
    fields = {"observation_pressure_complex": "pressure_complex",
              "observation_sphere_pressure_complex": "sphere_pressure_complex",
              "observation_spl_db": "spl_db", "observation_directivity_db": "directivity_db", "impedance": "impedance"}
    native = SimpleNamespace(frequencies_hz=[frequency_hz], **{
        target: np.asarray(entry[source])[None, ...]
        for source, target in fields.items() if entry.get(source) is not None})
    normalize_assembly_prescribed_result(native, geometry)
    return {**entry, **{source: getattr(native, target)[0] for source, target in fields.items() if hasattr(native, target)}}
