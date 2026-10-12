"""Official BEAT wire results in WG's unit-acceleration native contract."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import math
from typing import Mapping, TYPE_CHECKING, Any

import numpy as np

from server.contracts.conventions import PHASE_TIME_CONVENTION, SOLVER_TIME_CONVENTION
from .driver_loading import BoundaryLoading
from .observations import ObservationLayout

if TYPE_CHECKING:
    from .request import CompiledRequest


class ResultContractError(RuntimeError):
    """An official result or sweep differs from the requested contract."""


def real_source_copy_count(symmetry: str) -> int:
    """Rigid ground images are fictitious; x/xy copies are physical sources."""
    try:
        return {"off": 1, "x": 2, "xy": 4, "ground": 1}[symmetry]
    except KeyError as exc:
        raise ValueError(f"Unsupported BEAT symmetry {symmetry!r}") from exc


def acceleration_scale(frequency_hz: float) -> complex:
    if not math.isfinite(frequency_hz) or frequency_hz <= 0:
        raise ValueError("Frequency must be finite and positive")
    return 1.0 / (-1j * 2.0 * math.pi * frequency_hz)


def mean_pressure_from_force(
    force_per_velocity: complex, *, source_area_m2: float, symmetry: str = "off",
) -> complex:
    """Convert unit-normal-velocity force using physical area times real copies.

    source_area_m2 is the meshed source area in the fundamental domain, after
    scaling to metres. Official force has no legacy x10/[Re/2,-Im/2] display
    packing. This conversion is not valid for a generalized axial force.
    """
    if not math.isfinite(source_area_m2) or source_area_m2 <= 0:
        raise ValueError("Source area must be finite and positive")
    if not np.isfinite(force_per_velocity):
        raise ResultContractError("Source force must be finite")
    return complex(force_per_velocity / (source_area_m2 * real_source_copy_count(symmetry)))


def decode_complex_values(values: Any, shape: tuple[int, ...]) -> np.ndarray:
    """Decode system_result v2's finite, C-order little-endian complex array."""
    if not isinstance(values, dict):
        raise ResultContractError("Quantity values must be a binary descriptor")
    dimensions = values.get("shape")
    if (values.get("encoding") != "base64" or values.get("order") != "C"
            or values.get("byte_order") != "little"
            or values.get("dtype") not in {"complex64", "complex128"}
            or not isinstance(dimensions, list) or any(type(n) is not int for n in dimensions)
            or dimensions != list(shape)):
        raise ResultContractError("Quantity encoding, dtype or shape changed")
    payload = values.get("content_base64")
    if not isinstance(payload, str):
        raise ResultContractError("Quantity binary payload is missing")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ResultContractError("Quantity binary payload is invalid base64") from exc
    dtype = np.dtype("<c8" if values["dtype"] == "complex64" else "<c16")
    if len(raw) != math.prod(shape) * dtype.itemsize:
        raise ResultContractError("Quantity binary payload has the wrong byte count")
    array = np.frombuffer(raw, dtype=dtype).reshape(shape).astype(np.complex128)
    if not np.isfinite(array).all():
        raise ResultContractError("Quantity contains non-finite complex values")
    return array


def result_outputs(
    layout: ObservationLayout, *, surface_traces: bool = False, driver_loading: bool = False,
) -> list[dict[str, Any]]:
    """Outputs in the order parse_frequency expects, without engine imports."""
    outputs = layout.exterior_outputs()
    quantities = [("impedance", "radiation_impedance")]
    if surface_traces or driver_loading:
        quantities.append(("surface:pressure", "bem_boundary_pressure"))
    if surface_traces:
        quantities.append(("surface:neumann", "bem_boundary_neumann"))
    return outputs + [
        {"id": name, "quantity": quantity, "target_ids": [], "options": {}}
        for name, quantity in quantities
    ]


