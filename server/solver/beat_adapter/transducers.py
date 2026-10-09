"""Opt-in exterior voltage bases, kept separate from WG's acceleration results.

This first consumer slice supports complete closed solids, CPU Float64 and no
image symmetry. Mmd is bare moving mass, supplied explicitly in SI units. No
Thiele/Small inference, extra radiation mass, or second driver network is added.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any
from urllib.parse import quote

import numpy as np

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from .mesh import SurfaceMesh
from .observations import ObservationLayout
from .preflight import UnsupportedPhysics, validate_exterior_physics
from .request import SourceBasis, build_request
from .results import ResultContractError, decode_complex_values

DRIVER_SCALARS = ("re_ohm", "le_h", "bl_n_per_a", "mmd_kg", "cms_m_per_n", "rms_n_s_per_m")
MATRIX_DEFINITION = "force_per_unit_velocity; per-row physical-copy weighting in row_weights"
AREA_DEFINITION = "signed integral of motion factor over physical moving surfaces; real symmetry copies included; ground images excluded"


@dataclass(frozen=True)
class ExteriorDriver:
    """One mechanical coordinate over disjoint tagged front/rear surfaces.

    The outward mesh normal determines the signed n·axis motion on each face.
    A rear surface needs no extra minus sign. Unrequested drivers are shorted.
    """
    id: str
    tags: tuple[int, ...]
    axis: Sequence[float]
    re_ohm: float
    le_h: float
    bl_n_per_a: float
    mmd_kg: float
    cms_m_per_n: float
    rms_n_s_per_m: float

    @property
    def component_id(self) -> str:
        return f"transducer:{quote(self.id, safe='')}"

    @property
    def port_id(self) -> str:
        return f"voltage:{quote(self.id, safe='')}"


def _positive(value: Any, name: str, *, zero: bool = False) -> float:
    if (isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (value < 0 if zero else value <= 0)):
        raise UnsupportedPhysics(f"{name} must be finite and {'nonnegative' if zero else 'positive'}")
    return float(value)


def outputs(layout: ObservationLayout) -> list[dict[str, Any]]:
    return layout.exterior_outputs() + [
        {"id": kind, "quantity": kind, "target_ids": [], "options": {}}
        for kind in ("diaphragm_velocity", "voice_coil_current", "radiation_impedance_matrix")
    ]


@dataclass(frozen=True)
class TransducerRequest:
    wire: dict[str, Any]
    layout: ObservationLayout
    mesh: SurfaceMesh

    @property
    def transducer_ids(self) -> tuple[str, ...]:
        return tuple(c["id"] for c in self.wire["compiled_system"]["components"]
                     if c["kind"] == "electrodynamic_transducer")

    @property
    def matrix_ids(self) -> tuple[str, ...]:
        requested = set(self.wire["excitation_port_ids"])
        active = {p["component_id"] for p in self.wire["compiled_system"]["excitation_ports"]
                  if p["id"] in requested}
        return tuple(c["id"] for c in self.wire["compiled_system"]["components"]
                     if c["kind"] == "electrodynamic_transducer" or c["id"] in active)


def build_transducer_request(
    msh_text: str, *, drivers: Sequence[ExteriorDriver], layout: ObservationLayout,
    frame: Mapping[str, Any], frequencies_hz: Sequence[float],
    ideal_sources: Sequence[SourceBasis] = (), excitation_port_ids: Sequence[str] | None = None,
    reference_voltage_rms_v: float = 2.83, mesh_scale_to_m: float = 1.,
    sound_speed_m_per_s: float = 343., density_kg_per_m3: float = 1.2041,
) -> TransducerRequest:
    """Build independent voltage/RMS and ideal 1 m/s RMS bases in requested order.

    Explicit invocation is the opt-in. It neither changes stored jobs nor routes
    legacy driver workflows here. Electrical impedance is meaningful only for
    the driven voltage port; other drivers retain their short-circuit response.
    The engine independently verifies closed, outward-wound exterior topology.
    """
    voltage = _positive(reference_voltage_rms_v, "reference_voltage_rms_v")
    if not drivers or len({d.id for d in drivers}) != len(drivers):
        raise UnsupportedPhysics("Drivers require nonempty unique identities")
    surrogate = list(ideal_sources)
    for driver in drivers:
        if (not isinstance(driver.id, str) or not driver.id or not driver.tags
                or len(set(driver.tags)) != len(driver.tags)):
            raise UnsupportedPhysics("Driver requires an identity and unique physical tags")
        for name in DRIVER_SCALARS:
            _positive(getattr(driver, name), name, zero=name == "le_h")
        surrogate.extend(SourceBasis(f"driver:{driver.id}:{tag}", tag, "axial", driver.axis,
                                     f"driver-patch:{quote(driver.id, safe='')}:{tag}")
                         for tag in driver.tags)
    # Reuse unchanged geometry/packing/observation preparation. The temporary
    # ideal components are never submitted or used for driver loading.
    port_ids = [s.port_id or f"excitation:{s.motion}:{quote(s.source_id, safe='')}" for s in surrogate]
    prepared = build_request(
        msh_text, sources=surrogate, channel_ports={"basis": port_ids}, layout=layout,
        frame=frame, frequencies_hz=frequencies_hz, precision="float64", engine_id="beat-cpu",
        mesh_scale_to_m=mesh_scale_to_m, sound_speed_m_per_s=sound_speed_m_per_s,
        density_kg_per_m3=density_kg_per_m3,
    )
    wire = prepared.wire
    system = wire["compiled_system"]
    components = system["components"][:len(ideal_sources)]
    ports = system["excitation_ports"][:len(ideal_sources)]
    offset = len(ideal_sources)
    for driver in drivers:
        axis = system["components"][offset]["parameters"]["motion_axis"]
        offset += len(driver.tags)
        components.append({"id": driver.component_id, "name": driver.id,
                           "kind": "electrodynamic_transducer",
                           "boundary_ids": [f"boundary:tag:{tag}" for tag in driver.tags],
                           "parameters": {**{name: float(getattr(driver, name)) for name in DRIVER_SCALARS},
                                          "motion_profile": "rigid_translation", "motion_axis": axis}})
        ports.append({"id": driver.port_id, "name": driver.id,
                      "component_id": driver.component_id, "kind": "voltage"})
    requested = list(excitation_port_ids) if excitation_port_ids is not None else [d.port_id for d in drivers]
    if not requested or len(set(requested)) != len(requested) or set(requested) - {p["id"] for p in ports}:
        raise UnsupportedPhysics("Excitations require unique known voltage or ideal ports")
    system.update(contract_version=3, components=components, excitation_ports=ports,
                  metadata={"provider": system["metadata"]["provider"],
                            "wg_opt_in": "exterior-transducers-v1", "amplitude_convention": "rms",
                            "undriven_transducer_termination": "shorted"})
    wire.update(excitation_port_ids=requested, outputs=outputs(prepared.layout))
    wire["solver_options"]["transducer_reference_voltage_v"] = voltage
    request = TransducerRequest(wire, prepared.layout, prepared.mesh)
    validate_transducer_request(request)
    return request


def validate_transducer_request(request: TransducerRequest) -> None:
    """WG-only v3 admission; BEAT's existing validator remains authoritative."""
    wire = request.wire
    system, options = wire["compiled_system"], wire["solver_options"]
    if (system["contract_version"] != 3 or options.get("bem_backend") != "cpu"
            or options.get("precision") != "float64" or options.get("symmetry") != "off"):
        raise UnsupportedPhysics("WG transducer adoption currently requires v3 CPU float64 symmetry off")
    _positive(options.get("transducer_reference_voltage_v"), "transducer_reference_voltage_v")
    if wire["outputs"] != outputs(request.layout):
        raise UnsupportedPhysics("WG transducer outputs must match the requested voltage-basis layout")
    # Apply existing strict exterior policy to a private ideal-only view. No
    # original bytes, legacy validation rules, or engine functions are changed.
    view = deepcopy(wire)
    view["compiled_system"]["contract_version"] = 2
    del view["solver_options"]["transducer_reference_voltage_v"]
    owned: set[tuple[str, int, int]] = set()
    groups = {b["id"]: (b["group"]["mesh_id"], b["group"]["dimension"], b["group"]["tag"])
              for b in system["boundaries"]}
    kinds = {}
    for component in view["compiled_system"]["components"]:
        kinds[component["id"]] = component["kind"]
        boundaries = component["boundary_ids"]
        if not boundaries or any(b not in groups for b in boundaries):
            raise UnsupportedPhysics("Moving components require known boundary references")
        physical = [groups[b] for b in boundaries]
        if len(set(physical)) != len(physical) or owned.intersection(physical):
            raise UnsupportedPhysics("Moving boundaries require disjoint physical group ownership")
        owned.update(physical)
        if component["kind"] == "electrodynamic_transducer":
            params = component["parameters"]
            if set(params) != {*DRIVER_SCALARS, "motion_profile", "motion_axis"}:
                raise UnsupportedPhysics("WG supports explicit bare driver scalars and rigid translation only")
            for name in DRIVER_SCALARS:
                _positive(params[name], name, zero=name == "le_h")
            if params["motion_profile"] != "rigid_translation":
                raise UnsupportedPhysics("WG transducers require rigid_translation")
            component["kind"] = "ideal_velocity_source"
            component["parameters"] = {name: params[name] for name in ("motion_profile", "motion_axis")}
    if not request.transducer_ids:
        raise UnsupportedPhysics("Opt-in transducer request requires an electrodynamic driver")
    for port in view["compiled_system"]["excitation_ports"]:
        expected = "voltage" if kinds.get(port["component_id"]) == "electrodynamic_transducer" else "normal_velocity"
        if port["kind"] != expected:
            raise UnsupportedPhysics("Port kind must match its component's voltage or velocity basis")
        port["kind"] = "normal_velocity"
    validate_exterior_physics(view)


