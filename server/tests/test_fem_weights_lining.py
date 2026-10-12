"""Motion and lining contracts without a native acoustic solve."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from server.mesh.fem_regions import physical_name
from server.solver.beat_adapter import coupled_request
from server.solver.beat_adapter.coupled_transducers import channel_problem, parameters
from server.solver.context import SolverContext
from server.solver.coupled_admission import fem_group_options_problem
from server.solver.coupled_system import CoupledSystemError, ExteriorSurface, build_coupled_system
from server.tests.test_fem_electrodynamic import driver
from server.tests.test_fem_engine_route import FRAME, compiled, region_input

LINING = {"model": "miki", "thickness_mm": 30., "flow_resistivity_pa_s_per_m2": 5000.}


def request_for(region, exterior=None, **options):
    return coupled_request.build_coupled_request(
        exterior, [region], layout=compiled().layout, frame=FRAME, frequencies_hz=[100.], **options)


@pytest.mark.parametrize("level,weight", [(None, 1.), (0., 1.), (-6., .5011872336272722),
                                         (-20., .1), (-120., 1e-6)])
@pytest.mark.parametrize("coupled", [False, True])
def test_motion_and_lining_exact_engine_contract_and_revert(level, weight, coupled):
    from beat_engine.beat_contract import validate_solve_request
    region = region_input(interface=coupled)
    if level is not None:
        region.record["surface_groups"][0]["relative_level_db"] = level
    region.record["surface_groups"].append({"group_id": "walls", "kind": "rigid", "lining": deepcopy(LINING)})
    exterior = None
    if coupled:
        exterior = ExteriorSurface(region.domain.points_mm, region.domain.surfaces, {
            name: {"kind": "interface" if "kind=interface" in name else "rigid"}
            for name in region.domain.surfaces})
    before = deepcopy(region.record)
    wire = request_for(region, exterior).wire
    validate_solve_request(wire)
    system = wire["compiled_system"]
    component, = system["components"]
    # A unit weight is not sent at all, so default systems compile as before weights existed.
    assert component["parameters"] == ({} if weight == 1. else
                                       {"boundary_motion_weights": {"boundary:fem:air:cone": weight}})
    wall = next(b for b in system["boundaries"] if b["id"] == "boundary:fem:air:walls")
    assert wall["parameters"] == {"wall_impedance": {
        "model": "miki", "thickness_m": .03, "flow_resistivity_pa_s_per_m2": 5000.}}
    assert all(b["parameters"] == {} for b in system["boundaries"] if b is not wall)
    assert system["excitation_ports"][0]["kind"] == "normal_velocity"
    assert region.record == before
    # Removing the settings reproduces the default compiled system.
    region.record["surface_groups"][0].pop("relative_level_db", None)
    region.record["surface_groups"].pop()
    reverted = request_for(region, exterior).wire["compiled_system"]
    assert reverted == request_for(region_input(interface=coupled), exterior).wire["compiled_system"]


@pytest.mark.parametrize("kind", ["moving", "interface", "plane-wave-termination"])
def test_misplaced_lining_refuses_retained_records_and_compiler(kind):
    region = region_input()
    region.record["surface_groups"][0].update(kind=kind, lining=LINING)
    problem = fem_group_options_problem([region.record])
    assert problem[0] == "fem_group_options_invalid"
    assert "air" in problem[1] and "cone" in problem[1] and "only on a rigid group" in problem[1]
    with pytest.raises(CoupledSystemError, match="only on a rigid group"):
        build_coupled_system(None, [region])


@pytest.mark.parametrize("kind", ["rigid", "moving", "interface"])
def test_exterior_lining_refuses_even_on_rigid_groups(kind):
    region = region_input()
    kinds = {name: {"kind": "rigid"} for name in region.domain.surfaces}
    next(iter(kinds.values())).update(kind=kind, lining=LINING, drive_channel_id="paint")
    exterior = ExteriorSurface(region.domain.points_mm, region.domain.surfaces, kinds)
    with pytest.raises(CoupledSystemError, match="only on rigid FEM boundaries"):
        build_coupled_system(exterior, [region])


@pytest.mark.parametrize("kind", ["rigid", "interface", "plane-wave-termination"])
def test_misplaced_relative_level_refuses(kind):
    region = region_input()
    region.record["surface_groups"][0].update(kind=kind, relative_level_db=0.)
    with pytest.raises(CoupledSystemError, match="only on a moving group"):
        build_coupled_system(None, [region])


@pytest.mark.parametrize("level", [1., -121., float("nan"), float("inf"), True])
def test_invalid_relative_levels_refuse(level):
    region = region_input()
    region.record["surface_groups"][0]["relative_level_db"] = level
    assert fem_group_options_problem([region.record])[0] == "fem_group_options_invalid"


@pytest.mark.parametrize("weight", [0., -1., float("nan"), float("inf"), True])
def test_coupled_patch_weights_must_be_positive(weight):
    channel = SimpleNamespace(id="paint", source_ids=["patch"], patch_weights={"patch": weight})
    assert "finite and positive" in channel_problem(channel)[1]


@pytest.mark.parametrize("interfaces", [False, True])
def test_imported_patch_weights_reach_both_exterior_build_paths(monkeypatch, interfaces):
    from beat_engine.beat_contract import validate_solve_request
    region = region_input(interface=interfaces)
    wall_name = physical_name("air", "walls", "rigid")
    wall_faces = region.domain.surfaces[wall_name]
    groups = {"wg-import-v1|source_id=one": wall_faces[:1],
              "wg-import-v1|source_id=two": wall_faces[1:]}
    kinds = {name: {"kind": "moving"} for name in groups}
    for name, block in region.domain.surfaces.items():
        if name != wall_name:
            groups[name] = block
            kinds[name] = {"kind": "interface" if "kind=interface" in name else "rigid"}
    record = {"fem_regions": [region.record], "source_tags": {"one": 101, "two": 102}}
    monkeypatch.setattr(coupled_request, "read_verified_fem_region_mesh", lambda *a: "fem")
    monkeypatch.setattr(coupled_request, "read_fem_region_domain", lambda *a: region.domain)
    monkeypatch.setattr(coupled_request, "imported_anchor_frame", lambda *a: FRAME)
    if interfaces:
        record["coupled_exterior"] = {"physical_names": kinds}
        monkeypatch.setattr(coupled_request, "read_verified_coupled_exterior_mesh", lambda *a: "bem")
        monkeypatch.setattr(coupled_request, "read_coupled_exterior", lambda *a: (region.domain.points_mm, groups))
    else:
        from server.mesh import artifact
        from server.solver.beat_adapter import mesh
        monkeypatch.setattr(artifact, "read_verified_import_mesh", lambda *a: "bem")
        faces = np.concatenate(list(groups.values()))
        tags = np.concatenate([np.full(len(block), tag) for block, tag in zip(groups.values(), [101, 102, 201])])
        monkeypatch.setattr(mesh, "read_surface", lambda *a: SimpleNamespace(
            points_m=region.domain.points_mm / 1000, faces=faces, tags=tags))
    channels = [SimpleNamespace(id="paint", source_ids=["one", "two"], motion="normal", driver=None,
                                patch_weights={"one": .25, "two": 2.5}, exterior_transducer=None),
                SimpleNamespace(id="fem", source_ids=[], motion="normal", patch_weights=None, exterior_transducer=None)]
    geometry = SimpleNamespace(drive_channels=channels)
    request = coupled_request.build_imported_coupled_request(
        record, geometry, SolverContext(design=None, frequency_range=(100., 200.), num_frequencies=2), frequencies_hz=[100.])
    validate_solve_request(request.wire)
    system = request.wire["compiled_system"]
    paint = next(c for c in system["components"] if c["name"] == "paint")
    weights = paint["parameters"]["boundary_motion_weights"]
    assert sorted(weights.values()) == [.25, 2.5]
    assert set(weights) == set(paint["boundary_ids"])
    assert request.drive_normalization == {"fem": "unit_acceleration", "paint": "unit_acceleration"}
    # Port velocity and radiation remain engine outputs; source weights are not applied a second time.
    assert sum(o["quantity"] == "interface_average_normal_velocity" for o in request.wire["outputs"]) == int(interfaces)


@pytest.mark.parametrize("level", [-20., 0.])
def test_weighted_geometry_keeps_declared_chamber_sd_and_raw_load(level):
    region = region_input()
    region.record["surface_groups"][0]["relative_level_db"] = level
    spec = driver(sd_m2=5e-5, lumped_sealed_rear_chamber={
        "enabled": True, "volume_m3": .002})
    with pytest.warns(UserWarning, match="Sd cross-check") if level else nullcontext():
        request = request_for(region, transducers={"fem": spec})
    system = request.wire["compiled_system"]
    params = system["components"][0]["parameters"]
    weight = 10 ** (level / 20)
    assert params["boundary_motion_weights"] == {"boundary:fem:air:cone": weight}
    assert system["metadata"]["driver_sd"]["fem"]["geometric_sd_m2"] == pytest.approx(5e-5 * weight)
    assert system["metadata"]["projected_areas_m2"]["fem"] == pytest.approx(5e-5)
    assert params["lumped_sealed_rear_chamber"]["projected_area_m2"] == pytest.approx(5e-5)
    from server.solver.beat_adapter.results import coupled_envelope, map_coupled_sweep
    from server.solver import official_beat
    from server.tests.test_fem_engine_route import events
    from server.jobs.models import DriveChannel
    from server.tests.test_imported_jobs import _request
    context = SolverContext(design=None, frequency_range=(100., 200.), num_frequencies=2)
    job = _request("wgi_" + "0" * 26)
    job.geometry.drive_channels = [DriveChannel(id="fem", source_ids=[], motion="axial", exterior_transducer=spec)]
    sweep = map_coupled_sweep(events(request), request)
    response = coupled_envelope(sweep, request, context=context, geometry=job.geometry, record={},
                                config=official_beat.response_config(request, context), started=0.)
    channel = response["channels"]["fem"]
    assert channel["metadata"]["sd_m2"] == pytest.approx(5e-5)
    load = channel["metadata"]["beat_transducer"]["generalized_load"]
    assert load["units"] == "N*s/m" and load["component_ids"] == ["component:fem"]
    assert np.isfinite(sweep.generalized_load).all()
    from server.solver.result_mapping import json_safe_native_value
    assert load["values"] == json_safe_native_value(sweep.generalized_load[:, 0])
    if level:
        assert "acoustic_impedance" not in channel
        assert "acoustic_impedance" not in channel["metadata"]
        assert channel["metadata"]["acoustic_impedance_note"] == "acoustic impedance is not normalised for weighted motion"
    else:
        assert channel["metadata"]["acoustic_impedance"]["projected_area_m2"] == pytest.approx(5e-5)
    assert request.drive_normalization == {"fem": "voltage_rms"}


@pytest.mark.parametrize("declared", [None, 5e-5])
@pytest.mark.parametrize("levels", [(-20., 0.), (0., -20.)])
def test_any_weighted_boundary_omits_normalized_load_and_revert_restores_it(declared, levels):
    from server.jobs.models import DriveChannel
    from server.solver.beat_adapter.results import map_coupled_sweep
    from server.solver.result_mapping import json_safe_native_value, map_coupled_transducer_response
    from server.tests.test_fem_electrodynamic import _shared_regions
    from server.tests.test_fem_engine_route import events
    region, = _shared_regions(two_regions=False)
    spec = driver(**({"sd_m2": declared} if declared is not None else {}))
    for group, level in zip(region.record["surface_groups"], levels, strict=True):
        group["relative_level_db"] = level
    request = request_for(region, transducers={"fem": spec})
    metadata = request.wire["compiled_system"]["metadata"]["driver_sd"]["fem"]
    assert metadata["geometric_sd_m2"] == pytest.approx(2.75e-5)
    assert metadata["sd_m2"] == pytest.approx(declared or 2.75e-5)
    channel = DriveChannel(id="fem", source_ids=[], motion="axial", exterior_transducer=spec)
    for weighted in (True, False):
        sweep = map_coupled_sweep(events(request), request)
        response = {"frequencies": sweep.channels["fem"].frequencies_hz.tolist(), "metadata": {}}
        map_coupled_transducer_response(response, sweep, request, "fem", channel)
        assert response["metadata"]["beat_transducer"]["generalized_load"]["values"] == json_safe_native_value(
            sweep.generalized_load[:, 0])
        assert ("acoustic_impedance" in response) == (not weighted)
        if weighted:
            assert response["metadata"]["acoustic_impedance_note"] == "acoustic impedance is not normalised for weighted motion"
            for group in region.record["surface_groups"]:
                group.pop("relative_level_db")
            request = request_for(region, transducers={"fem": spec})
        else:
            assert "acoustic_impedance_note" not in response["metadata"]
            assert response["metadata"]["acoustic_impedance"]["projected_area_m2"] == pytest.approx(5e-5)


def test_weighted_geometry_preserves_declared_sd_and_opposite_sign_chamber_refusal():
    region = region_input()
    faces = next(block for name, block in region.domain.surfaces.items() if "moving" in name)
    front = region.domain.points_mm[faces] / 1000
    rear = front[:, [0, 2, 1]]
    meshes = {"front": [(faces, front)], "rear": [(faces, rear)]}
    weights = {"front": [np.full(len(faces), .01)], "rear": [np.full(len(faces), .5)]}
    metadata = {}
    with pytest.warns(UserWarning, match="geometry is ambiguous"):
        _, area = parameters(driver(sd_m2=5e-5), meshes, face_weights=weights, sd_metadata=metadata)
    assert area == pytest.approx(5e-5)
    assert metadata["geometric_sd_m2"] == pytest.approx(5e-5 * (.01 + .5) / 2)
    assert metadata["geometry_ambiguous"] and metadata["sd_source"] == "declared"
    with pytest.raises(ValueError, match="lumped_chamber_double_rear_load"):
        parameters(driver(sd_m2=5e-5, lumped_sealed_rear_chamber={"enabled": True, "volume_m3": .002}),
                   meshes, face_weights=weights)


@pytest.mark.parametrize("key,value", [("model", "other"), ("thickness_mm", 0.),
                                     ("flow_resistivity_pa_s_per_m2", -1.),
                                     ("thickness_mm", True), ("thickness_mm", float("inf"))])
def test_invalid_lining_values_refuse_retained_records(key, value):
    region = region_input()
    lining = {**LINING, key: value}
    region.record["surface_groups"].append({"group_id": "walls", "kind": "rigid", "lining": lining})
    with pytest.raises(CoupledSystemError, match="lining"):
        build_coupled_system(None, [region])


@pytest.mark.parametrize("options", [{"lining": LINING}, {"relative_level_db": -6.}])
def test_boundary_physics_cannot_disappear_on_a_missing_mesh_group(options):
    region = region_input()
    kind = "rigid" if "lining" in options else "moving"
    region.record["surface_groups"] = [{"group_id": "missing", "kind": kind,
                                        "drive_channel_id": "fem", **options}]
    with pytest.raises(CoupledSystemError, match="no matching mesh group"):
        build_coupled_system(None, [region])


def test_quiet_motion_and_lining_pass_gpu_float32_contract():
    from beat_engine.beat_contract import validate_solve_request
    region = region_input(interface=True)
    region.record["surface_groups"][0]["relative_level_db"] = -120.
    region.record["surface_groups"].append({"group_id": "walls", "kind": "rigid", "lining": LINING})
    exterior = ExteriorSurface(region.domain.points_mm, region.domain.surfaces, {
        name: {"kind": "interface" if "kind=interface" in name else "rigid"}
        for name in region.domain.surfaces})
    request = request_for(region, exterior, backend="metal", precision="float32")
    validate_solve_request(request.wire)
    assert request.wire["compiled_system"]["components"][0]["parameters"]["boundary_motion_weights"] == {
        "boundary:fem:air:cone": 1e-6}