@dataclass
class FrequencyResult:
    frequency_hz: float
    pressure_complex: np.ndarray
    impedance: complex
    radiation_impedance: complex | None
    sphere_pressure_complex: np.ndarray | None
    surface_pressure_complex: np.ndarray | None
    surface_neumann_complex: np.ndarray | None
    diagnostics: dict[str, Any]
    generalized_impedances: dict[str, complex] | None = None

    @property
    def spl_db(self) -> np.ndarray:
        with np.errstate(divide="ignore"):
            return 20.0 * np.log10(np.abs(self.pressure_complex) / 20e-6)

    def log_entry(self, layout: ObservationLayout) -> dict[str, Any]:
        return {"frequency_hz": self.frequency_hz, "converged": True,
                "observation_angles_deg": layout.angles_deg,
                "observation_planes": list(layout.planes),
                "observation_pressure_complex": self.pressure_complex,
                "observation_spl_db": self.spl_db, "impedance": self.impedance,
                "timings": self.diagnostics.get("timings", {}),
                "native_diagnostics": self.diagnostics,
                "generalized_impedances": self.generalized_impedances}


def parse_frequency(
    result: Any, *, frequency_hz: float, layout: ObservationLayout,
    source_area_m2: float, excitation_port_id: str, symmetry: str = "off",
    precision: str = "float32", backend: str = "cpu",
    trace_counts: tuple[int, int] | None = None, source_motion: str = "normal",
    mean_pressure_velocity: complex | None = None, boundary_loading: BoundaryLoading | None = None,
    excitation_port_ids: tuple[str, ...] | None = None,
    channel_port_ids: tuple[str, ...] | None = None,
    _decoded_quantities: dict[str, np.ndarray] | None = None,
) -> FrequencyResult:
    """Map a unit-velocity basis or a WG channel's sum of independent ports.

    BoundaryLoading derives historical pressure loading before acceleration
    scaling, without applying generalized-force motion projections. Pressure
    traces can be requested solely for loading without retaining field traces.
    """
    scale = acceleration_scale(frequency_hz)
    if not math.isfinite(source_area_m2) or source_area_m2 <= 0:
        raise ValueError("Source area must be finite and positive")
    if precision not in {"float32", "float64"}:
        raise ValueError("Result precision must be float32 or float64")
    real_source_copy_count(symmetry)
    if (not isinstance(result, dict) or type(result.get("schema_version")) is not int
            or result["schema_version"] != 2):
        raise ResultContractError("Expected system_result schema version 2")
    echoed = result.get("freq_hz")
    scalar = np.float32 if precision == "float32" else np.float64
    with np.errstate(over="ignore", under="ignore"):
        wire_frequency = scalar(frequency_hz)
    if not np.isfinite(wire_frequency) or wire_frequency <= 0:
        raise ValueError("Frequency is invalid at solver precision")
    if (type(echoed) not in {float, int} or not math.isfinite(echoed)
            or scalar(echoed) != wire_frequency):
        raise ResultContractError("Result frequency is out of order")
    ports = excitation_port_ids or (excitation_port_id,)
    channel_ports = channel_port_ids or (excitation_port_id,)
    if (len(set(ports)) != len(ports) or not channel_ports
            or len(set(channel_ports)) != len(channel_ports) or set(channel_ports) - set(ports)):
        raise ValueError("Channel ports must be unique members of the compiled excitation list")
    indices = [ports.index(port) for port in channel_ports]
    if result.get("excitation_port_ids") != list(ports):
        raise ResultContractError("Result excitation identity or count changed")
    diagnostics = result.get("diagnostics")
    expected = {"phasor_convention": SOLVER_TIME_CONVENTION, "symmetry": symmetry,
                "precision": precision, "bem_backend": backend}
    if not isinstance(diagnostics, dict) or any(diagnostics.get(k) != v for k, v in expected.items()):
        raise ResultContractError("Result phasor, symmetry, precision or backend changed")
    outputs = result_outputs(layout, surface_traces=trace_counts is not None,
                             driver_loading=boundary_loading is not None)
    items = result.get("quantities")
    if (not isinstance(items, list) or any(not isinstance(item, dict) for item in items)
            or [item.get("id") for item in items] != [item["id"] for item in outputs]):
        raise ResultContractError("Result outputs changed order, identity or count")
    by_id = {item["id"]: item for item in items}

    def quantity(name: str, kind: str, unit: str, axis: str, count: int) -> np.ndarray:
        item = by_id[name]
        axes = [axis] if axis == "radiator" else ["excitation", axis]
        shape = (count,) if axis == "radiator" else (len(ports), count)
        if (item.get("quantity"), item.get("unit"), item.get("axes"), item.get("target_id")) != (
                kind, unit, axes, None):
            raise ResultContractError(f"{name} quantity, unit, axes or target changed")
        values = item.get("values")
        array = (_decoded_quantities.get(name) if _decoded_quantities is not None else None)
        if array is None:
            array = decode_complex_values(values, shape)
            if _decoded_quantities is not None:
                _decoded_quantities[name] = array
        if values["dtype"] != ("complex64" if precision == "float32" else "complex128"):
            raise ResultContractError(f"{name} precision changed")
        return array if axis == "radiator" else np.sum(array[indices], axis=0)

    pressure = np.stack([
        quantity(f"pressure:{plane}", "exterior_pressure", "Pa", "observation", len(layout.angles_deg))
        for plane in layout.planes
    ]) * scale
    sphere = None
    if "sphere" in layout.points_m:
        sphere = quantity("pressure:sphere", "exterior_pressure", "Pa", "observation",
                          len(layout.points_m["sphere"])) * scale
    forces = quantity("impedance", "radiation_impedance", "N*s/m", "radiator", len(ports))
    generalized = dict(zip(ports, (complex(value) for value in forces)))
    force = generalized[channel_ports[0]] if len(channel_ports) == 1 else None
    surface_pressure = surface_neumann = None
    if trace_counts is not None:
        if len(trace_counts) != 2 or any(type(n) is not int or n <= 0 for n in trace_counts):
            raise ValueError("Trace counts must be positive (node, face) integers")
        if boundary_loading is not None and trace_counts != (
                boundary_loading.node_count, boundary_loading.face_count):
            raise ValueError("Trace counts differ from compiled loading topology")
    if boundary_loading is not None or trace_counts is not None:
        node_count = boundary_loading.node_count if boundary_loading is not None else trace_counts[0]
        boundary_pressure = quantity("surface:pressure", "bem_boundary_pressure", "Pa",
                                     "bem_node", node_count)
        if boundary_loading is not None:
            if mean_pressure_velocity is not None:
                raise ValueError("Supply boundary loading or a precomputed mean, not both")
            mean_pressure_velocity = boundary_loading.mean_pressure(boundary_pressure)
        if trace_counts is not None:
            surface_pressure = boundary_pressure * scale
            surface_neumann = quantity("surface:neumann", "bem_boundary_neumann", "Pa/m",
                                       "bem_face", trace_counts[1]) * scale
    if mean_pressure_velocity is None:
        if source_motion != "normal" or force is None:
            raise ResultContractError("W4 requires boundary-derived mean pressure for non-normal motion")
        mean_pressure_velocity = mean_pressure_from_force(
            force, source_area_m2=source_area_m2, symmetry=symmetry)
    elif not np.isfinite(mean_pressure_velocity):
        raise ResultContractError("Boundary-derived mean pressure must be finite")
    return FrequencyResult(frequency_hz, pressure, complex(mean_pressure_velocity * scale),
                           force, sphere, surface_pressure, surface_neumann, diagnostics, generalized)


