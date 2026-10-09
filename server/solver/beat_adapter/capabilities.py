"""Request-builder evidence for official BEAT routes, without runtime probes."""

from __future__ import annotations

from collections.abc import Callable
import inspect
from typing import Any

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.jobs.models import DriveChannel
from server.solver.beat_runtime.paths import PROVIDER_ID
from server.solver.context import SolverContext
from server.solver.ground_plane import GroundPlane

from . import request
from .observations import build_observations
from .preflight import POLICY_VERSION, validate_exterior_physics

# HBB conformance/warm-up tetrahedron; all four triangles are a normal source.
PROBE_MESH = """$MeshFormat
2.2 0 8
$EndMeshFormat
$PhysicalNames
1
2 2 "warmup"
$EndPhysicalNames
$Nodes
4
1 0 0 0
2 0.08 0 0
3 0 0.08 0
4 0 0 0.08
$EndNodes
$Elements
4
1 2 2 2 2 1 3 2
2 2 2 2 2 1 2 4
3 2 2 2 2 2 3 4
4 2 2 2 2 3 1 4
$EndElements
"""
FRAME = {"origin": [0., 0., 0.], "axis": [0., 0., 1.],
         "u": [1., 0., 0.], "v": [0., 1., 0.]}


def probe_request(**options: Any) -> request.CompiledRequest:
    arguments = {"sources": [request.SourceBasis("source", 2, port_id="source")],
                 "channel_ports": {"source": ["source"]}, "frame": FRAME,
                 "layout": build_observations(sphere_grid=(3, 4)),
                 "frequencies_hz": [500., 300.]}
    arguments.update(options)
    return request.build_request(PROBE_MESH, **arguments)


def _outcome(call: Callable[[], request.CompiledRequest]) -> dict[str, Any]:
    try:
        built = call()
        validate_exterior_physics(built.wire)
    except (ValueError, TypeError, NotImplementedError) as exc:
        return {"supported": False, "reason": str(exc), "exception": type(exc).__name__}
    return {"supported": True, "solver_options": built.wire["solver_options"],
            "contract_version": built.wire["compiled_system"]["contract_version"]}


# Known engine features whose representation this adapter must explicitly probe.
DECLARED_FEATURES = frozenset({
    "boundary_admittance", "coupled_fem_bem_lem", "near_correction",
    "regular_quadrature_mode", "drive_amplitude", "frequency_dependent_drive",
    "post_solve_field_replay", "acoustic_power",
})
EXPECTED_REFUSALS = frozenset({
    "route.beat-metal.float64", "route.official-beat-metal.float64", "route.beat.None",
    "symmetry.xy", "symmetry.x", "symmetry.y",
    *(f"ground.{axis}+reduction" for axis in "xyz"),
    *(f"frequencies.{name}" for name in ("empty", "duplicate", "nonpositive", "nonfinite", "boolean")),
    *(f"quadrature.{backend}.{order}" for backend in ("cpu", "metal", "cuda", "rocm") for order in (3, 6, 8)),
    "source.axial.[0, 0, 0]", "source.normal.[0, 0, 1]", "source.radial.None",
    *(f"feature.{name}" for name in DECLARED_FEATURES),
    *(f"singular.float32.{order}" for order in (0, 5, 12, 13)),
    "singular.float64.0", "singular.float64.13", "imported.missing_tags",
    "parametric.infinite_baffle", "imported.infinite_baffle",
    "wire.exterior_impedance", "wire.exterior_bulk_loss", "wire.source_parameter",
    "wire.source_profile", "wire.solver_option", "wire.transducer",
})


def _unsupported_wire(feature: str) -> request.CompiledRequest:
    built = probe_request()
    system = built.wire["compiled_system"]
    if feature == "exterior_impedance":
        system["boundaries"][0]["kind"] = "impedance"
    elif feature == "exterior_bulk_loss":
        system["regions"][0]["loss_model"] = {"bulk_loss_factor": 0.2}
    elif feature == "source_parameter":
        system["components"][0]["parameters"]["source_velocity_profile"] = "taper"
    elif feature == "source_profile":
        system["contract_version"] = 2
        system["components"][0]["parameters"]["motion_profile"] = "taper"
    elif feature == "solver_option":
        built.wire["solver_options"]["precisoin"] = "float64"
    elif feature == "transducer":
        system["contract_version"] = 3
        system["components"][0]["kind"] = "electrodynamic_transducer"
    else:
        raise ValueError(f"Unknown wire probe: {feature}")
    return built


def probe_feature(name: str) -> dict[str, Any]:
    """Unknown spellings are probe errors, never successful refusals."""
    if name not in DECLARED_FEATURES:
        raise ValueError(f"Unknown declared feature: {name}")
    parameters = inspect.signature(request.build_request).parameters
    if name not in parameters and not any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return {"supported": False, "reason": f"Adapter has no representation for {name}",
                "exception": "NotImplementedError"}
    return _outcome(lambda: probe_request(**{name: True}))


