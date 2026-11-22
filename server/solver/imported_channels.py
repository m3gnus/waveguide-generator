"""Shared channel metadata and driver scaling for imported CAD solves."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping, Sequence, cast
from types import SimpleNamespace

import numpy as np

from server.contracts.source_roles import normalize_source_role
from server.contracts.conventions import ENGINEERING_PHASE_CONVENTION
from server.jobs.models import ImportedGeometrySource, SolveRequest

from .combined_channel import package_combined_channel
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
    scale_channel_result(result, scale)
    impedance = getattr(result, "impedance", None)
    # Only a tagged result may carry a velocity-basis impedance: the response
    # mapping converts an untagged (acceleration_legacy) one itself. An old
    # assembly replay decodes RMS velocity untagged, and converting it here as
    # well put its shown Z/(rho*c) off by j*omega.
    if impedance is not None and explicit:
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


_FROM_GEOMETRY: Any = object()


def imported_combined_channel(
    *,
    geometry: ImportedGeometrySource,
    sorted_results: Mapping[str, Any],
    request: SolveRequest,
    quadrants: int,
    config: Any,
    config_motion: str,
    started: float,
    per_source_validity: Mapping[str, Any],
    channel_identity: Mapping[str, Mapping[str, Any]],
    member_channels: Mapping[str, Any],
    sound_speed_m_per_s: float,
    backend_metadata: Callable[[Any], dict[str, Any]],
    max_drive_voltage_v: Any = _FROM_GEOMETRY,
    frequencies_hz: Any = None,
) -> dict[str, Any]:
    """Package an imported request's combined channel: the filtered sum.

    Every imported adapter combines its member channels the same way; only the
    backend's metadata block differs, and the adapter supplies it as
    ``backend_metadata``. The drive limit defaults to the geometry's and the
    frequency axis to the first physical member's; the exterior-transducer
    route passes its own.
    """

    spec = geometry.combine
    assert spec is not None
    return package_combined_channel(
        spec=spec,
        drive_channels=geometry.drive_channels,
        results_by_id=sorted_results,
        member_channels=member_channels,
        per_source_validity=per_source_validity,
        member_roles={member: identity.get("role") for member, identity in channel_identity.items()},
        max_drive_voltage_v=(geometry.max_drive_voltage_v if max_drive_voltage_v is _FROM_GEOMETRY
                             else max_drive_voltage_v),
        frequencies_hz=(sorted_results[geometry.physical_members(spec.members)[0]].frequencies_hz
                        if frequencies_hz is None else frequencies_hz),
        request=request,
        quadrants=quadrants,
        config_motion=config_motion,
        config=config,
        start_time=started,
        sound_speed_m_per_s=sound_speed_m_per_s,
        backend_metadata=backend_metadata,
    )


IMPEDANCE_OMITTED_MULTI_SOURCE = "multi-source channel: per-patch impedance is not a channel impedance"
IMPEDANCE_OMITTED_WEIGHTED = (
    "weighted patch: the patch pressure is scaled by its weight, so it is not the patch impedance"
)


# A patch weight this close to 1 is unit weight: assembly code derives weights.
_UNIT_WEIGHT_TOLERANCE = 1e-9


def impedance_omitted_reason(channel: Any) -> str | None:
    """Why a drive channel's raw acoustic impedance is not a channel impedance.

    The one rule for every backend, live and final. The raw field is the
    area-mean pressure per unit drive of a single tag (Metal, BEMPP) or of the
    union of the channel's tags under their weighted summed pressure (BEAT), so
    it is the channel impedance only for one source at unit weight. ``None``
    means the raw value is the channel impedance as solved. Otherwise the raw
    value is never shown: a final result carries the channel's modal impedance
    instead when ``apply_modal_channel_impedance`` could compute it, and drops
    the curve with this reason when it could not (and on every live frame).
    """

    if len(channel.source_ids) > 1:
        return IMPEDANCE_OMITTED_MULTI_SOURCE
    if any(abs(float(weight) - 1.0) > _UNIT_WEIGHT_TOLERANCE
           for weight in (channel.patch_weights or {}).values()):
        return IMPEDANCE_OMITTED_WEIGHTED
    return None


def drop_live_impedance(channel: Any, channel_response: dict[str, Any]) -> None:
    """Apply ``impedance_omitted_reason`` to one live frame, with its reason."""

    omitted = impedance_omitted_reason(channel)
    if omitted is not None:
        channel_response.pop("impedance", None)
        channel_response.setdefault("metadata", {})["impedance_omitted"] = omitted


# Names what a modal channel's curve is. Not ``impedance_normalization``: the
# chart payload already uses that key for rho_c/absolute display scaling.
IMPEDANCE_PROJECTION_KEY = "impedance_projection"
IMPEDANCE_PROJECTION_MODAL = "modal_mean_square_velocity"


def solved_tag_areas(msh_text: str) -> dict[int, float]:
    """Each physical tag's meshed area in the solved (possibly reduced) domain.

    Only ratios between a channel's tags are used. Every face of a reduced
    domain has the same number of mirror images, so these ratios are the full
    channel's; recorded source areas are not, since a completed cut source and
    an off-plane one carry different completion factors.
    """

    from server.mesh.imported import _tag_areas

    # Reads through the shared Gmsh reader's text-keyed cache, so a mesh the
    # solve already parsed is not parsed again.
    from .imported import _msh_triangles

    points, triangles, tags = _msh_triangles(msh_text)
    return _tag_areas(points, triangles, tags)


def modal_channel_impedance(
    result: Any,
    channel: Any,
    source_tags: Mapping[str, Any],
    tag_areas: Mapping[int, float],
) -> np.ndarray | None:
    """The channel's power-consistent impedance, in the raw ``impedance`` basis.

    ``Zs = sum_i w_i S_i <p>_i / sum_i w_i**2 S_i`` per frequency, from the
    area-mean pressure ``<p>_i`` on each driven tag under the channel's whole
    drive (``surface_pressure_avg``), the patch weights ``w_i`` and the tag
    areas ``S_i``. ``Re(Zs)/(rho*c)`` is the radiated power over
    ``rho*c * integral |v|^2 dS``, so it tends to 1 at high ka for any shape,
    and for one unit-weight patch it is exactly that patch's ``<p>/v``.
    Normal motion only: an axial patch's raw tag mean lacks the n.a projection.
    ``None`` when the solve did not return every tag's mean or the weights
    leave no motion.
    """

    if getattr(channel, "motion", "normal") != "normal":
        return None
    # BEAT reports its modal tag means apart from ``surface_pressure_avg``.
    averages = getattr(result, "tag_mean_pressure", None)
    if not isinstance(averages, Mapping):
        averages = getattr(result, "surface_pressure_avg", None)
    if not isinstance(averages, Mapping):
        return None
    weights = channel.patch_weights or {}
    by_tag: dict[int, float] = {}
    for source_id in channel.source_ids:
        tag = int(source_tags[source_id])
        weight = float(weights.get(source_id, 1.0))
        if by_tag.setdefault(tag, weight) != weight:
            return None
    numerator: Any = 0.0
    denominator = 0.0
    for tag, weight in by_tag.items():
        if weight == 0.0:
            continue  # A still patch adds nothing, wherever it lies.
        if tag not in averages or tag_areas.get(tag, 0.0) <= 0.0:
            return None
        area = float(tag_areas[tag])
        numerator = numerator + weight * area * np.asarray(averages[tag], dtype=np.complex128)
        denominator += weight * weight * area
    if denominator <= 0.0:
        return None
    return np.asarray(numerator, dtype=np.complex128).reshape(-1) / denominator


def apply_modal_channel_impedance(
    results_by_id: Mapping[str, Any],
    drive_channels: Any,
    source_tags: Mapping[str, Any],
    msh_text: str,
    *,
    tag_areas: Mapping[int, float] | None = None,
) -> set[str]:
    """Give every channel the raw field misreports its power-consistent impedance.

    Only channels ``impedance_omitted_reason`` rejects are touched: a
    unit-weight single source's raw value already is its ``Zs`` and stays
    bit-identical. Call it on the native results, before any basis
    normalisation, so the tag means and the raw impedance share one basis.
    ``tag_areas`` may be supplied by a backend that already holds them;
    otherwise they are read from ``msh_text``. Returns the channels that now
    carry ``Zs``.
    """

    candidates = [channel for channel in drive_channels
                  if channel.id in results_by_id and impedance_omitted_reason(channel) is not None]
    if not candidates:
        return set()
    if tag_areas is None:
        try:
            tag_areas = solved_tag_areas(msh_text)
        except ValueError:
            # Without readable tag areas there is no Zs; the rule's drop stands.
            return set()
    modal: set[str] = set()
    for channel in candidates:
        result = results_by_id[channel.id]
        zs = modal_channel_impedance(result, channel, source_tags, tag_areas)
        if zs is None or zs.shape != np.asarray(result.frequencies_hz).reshape(-1).shape:
            continue
        result.impedance = zs
        modal.add(channel.id)
    return modal


def finish_imported_channel_response(
    channel_response: dict[str, Any],
    *,
    geometry: ImportedGeometrySource,
    driver_payload: dict[str, Any] | None,
    rms_velocity: bool,
    channel: Any,
    modal_impedance: bool = False,
) -> bool:
    """Apply the impedance and driver contract to one imported channel response.

    A raw impedance that is not a channel impedance (``impedance_omitted_reason``)
    is dropped with its reason, unless ``modal_impedance`` says the result
    already carries the channel's power-consistent ``Zs``; a voltage-driven
    channel then reports its electrical input impedance instead. Returns
    whether a driver payload was applied.
    """

    if rms_velocity:
        channel_response["metadata"]["impedance_drive"] = "rms_velocity"
    omitted = impedance_omitted_reason(channel)
    if omitted is not None and modal_impedance:  # the result already carries Zs
        channel_response["metadata"][IMPEDANCE_PROJECTION_KEY] = IMPEDANCE_PROJECTION_MODAL
    elif omitted is not None:
        channel_response.pop("impedance", None)
        channel_response["metadata"]["impedance_omitted"] = omitted
    if driver_payload is None:
        return False
    electrical = driver_payload.pop("electrical_impedance_ohm")
    channel_response["impedance"] = {
        "frequencies": electrical["frequencies"],
        "real": electrical["real"],
        "imaginary": electrical["imaginary"],
    }
    metadata = channel_response["metadata"]
    metadata["impedance_units"] = "ohms"
    metadata["impedance_quantity"] = "electrical_input_impedance"
    # hornlab_sim's LEM uses engineering +jω throughout. The channel's pressure
    # keeps its own convention tag, so impedance needs its own or an exporter
    # could faithfully apply the wrong sign while honouring the acoustic one.
    metadata["impedance_phase_convention"] = ENGINEERING_PHASE_CONVENTION
    metadata["impedance_drive"] = "voltage"
    metadata["driver"] = driver_payload
    metadata["drive"] = {
        "voltage_v": geometry.drive_voltage_v,
        "rg_ohm": geometry.rg_ohm,
    }
    warnings = metadata.setdefault("warnings", [])
    warnings.extend(driver_payload.get("warnings") or [])
    metadata["warning_count"] = len(warnings)
    return True


def imported_fem_exclusion(record: Mapping[str, Any], geometry: ImportedGeometrySource) -> dict[str, Any]:
    """Whether the record's declared FEM air volumes were left out of this solve."""

    evidence = record.get("evidence")
    fem_volumes = (evidence or {}).get("fem_air_volumes") if isinstance(evidence, Mapping) else []
    fem_volumes = fem_volumes or []
    if fem_volumes and geometry.exterior_only:
        return {
            "excluded": True,
            "declared_volume_count": len(fem_volumes),
            "reason": "Phase 2 exterior_only override",
        }
    return {"excluded": False, "declared_volume_count": len(fem_volumes)}