def parse_compiled_frequency(result: Any, request: CompiledRequest, *, frequency_hz: float
                             ) -> dict[str, FrequencyResult]:
    """Synthesize WG channels while retaining each engine generalized impedance.

    Official radiation_impedance contains each component's self loading only.
    A multi-source channel has no scalar generalized force here: cross loading
    cannot be inferred by summing those diagonal entries.
    """
    options = request.wire["solver_options"]
    ports = tuple(request.wire["excitation_port_ids"])
    counts = (len(request.mesh.points_m), len(request.mesh.faces)) if request.surface_traces else None
    decoded: dict[str, np.ndarray] = {}
    return {
        channel: parse_frequency(
            result, frequency_hz=frequency_hz, layout=request.layout,
            source_area_m2=request.channel_loading[channel].area_m2,
            excitation_port_id=members[0], excitation_port_ids=ports, channel_port_ids=members,
            symmetry=options["symmetry"], precision=options["precision"], backend=options["bem_backend"],
            trace_counts=counts, boundary_loading=request.channel_loading[channel],
            _decoded_quantities=decoded)
        for channel, members in request.channel_ports.items()
    }


@dataclass
class SweepResult:
    frequencies_hz: np.ndarray
    pressure_complex: np.ndarray
    spl_db: np.ndarray
    impedance: np.ndarray
    observation_angles_deg: np.ndarray
    observation_planes: list[str]
    sphere_pressure_complex: np.ndarray | None
    sphere_theta_deg: np.ndarray | None
    sphere_phi_deg: np.ndarray | None
    surface_pressure_complex: np.ndarray | None
    surface_neumann_complex: np.ndarray | None
    cancelled: bool
    requested_frequency_count: int
    solver_log: list[dict[str, Any]]
    radiation_impedance: np.ndarray | None = None

    timings: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        totals: dict[str, float] = {}
        for entry in self.solver_log:
            for name, value in entry.get("timings", {}).items():
                if isinstance(value, (int, float)) and math.isfinite(value):
                    totals[name] = totals.get(name, 0.0) + value
        self.timings = totals

    @property
    def is_partial(self) -> bool:
        return len(self.frequencies_hz) < self.requested_frequency_count

    @property
    def directivity_reference_index(self) -> int:
        return int(np.argmin(np.abs(self.observation_angles_deg)))

    @property
    def directivity_reference_deg(self) -> float:
        return float(self.observation_angles_deg[self.directivity_reference_index])

    @property
    def directivity_db(self) -> np.ndarray:
        amplitudes = np.maximum(np.abs(self.pressure_complex), 20e-6 * 1e-6)
        levels = 20.0 * np.log10(amplitudes / 20e-6)
        return levels - levels[:, :, self.directivity_reference_index, None]

    @property
    def spl_norm_db(self) -> np.ndarray:
        return self.directivity_db