def capability_report() -> dict[str, Any]:
    """Exercise declared scenarios; refusal reasons come from the real builders.

    Acceptance means representable by this additive adapter, not registered
    production routing or availability on this host. Scenarios are intentionally
    explicit; the observed refusal set must equal the declared acceptance set.
    """
    probes: dict[str, Callable[[], request.CompiledRequest]] = {}
    for engine in ("beat-cpu", "beat-metal", "beat-cuda", "beat-rocm",
                   "official-beat-cpu", "official-beat-metal", "official-beat-cuda", "official-beat-rocm"):
        for precision in ("float32", "float64"):
            probes[f"route.{engine}.{precision}"] = (
                lambda engine=engine, precision=precision:
                probe_request(engine_id=engine, precision=precision))
    for backend in (None, "cpu", "metal", "cuda", "rocm"):
        probes[f"route.beat.{backend}"] = lambda backend=backend: probe_request(engine_id="beat", backend=backend)
    for symmetry in ("full", "off", "yz", "xz", "yz+xz", "xy", "x", "y"):
        probes[f"symmetry.{symmetry}"] = lambda symmetry=symmetry: probe_request(symmetry=symmetry)
    for axis in ("x", "y", "z"):
        probes[f"ground.{axis}"] = lambda axis=axis: probe_request(ground=GroundPlane(axis, 3.))
        probes[f"ground.{axis}+reduction"] = (
            lambda axis=axis: probe_request(ground=GroundPlane(axis, 3.), symmetry="yz"))
    for label, frequencies in {"unordered": [500., 300., 400.], "empty": [],
                               "duplicate": [300., 300.], "nonpositive": [0.],
                               "nonfinite": [float("nan")], "boolean": [True],
                               "float32_alias": [500., 500.000001]}.items():
        probes[f"frequencies.{label}"] = lambda frequencies=frequencies: probe_request(frequencies_hz=frequencies)
    for backend in ("cpu", "metal", "cuda", "rocm"):
        for order in (1, 2, 3, 4, 6, 8):
            probes[f"quadrature.{backend}.{order}"] = (
                lambda backend=backend, order=order:
                probe_request(engine_id=f"beat-{backend}", quadrature_order=order))
    for motion, axis in (("normal", None), ("axial", [0, 0, -1]), ("axial", [1, 2, 3]),
                         ("axial", [0, 0, 0]), ("normal", [0, 0, 1]), ("radial", None)):
        probes[f"source.{motion}.{axis}"] = lambda motion=motion, axis=axis: probe_request(
            sources=[request.SourceBasis("source", 2, motion, axis, "source")])
    for planes in (("horizontal", "vertical", "diagonal"), ("diagonal",)):
        probes[f"observation.{'+'.join(planes)}"] = lambda planes=planes: probe_request(
            layout=build_observations(planes=planes, inclination_deg=27., sphere_grid=(37, 72)))
    probes["result.surface_traces"] = lambda: probe_request(surface_traces=True)
    for precision in ("float32", "float64"):
        for singular in (0, 4, 5, 12, 13):
            probes[f"singular.{precision}.{singular}"] = (
                lambda precision=precision, singular=singular:
                probe_request(precision=precision, singular_order=singular))
    context = SolverContext(None, (300., 500.), 2)
    probes["parametric.exterior"] = lambda: request.build_parametric_request(PROBE_MESH, context)
    record = {"source_tags": {"source": 2}, "anchor": {"throat_frame": FRAME},
              "symmetry": {"cut_planes": []}}
    channels = [DriveChannel(id="source", source_ids=["source"])]
    probes["imported.exterior"] = lambda: request.build_imported_request(PROBE_MESH, context, record, channels)
    probes["imported.missing_tags"] = lambda: request.build_imported_request(PROBE_MESH, context, {}, channels)
    baffle = SolverContext(None, (300., 500.), 2, sim_type=1)
    probes["parametric.infinite_baffle"] = lambda: request.build_parametric_request(PROBE_MESH, baffle)
    probes["imported.infinite_baffle"] = lambda: request.build_imported_request(PROBE_MESH, baffle, {}, [])
    for feature in ("exterior_impedance", "exterior_bulk_loss", "source_parameter",
                    "source_profile", "solver_option", "transducer"):
        probes[f"wire.{feature}"] = lambda feature=feature: _unsupported_wire(feature)
    scenarios = {name: _outcome(call) for name, call in probes.items()}
    scenarios.update({f"feature.{name}": probe_feature(name) for name in sorted(DECLARED_FEATURES)})
    refused = {name for name, outcome in scenarios.items() if not outcome["supported"]}
    failures = []
    if refused != EXPECTED_REFUSALS:
        failures.append(f"Refusal set differs: missing {sorted(EXPECTED_REFUSALS - refused)}, "
                        f"extra {sorted(refused - EXPECTED_REFUSALS)}")
    return {"passed": not failures, "failures": failures,
            "expected_refusals": sorted(EXPECTED_REFUSALS),
            "schema_version": 1, "provider": PROVIDER_ID,
            "physics_policy_version": POLICY_VERSION,
            "engine": "JWSound/BEAT_Engine", "distribution": "beat-engine",
            "time_convention": SOLVER_TIME_CONVENTION,
            "scope": "request construction; not runtime readiness or production adoption",
            "scenarios": scenarios,
            "supported_count": sum(item["supported"] for item in scenarios.values()),
            "refused_count": sum(not item["supported"] for item in scenarios.values())}