@dataclass(frozen=True)
class RadiationMatrix:
    """Mechanical N*s/m matrix with engine-defined force/physical-copy metadata."""
    values: np.ndarray
    component_ids: tuple[str, ...]
    metadata: dict[str, Any]

    def acoustic_impedance(self) -> np.ndarray:
        """Convert with signed areas; cancelling dipoles have no scalar flow."""
        areas = np.asarray(self.metadata["effective_volume_area_m2"], dtype=float)
        ratios = np.asarray(self.metadata["effective_volume_area_cancellation_ratio"], dtype=float)
        if np.any(areas == 0) or np.any(ratios <= self.metadata["effective_volume_area_cancellation_relative_tolerance"]):
            raise ResultContractError("Cannot convert zero or near-cancelling signed volume area")
        weights = np.asarray(self.metadata["row_weights"], dtype=float)
        return weights[:, None] * self.values / (areas[:, None] * areas[None, :])


@dataclass(frozen=True)
class TransducerFrequency:
    frequency_hz: float
    excitation_port_ids: tuple[str, ...]
    transducer_ids: tuple[str, ...]
    reference_voltage_rms_v: float
    pressure_rms_pa: dict[str, np.ndarray]  # [excitation, observation]
    velocity_rms_m_per_s: np.ndarray  # [excitation, transducer]
    current_rms_a: np.ndarray  # [excitation, transducer]
    radiation: RadiationMatrix
    diagnostics: dict[str, Any]
    voltage_component_ids: tuple[str | None, ...]

    @property
    def displacement_rms_m(self) -> np.ndarray:
        return self.velocity_rms_m_per_s / (-1j * 2 * np.pi * self.frequency_hz)

    @property
    def excursion_peak_mm(self) -> np.ndarray:
        return np.sqrt(2) * 1000 * np.abs(self.displacement_rms_m)

    def input_impedance_ohm(self, port_id: str) -> complex | None:
        """V/I for the driven driver's port; zero current means undefined."""
        index = self.excitation_port_ids.index(port_id)
        component = self.voltage_component_ids[index]
        if component is None:
            raise ValueError("An ideal velocity port has no electrical input impedance")
        current = self.current_rms_a[index, self.transducer_ids.index(component)]
        return complex(self.reference_voltage_rms_v / current) if current != 0 else None

    def spl_db(self, observation: str) -> np.ndarray:
        with np.errstate(divide="ignore"):
            return 20 * np.log10(np.abs(self.pressure_rms_pa[observation]) / 20e-6)


