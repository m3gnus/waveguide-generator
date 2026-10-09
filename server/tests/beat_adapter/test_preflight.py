"""WG refuses unrepresented physics without changing BEAT's legacy contract."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import numpy as np

from server.solver.beat_adapter.capabilities import capability_report, probe_request
from server.solver.beat_adapter.preflight import UnsupportedPhysics, validate_exterior_physics
from server.solver.beat_adapter.request import SourceBasis
from server.solver.beat_adapter.observations import ObservationLayout
from server.solver.ground_plane import GroundPlane


COMPATIBILITY_CASES = {
    "normal_cpu": {},
    "normal_metal": {"engine_id": "beat-metal"},
    "axial_cpu": {"sources": [SourceBasis("source", 2, "axial", [0, 0, 1], "source")]},
    "axial_metal": {"engine_id": "beat-metal", "sources": [SourceBasis("source", 2, "axial", [0, 0, -1], "source")]},
    "ground_cpu": {"ground": GroundPlane("y", 3.)},
    "normal_cpu_float64": {"precision": "float64", "surface_traces": True},
}


def compatibility_layout():
    # Explicit axis-aligned points avoid platform libm differences in byte
    # snapshots. Observation-generator parity has its own existing fixtures.
    return ObservationLayout(
        np.array([-90., 0., 90.], dtype=np.float32), ("horizontal", "vertical"),
        {"horizontal": np.array([[-2., 0., 0.], [0., 0., 2.], [2., 0., 0.]]),
         "vertical": np.array([[0., -2., 0.], [0., 0., 2.], [0., 2., 0.]]),
         "sphere": np.array([[0., 0., 2.]] * 4 + [[2., 0., 0.], [0., 2., 0.], [-2., 0., 0.], [0., -2., 0.]]
                            + [[0., 0., -2.]] * 4)},
        np.array([0., 90., 180.]), np.array([0., 90., 180., 270.]),
    )


@pytest.mark.parametrize("name", COMPATIBILITY_CASES)
def test_complete_default_request_remains_frozen_and_preflight_does_not_mutate(name):
    fixture = json.loads(Path(__file__).with_name("fixtures").joinpath("wg_request_compatibility.json").read_text())
    expected = {case["name"]: case["wire_sha256"] for case in fixture["cases"]}
    request = probe_request(layout=compatibility_layout(), **COMPATIBILITY_CASES[name])
    before = copy.deepcopy(request.wire)
    validate_exterior_physics(request.wire)
    assert request.wire == before
    blob = json.dumps(request.wire, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(blob).hexdigest() == expected[name]


# Requests remain structurally plausible; these are physics WG cannot forward
# or interpret, even when BEAT's open parameter objects accept their syntax.
REFUSALS = [
    (("compiled_system", "boundaries", 0, "kind"), "impedance", "boundaries[0].kind"),
    (("compiled_system", "boundaries", 0, "parameters"), {"wall_impedance": {"model": "miki"}}, "boundaries[0].parameters"),
    (("compiled_system", "regions", 0, "loss_model"), {"bulk_loss_factor": 0.2}, "regions[0].loss_model"),
    (("compiled_system", "regions", 0, "kind"), "bounded_air", "regions[0].kind"),
    (("compiled_system", "components", 0, "parameters"), {"source_velocity_profile": "taper"}, "source_velocity_profile"),
    (("compiled_system", "components", 0, "parameters"), {"boundary_motion_weights": {"source": 0.5}}, "boundary_motion_weights"),
    (("compiled_system", "components", 0, "parameters"), {"motion_profile": "taper"}, "motion_profile"),
    (("compiled_system", "components", 0, "kind"), "electrodynamic_transducer", "components[0].kind"),
    (("compiled_system", "excitation_ports", 0, "kind"), "voltage", "excitation_ports[0].kind"),
    (("solver_options", "precisoin"), "float64", "precisoin"),
    (("solver_options", "boundary_admittance"), {"2": 1.0}, "boundary_admittance"),
    (("solver_options", "regular_quadrature_mode"), "wavelength", "regular_quadrature_mode"),
    (("solver_options", "phasor_convention"), "exp(+i omega t)", "phasor_convention"),
    (("solver_options", "quadrature_order"), True, "quadrature_order"),
    (("solver_options", "singular_order"), 4.5, "singular_order"),
    (("solver_options", "singular_order"), 8, "singular_order"),
    (("solver_options", "ground_plane_min_clearance_m"), 0.2, "ground_plane_min_clearance_m"),
]


def changed_request(path, value):
    request = probe_request()
    parent = request.wire
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return request


@pytest.mark.parametrize("path,value,field", REFUSALS)
def test_unsupported_physics_names_the_field_without_mutating(path, value, field):
    request = changed_request(path, value)
    before = copy.deepcopy(request.wire)
    with pytest.raises(UnsupportedPhysics) as error:
        validate_exterior_physics(request.wire)
    assert field in str(error.value)
    assert request.wire == before


@pytest.mark.parametrize("path,value,field", REFUSALS)
def test_production_refuses_before_importing_engine_or_acquiring_worker(monkeypatch, path, value, field):
    from server.solver import official_beat
    from server.solver.beat_runtime import warm_cache

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported request reached engine import, profiling, session, or worker acquisition")

    monkeypatch.setattr(official_beat, "importlib", SimpleNamespace(import_module=forbidden))
    monkeypatch.setattr(official_beat, "get_manager", forbidden)
    monkeypatch.setattr(official_beat, "SolveSession", forbidden)
    monkeypatch.setattr(official_beat, "start_profile", forbidden)
    before = warm_cache.generation()
    with pytest.raises(UnsupportedPhysics) as error:
        official_beat.solve_compiled(changed_request(path, value), channel_id="source")
    assert field in str(error.value)
    assert warm_cache.generation() == before


def test_engine_legacy_structural_acceptance_is_not_globally_tightened():
    contract = pytest.importorskip("beat_engine.beat_contract")
    request = changed_request(("compiled_system", "regions", 0, "loss_model"), {"bulk_loss_factor": 0.2})
    contract.validate_solve_request(request.wire)
    with pytest.raises(UnsupportedPhysics, match="loss_model"):
        validate_exterior_physics(request.wire)


def test_descriptive_metadata_remains_open_and_untouched():
    request = probe_request()
    request.wire["compiled_system"]["metadata"]["author_notes"] = {"model": "measurement", "labels": ["one", "two"]}
    before = copy.deepcopy(request.wire)
    validate_exterior_physics(request.wire)
    assert request.wire == before


@pytest.mark.parametrize("axis", [[0, 0, 0], [True, 0, 1], [0, float("nan"), 1], [0, 1]])
def test_mutated_axial_direction_is_refused(axis):
    request = probe_request(sources=[SourceBasis("source", 2, "axial", [0, 0, 1], "source")])
    request.wire["compiled_system"]["components"][0]["parameters"]["motion_axis"] = axis
    with pytest.raises(UnsupportedPhysics, match="motion_axis"):
        validate_exterior_physics(request.wire)


def test_builder_accepted_axis_at_symmetry_tolerance_is_not_renormalized():
    request = probe_request(symmetry="yz", sources=[SourceBasis(
        "source", 2, "axial",
        [9.114491429924474e-09, -0.3658269542776238, 0.8348114636165607], "source",
    )])
    axis = request.wire["compiled_system"]["components"][0]["parameters"]["motion_axis"]
    assert axis[0] == 1e-8
    before = copy.deepcopy(request.wire)
    validate_exterior_physics(request.wire)
    assert request.wire == before


def test_capability_report_checks_actual_wire_policy():
    report = capability_report()
    assert report["passed"], report["failures"]
    assert report["physics_policy_version"] == 1
    for name in ("exterior_impedance", "exterior_bulk_loss", "source_parameter", "source_profile", "solver_option", "transducer"):
        outcome = report["scenarios"][f"wire.{name}"]
        assert not outcome["supported"] and outcome["exception"] == "UnsupportedPhysics"


@pytest.mark.parametrize("precision,singular", [("float32", value) for value in range(1, 5)]
                         + [("float64", value) for value in range(1, 13)])
@pytest.mark.parametrize("regular", [1, 2, 4])
def test_existing_builder_quadrature_choices_remain_accepted(precision, singular, regular):
    request = probe_request(precision=precision, singular_order=singular, quadrature_order=regular)
    validate_exterior_physics(request.wire)