def map_sweep(
    events: Iterable[dict[str, Any]], frequencies_hz: Iterable[float], *,
    layout: ObservationLayout, source_area_m2: float, excitation_port_id: str,
    symmetry: str = "off", precision: str = "float32", backend: str = "cpu",
    trace_counts: tuple[int, int] | None = None,
    boundary_loading: BoundaryLoading | None = None, source_motion: str = "normal",
    progress_callback: Callable[[int, int, float], None] | None = None,
    on_frequency_result: Callable[[int, float, dict[str, Any]], bool | None] | None = None,
    request_cancel: Callable[[], None] | None = None,
    compiled_request: CompiledRequest | None = None, channel_id: str | None = None,
    _channel_callbacks: Mapping[str, tuple[Callable | None, Callable | None]] | None = None,
) -> SweepResult:
    """Consume and close an owned stream, including on callback/decoder failure.

    Preserve execution order and the requested Float64 axis. Only cancellation
    permits a short prefix. Session staging, retirement and admission remain
    beat_runtime responsibilities; closing here uses the public stream API.
    """
    stream = iter(events)
    try:
        channels = tuple(_channel_callbacks) if _channel_callbacks is not None else (channel_id,)
        if _channel_callbacks is not None and (compiled_request is None or not channels
                or any(name not in compiled_request.channel_ports for name in channels)):
            raise ValueError("Compiled sweep requires known WG channels")
        if _channel_callbacks is None and compiled_request is not None and channel_id not in compiled_request.channel_ports:
            raise ValueError("Compiled sweep requires a named WG channel")
        frequencies = np.asarray(list(frequencies_hz), dtype=float)
        if (frequencies.ndim != 1 or frequencies.size == 0 or not np.isfinite(frequencies).all()
                or np.any(frequencies <= 0) or np.unique(frequencies).size != frequencies.size):
            raise ValueError("Frequencies must be a nonempty, finite, positive, unique list")
        if precision not in {"float32", "float64"}:
            raise ValueError("Result precision must be float32 or float64")
        if not math.isfinite(source_area_m2) or source_area_m2 <= 0:
            raise ValueError("Source area must be finite and positive")
        real_source_copy_count(symmetry)
        if trace_counts is not None and (
                len(trace_counts) != 2 or any(type(n) is not int or n <= 0 for n in trace_counts)):
            raise ValueError("Trace counts must be positive (node, face) integers")
        with np.errstate(over="ignore", under="ignore"):
            wire_frequencies = frequencies.astype(precision)
        if not np.isfinite(wire_frequencies).all() or np.any(wire_frequencies <= 0):
            raise ValueError("Frequencies are invalid at solver precision")
        channel_rows = {name: [] for name in channels}
        channel_logs = {name: [] for name in channels}
        rows = channel_rows[channels[0]]
        terminal = None
        for event in stream:
            if terminal is not None:
                raise ResultContractError("Worker sent events after a terminal event")
            if not isinstance(event, dict):
                raise ResultContractError("Worker event must be an object")
            kind = event.get("type")
            if kind in {"completed", "cancelled"}:
                count = event.get("solved_count")
                if type(count) is not int or count != len(rows):
                    raise ResultContractError("Terminal solved count differs from received rows")
                if kind == "completed" and len(rows) != len(frequencies):
                    raise ResultContractError("Completion is missing requested frequencies")
                terminal = kind
                continue
            if kind == "result":
                index = len(rows)
                if index >= len(frequencies):
                    raise ResultContractError("Worker returned extra frequency rows")
                if compiled_request is not None:
                    parsed = parse_compiled_frequency(
                        event.get("result"), compiled_request, frequency_hz=float(frequencies[index]))
                else:
                    parsed = {channel_id: parse_frequency(
                        event.get("result"), frequency_hz=float(frequencies[index]), layout=layout,
                        source_area_m2=source_area_m2, excitation_port_id=excitation_port_id,
                        symmetry=symmetry, precision=precision, backend=backend, trace_counts=trace_counts,
                        boundary_loading=boundary_loading, source_motion=source_motion)}
                stopping = False
                for name in channels:
                    row = parsed[name]
                    channel_rows[name].append(row)
                    log = row.log_entry(layout)
                    channel_logs[name].append(log)
                    progress, publish = (_channel_callbacks[name] if _channel_callbacks is not None
                                         else (progress_callback, on_frequency_result))
                    if progress:
                        progress(index, len(frequencies), row.frequency_hz)
                    if publish and publish(index, row.frequency_hz, log) is False:
                        stopping = True
                if stopping:
                    if request_cancel is None:
                        raise ValueError("A stopping result callback requires request_cancel")
                    request_cancel()
            elif kind == "failed":
                raise ResultContractError(f"Official BEAT solve failed: {event.get('error', event)}")
            elif kind not in {"status", "progress"}:
                raise ResultContractError(f"Unknown worker event {kind!r}")
        if terminal is None:
            raise ResultContractError("Stream ended without a completed or cancelled event")

        def finish(name):
            rows, logs = channel_rows[name], channel_logs[name]
            def stack(field: str, shape: tuple[int, ...]) -> np.ndarray:
                return (np.stack([getattr(row, field) for row in rows]) if rows
                        else np.empty((0, *shape), dtype=np.complex128))

            pressure = stack("pressure_complex", (len(layout.planes), len(layout.angles_deg)))
            with np.errstate(divide="ignore"):
                spl = 20.0 * np.log10(np.abs(pressure) / 20e-6)
            return SweepResult(
                frequencies[:len(rows)], pressure, spl, np.asarray([r.impedance for r in rows], dtype=complex),
                layout.angles_deg, list(layout.planes),
                stack("sphere_pressure_complex", (len(layout.points_m["sphere"]),))
                if "sphere" in layout.points_m else None,
                layout.sphere_theta_deg, layout.sphere_phi_deg,
                stack("surface_pressure_complex", (trace_counts[0],)) if trace_counts else None,
                stack("surface_neumann_complex", (trace_counts[1],)) if trace_counts else None,
                terminal == "cancelled", len(frequencies), logs,
                np.asarray([row.radiation_impedance for row in rows], dtype=complex))
        results = {name: finish(name) for name in channels}
        return results if _channel_callbacks is not None else results[channel_id]
    finally:
        close = getattr(events, "close", None) or getattr(stream, "close", None)
        if close is not None:
            close()