def parse_transducer_frequency(raw: Any, request: TransducerRequest, frequency_hz: float) -> TransducerFrequency:
    """Validate typed axes/identities and retain native RMS values without scaling."""
    wire = request.wire
    ports, drivers, matrix_ids = wire["excitation_port_ids"], request.transducer_ids, request.matrix_ids
    if (not isinstance(raw, dict) or type(raw.get("schema_version")) is not int
            or raw["schema_version"] != 2 or type(raw.get("freq_hz")) not in (float, int)
            or raw["freq_hz"] != frequency_hz or raw.get("excitation_port_ids") != ports):
        raise ResultContractError("Transducer result version, frequency or excitation order changed")
    diagnostics = raw.get("diagnostics")
    if not isinstance(diagnostics, dict) or any(diagnostics.get(k) != wire["solver_options"][k]
            for k in ("precision", "bem_backend", "symmetry", "phasor_convention")):
        raise ResultContractError("Transducer result precision, backend, symmetry or phasor changed")
    items = raw.get("quantities")
    if (not isinstance(items, list) or any(not isinstance(q, dict) for q in items)
            or [q.get("id") for q in items] != [q["id"] for q in wire["outputs"]]):
        raise ResultContractError("Transducer result output order or identities changed")
    pressure, velocity, current, matrix = {}, None, None, None
    for output, item in zip(wire["outputs"], items):
        kind = output["quantity"]
        if kind == "exterior_pressure":
            unit, axes, shape = "Pa", ["excitation", "observation"], (len(ports), len(output["options"]["points_m"]))
        elif kind == "radiation_impedance_matrix":
            unit, axes, shape = "N*s/m", ["receiver_component", "source_component"], (len(matrix_ids), len(matrix_ids))
        else:
            unit = "m/s" if kind == "diaphragm_velocity" else "A"
            axes, shape = ["excitation", "transducer"], (len(ports), len(drivers))
        if (item.get("quantity"), item.get("unit"), item.get("axes"), item.get("target_id")) != (kind, unit, axes, None):
            raise ResultContractError(f"{kind} unit, axes, quantity or target changed")
        descriptor = item.get("values")
        if not isinstance(descriptor, dict) or descriptor.get("dtype") != "complex128":
            raise ResultContractError(f"{kind} requires complex128")
        values = decode_complex_values(descriptor, shape)
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            raise ResultContractError(f"{kind} requires metadata")
        if kind == "exterior_pressure":
            pressure[output["id"].removeprefix("pressure:")] = values
        elif kind in {"diaphragm_velocity", "voice_coil_current"}:
            if (metadata.get("component_ids") != list(drivers)
                    or metadata.get("surface_completion_factors") != [1] * len(drivers)
                    or metadata.get("physical_driver_orbit_counts") != [1] * len(drivers)):
                raise ResultContractError(f"{kind} transducer identity or physical-copy accounting changed")
            if kind == "diaphragm_velocity":
                velocity = values
            else:
                current = values
        else:
            _validate_matrix_metadata(metadata, request)
            matrix = RadiationMatrix(values, matrix_ids, deepcopy(metadata))
    component_by_port = {p["id"]: p["component_id"] if p["kind"] == "voltage" else None
                         for p in wire["compiled_system"]["excitation_ports"]}
    return TransducerFrequency(frequency_hz, tuple(ports), drivers,
                              wire["solver_options"]["transducer_reference_voltage_v"],
                              pressure, velocity, current, matrix, deepcopy(diagnostics),
                              tuple(component_by_port[p] for p in ports))