def imported_envelope(
    *,
    geometry: ImportedGeometrySource,
    record: Mapping[str, Any],
    channels: dict[str, Any],
    channel_order: list[str],
    per_source_validity: Mapping[str, Any],
    domain_planes: Sequence[str],
    frame_basis: Mapping[str, Any],
    solver_backend: str,
    solver_engine: Mapping[str, Any],
    started: float,
    trace_estimated_bytes: int | None,
    trace_cap_bytes: int | None,
    leading_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The imported ``multi_channel`` result envelope shared by every backend.

    Backend-specific metadata is the ``solver_backend`` name and the
    ``solver_engine`` block; ``leading_metadata`` keys come first, and an
    adapter appends its own extras to ``envelope["metadata"]`` afterwards.
    """

    metadata: dict[str, Any] = {
        **(leading_metadata or {}),
        "result_contract_version": 2,
        "geometry_type": "imported",
        "solver_backend": solver_backend,
        "solver_mode": "full_3d",
        "solve_path": "full-3d",
        "solver_engine": dict(solver_engine),
        "ingest_id": geometry.ingest_id,
        "manifest_sha256": geometry.manifest_sha256,
        "artifact_sha256": geometry.artifact_sha256,
        "tag_namespace": record.get("tag_namespace"),
        "tag_map": json_safe_native_value(record.get("tag_map") or {}),
        "per_source_frequency_validity": per_source_validity,
        "symmetry_planes_used": sorted(domain_planes),
        "polar_grid_derivation": json_safe_native_value(
            record.get("polar_grid_derivation") or {}
        ),
        "observation_origin_effective": "throat",
        "observation_frame_basis": dict(frame_basis),
        "acknowledged_findings": list(geometry.acknowledged_findings),
        "exterior_only": geometry.exterior_only,
        "fem_exclusion": imported_fem_exclusion(record, geometry),
        "performance": {"total_time_seconds": time.time() - started},
        "field_trace_retention": {
            "estimated_bytes": trace_estimated_bytes,
            "cap_bytes": trace_cap_bytes,
        },
    }
    envelope: dict[str, Any] = {
        "result_kind": "multi_channel",
        "result_contract_version": 2,
        "channels": channels,
        "channel_order": channel_order,
        "metadata": metadata,
    }
    # A convenience mirror of the per-channel grids: the sorted union of every
    # channel's frequencies. Optional in the contract -- envelopes persisted
    # before it existed remain valid without it.
    envelope_frequencies = sorted(
        {
            float(value)
            for channel_payload in channels.values()
            for value in (channel_payload.get("frequencies") or [])
        }
    )
    if envelope_frequencies:
        envelope["frequencies"] = envelope_frequencies
    return envelope


# Compatibility names for callers of the original conversion helper.
normalize_assembly_prescribed_result = normalize_prescribed_result
assembly_prescribed_entry = prescribed_entry
