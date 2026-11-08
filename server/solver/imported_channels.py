"""Shared channel metadata and driver scaling for imported CAD solves."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, cast
from types import SimpleNamespace

import numpy as np

from server.cadlink.roles import normalize_source_role
from server.jobs.models import ImportedGeometrySource, SolveRequest

from .context import SolverContext
from .driver_lem import channel_drive_scaling
from .excitation_basis import pressure_per_velocity_scale
from .field_traces_store import field_trace_retention_plan
from .frequency_sweep import live_execution_frequencies
from .imported import mesh_frequency_validation
from .result_mapping import json_safe_native_value

# Ingest source roles that name a driver band on a result channel, lowest first.
# Keep this ranking in sync with ROLE_BAND_RANK in frontend/src/stores/cadReturn.ts.
_BAND_ROLE_RANK = {"LF": 0, "MF": 1, "HF": 2}
_BAND_ROLES = frozenset(_BAND_ROLE_RANK)


@dataclass(frozen=True)
class ImportedSolveFacts:
    """Validated common inputs, prepared at each adapter's existing setup boundary.

    The context remains the existing solver value; this frozen holder never
    changes it. Backend availability, capabilities and native lifetime stay
    with the adapter.
    """

    context: SolverContext
    motions: frozenset[str]
    mesh_stats: Mapping[str, Any] | None
    frequencies_hz: tuple[float, ...]
    field_plane_enabled: bool
    channel_count: int

    def trace_retention_plan(
        self, msh_text: str, *, supported: bool, cap_bytes: int | None,
        unsupported_reason: str = "unsupported_solve_mode",
    ) -> tuple[bool, str | None, int | None, int]:
        return field_trace_retention_plan(
            msh_text, mesh_stats=self.mesh_stats,
            frequency_count=len(self.frequencies_hz), channel_count=self.channel_count,
            enabled=self.field_plane_enabled, supported=supported, cap_bytes=cap_bytes,
            unsupported_reason=unsupported_reason,
        )


def prepare_imported_solve_facts(
    request: SolveRequest, record: Mapping[str, Any], *, quadrants: int,
) -> ImportedSolveFacts:
    """Resolve facts after the adapter has verified imported geometry."""

    geometry = cast(ImportedGeometrySource, request.geometry)
    motions = frozenset(channel.motion for channel in geometry.drive_channels)
    config_motion = next(iter(motions)) if len(motions) == 1 else "normal"
    context = SolverContext.from_imported_request(
        request, quadrants=quadrants, source_motion=config_motion,
    )
    context.validate()
    mesh = record.get("mesh")
    mesh = mesh if isinstance(mesh, Mapping) else {}
    stats = mesh.get("stats")
    return ImportedSolveFacts(
        context=context, motions=motions,
        mesh_stats=stats if isinstance(stats, Mapping) else None,
        frequencies_hz=tuple(live_execution_frequencies(context).tolist()),
        field_plane_enabled=getattr(context, "polar_config", {}).get("field_plane", True) is True,
        channel_count=len(geometry.drive_channels),
    )


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


def record_source_completion_factor(record: Mapping[str, Any], source_id: str) -> float | None:
    """The saved area convention; None means the pre-completion contract.

    An explicit 1.0 is a current record whose observed source is already
    whole. Absence must remain distinct so retained runs replay the original
    campaign and coupling convention without upgrading their saved decision.
    File imports store already completed observations and mark their sources
    directly, since they have no CAD domain decision.
    """
    decision = record.get("domain_decision")
    decided = (decision.get("sources") or {}).get("by_id") if isinstance(decision, Mapping) else None
    decided = decided.get(source_id) if isinstance(decided, Mapping) else None
    factor = decided.get("completion_factor") if isinstance(decided, Mapping) else None
    if factor is None:
        factor = next((
            source.get("completion_factor") for source in record.get("sources") or []
            if isinstance(source, Mapping) and str(source.get("id")) == source_id
        ), None)
    return float(factor) if factor is not None else None


def record_source_area_m2(record: Mapping[str, Any], source_id: str) -> float:
    """The source area under its saved normalization convention, in m².

    Current CAD records complete a cut diaphragm to its full physical area;
    records predating completion retain their original observed area.
    """

    factor = record_source_completion_factor(record, source_id)
    for source in record.get("sources") or []:
        if not isinstance(source, Mapping) or str(source.get("id")) != source_id:
            continue
        observed = source.get("observed")
        observed = observed if isinstance(observed, Mapping) else {}
        area_mm2 = observed.get("total_area_mm2")
        if isinstance(area_mm2, (int, float)) and float(area_mm2) > 0.0:
            return float(area_mm2) * (factor if factor is not None else 1.0) * 1.0e-6
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
    *, engine: str | None = None,
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
                else "native_rms_velocity" if geometry.normalization_for_engine(
                    # Preserve even the legacy BEMPP basis label on replay;
                    # explicit requests describe their actual backend basis.
                    engine if (geometry.excitation_normalization is not None
                               or geometry.excitation_normalization_by_engine is not None) else None
                ) == "rms_velocity" else "unit_normal_acceleration"
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
        excitation_normalization=getattr(result, "excitation_normalization", "acceleration_legacy"),
    )
    # The LEM is driven by an RMS generator voltage: these are RMS fields.
    scale_channel_result(result, scale_raw, rms=True)
    payload["source_id"] = source_id
    payload["source_area_m2"] = area_m2
    return payload


def scale_channel_result(result: Any, scale: np.ndarray, *, rms: bool = False) -> None:
    """Scale retained complex fields and physical power together, before packaging.

    Native solvers integrate power from peak phasors: ``0.5*Re(p*conj(v))`` over
    the driven faces and ``|p|**2/(2*rho*c)`` over the sphere. ``rms=True``
    states that the scaled fields are RMS phasors (the convention WG's SPL
    already uses), whose time-average power is ``Re(p*conj(v))``: twice the
    native number. That factor is applied once per result, however many RMS
    scalings follow, and recorded as ``radiated_power_amplitude``.
    """

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
    power_fields = [field for field in ("radiated_power_surface_w", "radiated_power_sphere_w")
                    if getattr(result, field, None) is not None]
    power_scale = np.square(np.abs(scale))
    if rms and power_fields and getattr(result, "radiated_power_amplitude", "peak") != "rms":
        power_scale = 2.0 * power_scale
        result.radiated_power_amplitude = "rms"
    for field in power_fields:
        setattr(result, field, np.asarray(getattr(result, field), dtype=np.float64) * power_scale)


def normalize_prescribed_result(result: Any, geometry: ImportedGeometrySource, *, engine: str | None = None) -> None:
    """Convert acceleration bases to the requested prescribed normalization.

    The legacy imported solvers return pressure per unit acceleration under
    exp(-jwt). Undo that velocity factor once; ordinary CAD/file bases keep
    their established acceleration normalization.
    """

    normalization = geometry.normalization_for_engine(engine)
    explicit = (geometry.excitation_normalization is not None
                or geometry.excitation_normalization_by_engine is not None)
    if explicit:
        result.excitation_normalization = normalization
    if normalization != "rms_velocity":
        return
    scale = pressure_per_velocity_scale(result.frequencies_hz)
    scale_channel_result(result, scale, rms=True)
    impedance = getattr(result, "impedance", None)
    if impedance is not None:
        result.impedance = np.asarray(impedance) * scale
    if explicit and isinstance(getattr(result, "surface_pressure_avg", None), Mapping):
        result.surface_pressure_avg = {
            tag: np.asarray(pressure) * scale for tag, pressure in result.surface_pressure_avg.items()
        }


def prescribed_entry(entry: Mapping[str, Any], frequency_hz: float,
                              geometry: ImportedGeometrySource, *, engine: str | None = None) -> dict[str, Any]:
    """Apply the same basis conversion to a provisional single-frequency row."""

    if geometry.normalization_for_engine(engine) != "rms_velocity":
        return dict(entry)
    fields = {"observation_pressure_complex": "pressure_complex",
              "observation_sphere_pressure_complex": "sphere_pressure_complex",
              "observation_spl_db": "spl_db", "observation_directivity_db": "directivity_db", "impedance": "impedance"}
    native = SimpleNamespace(frequencies_hz=[frequency_hz], **{
        target: np.asarray(entry[source])[None, ...]
        for source, target in fields.items() if entry.get(source) is not None})
    normalize_prescribed_result(native, geometry, engine=engine)
    return {**entry, **{source: getattr(native, target)[0] for source, target in fields.items() if hasattr(native, target)},
            **({"excitation_normalization": native.excitation_normalization}
               if hasattr(native, "excitation_normalization") else {})}


# Compatibility names for callers of the original conversion helper.
normalize_assembly_prescribed_result = normalize_prescribed_result
assembly_prescribed_entry = prescribed_entry