def _validate_matrix_metadata(metadata: dict, request: TransducerRequest) -> None:
    ids = request.matrix_ids
    components = {c["id"]: c["kind"] for c in request.wire["compiled_system"]["components"]}
    expected = {"component_ids": list(ids), "kinds": [components[i] for i in ids],
                "row_weights": [1] * len(ids), "surface_completion_factors": [1] * len(ids),
                "physical_driver_orbit_counts": [1] * len(ids), "definition": MATRIX_DEFINITION,
                "phasor_convention": SOLVER_TIME_CONVENTION,
                "effective_volume_area_definition": AREA_DEFINITION,
                "effective_volume_area_cancellation_relative_tolerance": 1e-2}
    if any(metadata.get(k) != v for k, v in expected.items()):
        raise ResultContractError("Radiation matrix identities, definition or physical-copy accounting changed")
    for name in ("effective_volume_area_m2", "effective_volume_area_cancellation_ratio"):
        values = metadata.get(name)
        if (not isinstance(values, list) or len(values) != len(ids)
                or any(type(v) not in (float, int) or not math.isfinite(v) for v in values)):
            raise ResultContractError(f"Invalid matrix metadata {name}")
    ratios = metadata["effective_volume_area_cancellation_ratio"]
    flags = metadata.get("effective_volume_area_zero_or_near_cancelling")
    if (any(r < 0 or r > 1 + 1e-12 for r in ratios) or not isinstance(flags, list)
            or any(type(v) is not bool for v in flags) or flags != [r <= 1e-2 for r in ratios]):
        raise ResultContractError("Invalid signed-area cancellation metadata")
    for name in ("reciprocity_max_rel", "passivity_min_eig"):
        value = metadata.get(name)
        if type(value) not in (float, int) or not math.isfinite(value) or (name == "reciprocity_max_rel" and value < 0):
            raise ResultContractError(f"Invalid weighted matrix diagnostic {name}")