@dataclass
class CoupledSweep:
    channels: dict[str, SweepResult]
    nodal_pressure: np.ndarray  # frequency, channel, concatenated FEM node; per-channel drive basis
    cancelled: bool
    velocity: np.ndarray | None = None  # frequency, basis, transducer; native RMS
    current: np.ndarray | None = None
    generalized_load: np.ndarray | None = None  # frequency, transducer, component


def map_coupled_sweep(events, request, *, progress_callback=None, on_frequency_result=None,
                      request_cancel=None) -> CoupledSweep:
    """Validate contract-2 identities and node ordering before retaining any row."""
    frequencies = request.wire["frequencies_hz"]
    scalar = np.float32 if request.wire["solver_options"]["precision"] == "float32" else np.float64
    ports = request.wire["excitation_port_ids"]
    outputs = request.wire["outputs"]
    domains = request.regions
    counts = [len(item.domain.points_mm) for item in domains]
    metadata = {"mesh_ids": [f"mesh:fem:{item.domain.region_id}" for item in domains],
                "region_ids": [f"region:fem:{item.domain.region_id}" for item in domains],
                "node_counts": counts, "node_offsets": np.cumsum([0, *counts[:-1]]).tolist()}
    system = request.wire["compiled_system"]
    components = system["components"]
    drivers = [c for c in components if c["kind"] == "electrodynamic_transducer"]
    port_records = system["excitation_ports"]
    scales = np.asarray([1 if p["kind"] == "voltage" else 0 for p in port_records])
    velocities, currents, loads = [], [], []
    rows, fields, logs = [], [], []
    terminal = None
    stream = iter(events)
    try:
        for event in stream:
            if terminal is not None or not isinstance(event, dict):
                raise ResultContractError("Invalid event after terminal or non-object event")
            kind = event.get("type")
            if kind in {"completed", "cancelled"}:
                if (type(event.get("solved_count")) is not int or event["solved_count"] != len(rows)
                        or kind == "completed" and len(rows) != len(frequencies)):
                    raise ResultContractError("Terminal count differs from requested/received frequencies")
                terminal = kind
            elif kind == "result":
                index = len(rows)
                raw = event.get("result")
                if (index >= len(frequencies) or not isinstance(raw, dict)
                        or type(raw.get("schema_version")) is not int or raw["schema_version"] != 2
                        or type(raw.get("freq_hz")) not in {int, float}
                        or not math.isfinite(raw["freq_hz"])
                        or scalar(raw["freq_hz"]) != scalar(frequencies[index])
                        or raw.get("excitation_port_ids") != ports):
                    raise ResultContractError("Coupled result frequency, version or excitation identity changed")
                diagnostics = raw.get("diagnostics")
                expected = request.wire["solver_options"]
                backend_key = ("bem_backend" if any(r["kind"] == "unbounded_air"
                               for r in request.wire["compiled_system"]["regions"]) else "linear_backend")
                if (not isinstance(diagnostics, dict) or diagnostics.get(backend_key) != expected["bem_backend"]
                        or any(diagnostics.get(k) != expected[k]
                               for k in ("phasor_convention", "precision", "symmetry"))):
                    raise ResultContractError("Coupled result physics changed")
                quantities = raw.get("quantities")
                if (not isinstance(quantities, list) or any(not isinstance(q, dict) for q in quantities)
                        or [q.get("id") for q in quantities] != [o["id"] for o in outputs]):
                    raise ResultContractError("Coupled result outputs changed")
                decoded = {}
                for output, item in zip(outputs, quantities):
                    fem = output["quantity"] == "fem_nodal_pressure"
                    transducer = output["quantity"] in {"diaphragm_velocity", "voice_coil_current"}
                    axis = "fem_node" if fem else "transducer" if transducer else "observation"
                    count = sum(counts) if fem else len(drivers) if transducer else len(output["options"]["points_m"])
                    unit = "m/s" if output["quantity"] == "diaphragm_velocity" else "A" if transducer else "Pa"
                    if (item.get("quantity") != output["quantity"] or item.get("unit") != unit
                            or item.get("target_id") is not None
                            or item.get("axes") != ["excitation", axis]
                            or not isinstance(item.get("values"), dict)
                            or item["values"].get("dtype") != (
                                "complex64" if expected["precision"] == "float32" else "complex128")):
                        raise ResultContractError("Coupled quantity identity, unit or precision changed")
                    if fem and item.get("metadata") != metadata:
                        raise ResultContractError("FEM result node layout differs from retained domains")
                    if transducer:
                        expected_meta = {"component_ids": [c["id"] for c in drivers]}
                        if backend_key == "bem_backend":
                            expected_meta.update(surface_completion_factors=[1] * len(drivers),
                                                 physical_driver_orbit_counts=[1] * len(drivers))
                        if item.get("metadata") != expected_meta:
                            raise ResultContractError("Coupled transducer identity or physical-copy accounting changed")
                    decoded[output["id"]] = decode_complex_values(item["values"], (len(ports), count))
                if drivers:
                    from .coupled_transducers import recover_load

                    voltage = diagnostics.get("transducer_reference_voltage_v")
                    if (type(voltage) not in {int, float} or not math.isfinite(voltage)
                            or scalar(voltage) != scalar(expected["transducer_reference_voltage_v"])):
                        raise ResultContractError("Coupled reference voltage changed")
                    velocities.append(decoded.pop("diaphragm_velocity"))
                    currents.append(decoded.pop("voice_coil_current"))
                    medium = domains[0].record["medium"]
                    loads.append(recover_load(components, port_records, velocities[-1], currents[-1],
                                             frequencies[index], density=medium["density_kg_per_m3"],
                                             sound_speed=medium["sound_speed_m_per_s"]))
                scale = np.where(scales, 1, acceleration_scale(frequencies[index]))[:, None]
                fields.append(decoded.pop("fem:pressure") * scale)
                rows.append({key: value * scale for key, value in decoded.items()})
                logs.append({"frequency_hz": frequencies[index], "converged": True,
                             "native_diagnostics": diagnostics, "timings": diagnostics.get("timings", {})})
                if progress_callback:
                    progress_callback(index, len(frequencies), frequencies[index])
                if on_frequency_result and on_frequency_result(index, frequencies[index], rows[-1]) is False:
                    if request_cancel is None:
                        raise ValueError("A stopping callback requires request_cancel")
                    request_cancel()
            elif kind == "failed":
                raise ResultContractError(f"Official BEAT coupled solve failed: {event.get('error')}")
            elif kind not in {"status", "progress"}:
                raise ResultContractError(f"Unknown coupled event {kind!r}")
        if terminal is None:
            raise ResultContractError("Coupled stream ended without a terminal event")
        channels = {}
        for channel, members in request.channel_ports.items():
            port_index = ports.index(members[0])
            pressure = (np.stack([np.stack([row[f"pressure:{p}"][port_index]
                         for p in request.layout.planes]) for row in rows]) if rows and f"pressure:{request.layout.planes[0]}" in rows[0]
                        else np.zeros((len(rows), len(request.layout.planes), len(request.layout.angles_deg)), complex))
            sphere = (np.stack([row["pressure:sphere"][port_index] for row in rows])
                      if rows and "pressure:sphere" in rows[0] else None)
            with np.errstate(divide="ignore"):
                spl = 20 * np.log10(np.abs(pressure) / 20e-6)
            channels[channel] = SweepResult(
                np.asarray(frequencies[:len(rows)]), pressure, spl,
                np.full(len(rows), complex(np.nan, np.nan)), request.layout.angles_deg,
                list(request.layout.planes), sphere, request.layout.sphere_theta_deg,
                request.layout.sphere_phi_deg, None, None, terminal == "cancelled", len(frequencies), logs)
        nodal = np.stack(fields) if fields else np.empty((0, len(ports), sum(counts)), complex)
        return CoupledSweep(channels, nodal, terminal == "cancelled",
                            np.asarray(velocities).reshape(len(rows), len(ports), len(drivers)) if drivers else None,
                            np.asarray(currents).reshape(len(rows), len(ports), len(drivers)) if drivers else None,
                            np.asarray(loads).reshape(len(rows), len(drivers), len(components)) if drivers else None)
    finally:
        close = getattr(events, "close", None) or getattr(stream, "close", None)
        if close:
            close()