@dataclass(frozen=True)
class TransducerSweep:
    rows: tuple[TransducerFrequency, ...]
    cancelled: bool
    requested_frequency_count: int

    @property
    def frequencies_hz(self) -> np.ndarray:
        return np.asarray([row.frequency_hz for row in self.rows], dtype=float)


def map_transducer_sweep(events: Iterable[dict], request: TransducerRequest) -> TransducerSweep:
    """Only a cancellation terminal can authorize a short ordered prefix."""
    rows, terminal = [], None
    frequencies = request.wire["frequencies_hz"]
    try:
        for event in events:
            if terminal is not None or not isinstance(event, dict):
                raise ResultContractError("Invalid event or event after terminal")
            kind = event.get("type")
            if kind == "result":
                if len(rows) >= len(frequencies):
                    raise ResultContractError("Extra transducer frequency result")
                rows.append(parse_transducer_frequency(event.get("result"), request, frequencies[len(rows)]))
            elif kind in {"completed", "cancelled"}:
                count = event.get("solved_count")
                if type(count) is not int or count != len(rows) or (kind == "completed" and count != len(frequencies)):
                    raise ResultContractError("Transducer terminal count differs from requested/received results")
                terminal = kind
            elif kind == "failed":
                raise ResultContractError(f"BEAT transducer solve failed: {event.get('error', event)}")
            elif kind not in {"status", "progress"}:
                raise ResultContractError(f"Unknown transducer event {kind!r}")
        if terminal is None:
            raise ResultContractError("Transducer stream ended without a terminal event")
        return TransducerSweep(tuple(rows), terminal == "cancelled", len(frequencies))
    finally:
        close = getattr(events, "close", None)
        if close is not None:
            close()