def coupled_envelope(sweep, request, *, context, geometry, record, config, started):
    """Map ideal and voltage drives, including FEM-only drives, to the channel envelope."""
    from server.solver.result_mapping import build_solver_response
    from server.solver.imported_channels import channel_source_identity
    from server.solver.combine import combine_drive_channels
    from server.solver.coupled_system import solve_kind
    from server.solver.official_beat import engine_version

    if geometry.combine is not None and geometry.combine.id in request.channel_ports:
        raise ValueError(f"Combine id {geometry.combine.id!r} collides with a compiled drive channel id")
    channels = {}
    formulation = solve_kind(request.wire["compiled_system"])
    options = request.wire["solver_options"]
    backend, precision = options["bem_backend"], options["precision"]
    engine = f"beat-{backend}"
    solver_engine = {"provider": "official", "engine": engine, "package": "beat-engine",
                     "package_version": engine_version(), "device": backend,
                     "precision": "double" if precision == "float64" else "single",
                     "formulation": formulation}
    fem_metadata = {"engine": engine, "backend": backend, "precision": precision}
    reroute = request.wire["compiled_system"].get("metadata", {}).get("fem_execution_reroute")
    if reroute:
        fem_metadata["execution_reroute"] = reroute
    has_voltage = "voltage_rms" in request.drive_normalization.values()

    channel_warnings = request.wire["compiled_system"]["metadata"].get("channel_warnings", {})
    specs = {c.id: c for c in geometry.drive_channels}
    identity = channel_source_identity(geometry, record)
    speed = request.regions[0].record["medium"]["sound_speed_m_per_s"]
    for name, native in sweep.channels.items():
        spec = specs.get(name)
        response = build_solver_response(
            result=native, config=config, context=context, start_time=started,
            metadata={"drive_channel_id": name, "source_ids": list(spec.source_ids) if spec else [],
                      **identity.get(name, {"role": None}), "solver_backend": engine,
                      "solver_engine": solver_engine, "fem": fem_metadata,
                      "warnings": list(channel_warnings.get(name, ())),
                      "phase_time_convention": PHASE_TIME_CONVENTION,
                      "pressure_normalization": "voltage_rms" if request.drive_normalization[name] == "voltage_rms"
                      else "unit_acceleration_m_per_s2"},
            sound_speed_m_per_s=speed)
        response.pop("impedance", None)
        if request.drive_normalization[name] == "voltage_rms":
            from server.solver.result_mapping import map_coupled_transducer_response

            map_coupled_transducer_response(response, sweep, request, name, spec)
        channels[name] = response
    if geometry.combine is not None and not sweep.cancelled:
        spec = geometry.combine
        resolved = spec.resolved()
        combined, payload = combine_drive_channels(
            sweep.channels, members=list(spec.members), channels=resolved["channels"],
            reference=resolved["reference"],
            member_roles={name: identity.get(name, {}).get("role") for name in spec.members})
        response = build_solver_response(
            result=combined, config=config, context=context, start_time=started,
            metadata={"drive_channel_id": spec.id, "derived_from_channels": list(spec.members),
                      "source_ids": [source for name in spec.members for source in specs[name].source_ids],
                      "combine": payload, "solver_engine": solver_engine, "fem": fem_metadata,
                      "warnings": list(channel_warnings.get(spec.id, ())),
                      "phase_time_convention": PHASE_TIME_CONVENTION}, sound_speed_m_per_s=speed)
        response.pop("impedance", None)
        channels[spec.id] = response
    first = next(iter(sweep.channels.values()))
    return {"result_kind": "multi_channel", "result_contract_version": 2,
            "channels": channels, "channel_order": list(channels),
            "frequencies": first.frequencies_hz.tolist(),
            "metadata": {"result_contract_version": 2, "geometry_type": "imported",
                         "solver_backend": "beat", "solver_engine": solver_engine,
                         "solver_mode": "full_3d",
                         "phase_time_convention": PHASE_TIME_CONVENTION,
                         "pressure_normalization": ("mixed" if len(set(request.drive_normalization.values())) > 1
                                                    else "voltage_rms" if "voltage_rms" in request.drive_normalization.values()
                                                    else "unit_acceleration_m_per_s2"),
                         "drive_normalization": request.drive_normalization,
                         "solve_path": "interior-fem" if formulation == "interior_fem" else "coupled-fem-bem",
                         "cancelled": sweep.cancelled, "exterior_only": False,
                         "fem": fem_metadata,
                         "warnings": list(request.warnings),
                         **({"beat_transducer_contract": 1,
                             "channel_bases_unavailable_reason": "native_voltage_bases_not_supported_by_legacy_recombine"}
                            if has_voltage else {}),
                         "ingest_id": geometry.ingest_id, "manifest_sha256": geometry.manifest_sha256,
                         "artifact_sha256": geometry.artifact_sha256}}
