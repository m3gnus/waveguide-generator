"""WG parametric/imported requests against official schemas, without a worker."""

from __future__ import annotations

import base64
import builtins
import copy

import numpy as np
import pytest

from server.jobs.models import DriveChannel
from server.solver.beat_adapter import request as adapter
from server.solver.beat_adapter.mesh import read_surface
from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.results import acceleration_scale, parse_compiled_frequency
from server.solver.context import SolverContext
from server.solver.frequency_sweep import live_execution_frequencies
from server.solver.ground_plane import GroundPlane


FRAME = {"origin": [0., 0., 0.], "axis": [0., 0., 1.],
         "u": [1., 0., 0.], "v": [0., 1., 0.]}


@pytest.fixture
def msh(make_mesh):
    return make_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                     [[0, 1, 2], [0, 3, 1]], [2, 1], node_ids=[40, 9, 71, 3])


@pytest.fixture
def build(msh, durable_request):
    def make(**kwargs):
        options = {"sources": [adapter.SourceBasis("source", 2, port_id="excitation:source")],
                   "channel_ports": {"stored-channel": ["excitation:source"]},
                   "layout": build_observations(sphere_grid=(3, 4)), "frame": FRAME,
                   "frequencies_hz": [1000.25, 100.125, 500.5], "precision": "float64"}
        options.update(kwargs)
        return durable_request(adapter.build_request(msh, **options))
    return make


def test_packed_mesh_preserves_original_identity_scale_order_and_winding(build):
    built = build(mesh_scale_to_m=.001)
    assert built.mesh.node_ids == (40, 9, 71, 3)
    assert built.mesh.element_ids == (51, 52)
    np.testing.assert_array_equal(built.mesh.faces, [[0, 1, 2], [0, 3, 1]])
    np.testing.assert_array_equal(built.mesh.tags, [2, 1])
    np.testing.assert_allclose(built.mesh.points_m[1], [.001, 0, 0])
    packed = built.wire["compiled_system"]["meshes"][0]["mesh_data"]
    points = np.frombuffer(base64.b64decode(packed["points"]["data"]), dtype="<f8").reshape(-1, 3)
    np.testing.assert_array_equal(points, built.mesh.points_m)
    np.testing.assert_array_equal(built.wire["frequencies_hz"], [1000.25, 100.125, 500.5])
    assert built.wire["compiled_system"]["contract_version"] == 1
    assert built.wire["compiled_system"]["metadata"]["source_tags"] == {"source": 2}
    assert built.channel_loading["stored-channel"].area_m2 == pytest.approx(.5e-6)


@pytest.mark.parametrize("engine,backend,precision", [
    ("beat-cpu", None, "float64"), ("beat-metal", None, "float32"),
    ("beat", "cpu", "float32"), ("beat", "metal", "float32")])
def test_stored_engine_ids_survive_and_cpu_wavelength_policy_is_explicit(build, engine, backend, precision):
    built = build(engine_id=engine, backend=backend, precision=precision)
    options = built.wire["solver_options"]
    assert built.engine_id == engine
    assert options["regular_quadrature_mode"] == ("wavelength" if options["bem_backend"] == "cpu" else "fixed")
    assert options["quadrature_order"] == options["singular_order"] == 4
    assert options["wavelength_mesh_stat"] == "p90"
    assert options["wavelength_kh_q1_max"] == 0
    assert options["wavelength_kh_q2_max"] == 2
    # An independent scalar transcription of HBB's selection around the cutoff.
    h = float(np.sqrt(np.quantile(built.mesh.areas_m2.astype(float), .9)))
    f_cut = 343 / (np.pi * h)
    def order(f):
        kh = 2 * np.pi * f / 343 * h
        return 1 if kh <= options["wavelength_kh_q1_max"] else 2 if kh <= options["wavelength_kh_q2_max"] else options["quadrature_order"]
    assert order(f_cut * (1 - 1e-10)) == 2
    assert order(f_cut * (1 + 1e-10)) == 4


@pytest.mark.parametrize("symmetry,mode", [("full", "off"), ("yz", "x"), ("yz+xz", "xy"), ("xz", "x")])
def test_symmetry_and_signed_axial_contract(build, symmetry, mode):
    source = adapter.SourceBasis("rear", 2, "axial", [0, 0, -8])
    built = build(sources=[source], channel_ports={"rear-ch": ["excitation:axial:rear"]}, symmetry=symmetry)
    system = built.wire["compiled_system"]
    assert system["contract_version"] == 2
    assert system["components"][0]["parameters"] == {"motion_profile": "rigid_translation", "motion_axis": [0, 0, -1]}
    assert system["excitation_ports"][0]["kind"] == "normal_velocity"
    assert built.wire["solver_options"]["symmetry"] == mode
    assert built.channel_loading["rear-ch"].area_m2 == pytest.approx(.5)


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_ground_rotates_mesh_motion_and_observations_together(build, axis):
    source = adapter.SourceBasis("source", 2, "axial", [1, 2, 3])
    built = build(sources=[source], channel_ports={"ch": ["excitation:axial:source"]},
                  ground=GroundPlane(axis, 3))
    original = build(sources=[source], channel_ports={"ch": ["excitation:axial:source"]})
    assert built.wire["solver_options"]["symmetry"] == "ground"
    np.testing.assert_allclose(built.mesh.points_m[:, 1], 3 + original.mesh.points_m[:, "xyz".index(axis)])
    assert built.channel_loading["ch"].area_m2 == original.channel_loading["ch"].area_m2
    # Rigid transforms preserve point/source distances and winding (det R=+1).
    np.testing.assert_allclose(np.linalg.norm(built.layout.points_m["horizontal"] - built.mesh.points_m[0], axis=1),
                               np.linalg.norm(original.layout.points_m["horizontal"] - original.mesh.points_m[0], axis=1))
    wire_axis = built.wire["compiled_system"]["components"][0]["parameters"]["motion_axis"]
    assert wire_axis[1] == pytest.approx(np.array([1, 2, 3])["xyz".index(axis)] / np.sqrt(14))


def test_ground_and_reduction_refuse_instead_of_dropping_an_image(build):
    with pytest.raises(ValueError, match="combine ground"):
        build(symmetry="yz", ground=GroundPlane("y", 3))
    with pytest.raises(ValueError, match="no coplanar"):
        build(ground=GroundPlane("z", 0))
    with pytest.raises(ValueError, match="above"):
        build(ground=GroundPlane("y", -2))


@pytest.mark.parametrize("change", [
    {"frequencies_hz": []}, {"frequencies_hz": [1, 1]}, {"frequencies_hz": [0]},
    {"frequencies_hz": [np.inf]},
    {"engine_id": "beat"}, {"engine_id": "beat-metal", "backend": "cpu"},
    {"engine_id": "beat-metal", "precision": "float64"}, {"engine_id": "unknown"},
    {"quadrature_order": 6}, {"quadrature_order": True}, {"singular_order": 13},
    {"singular_order": 8, "precision": "float32"}, {"mesh_scale_to_m": 0},
    {"sound_speed_m_per_s": 0}, {"density_kg_per_m3": np.nan},
    {"symmetry": "invalid"}, {"channel_ports": {"ch": ["unknown"]}},
    {"channel_ports": {}}, {"sources": []},
])
def test_invalid_requests_are_refused_before_submission(build, change):
    with pytest.raises(ValueError):
        build(**change)


def test_axial_axis_outside_physical_symmetry_is_never_projected(build):
    source = adapter.SourceBasis("tilted", 2, "axial", [1, 0, 1])
    with pytest.raises(ValueError, match="symmetry planes"):
        build(sources=[source], channel_ports={"ch": ["excitation:axial:tilted"]}, symmetry="yz")
    built = build(sources=[source], channel_ports={"ch": ["excitation:axial:tilted"]})
    np.testing.assert_allclose(built.wire["compiled_system"]["components"][0]["parameters"]["motion_axis"],
                               [1 / np.sqrt(2), 0, 1 / np.sqrt(2)])


def test_parametric_context_and_adaptive_batch_preserve_frequency_plan(msh, durable_request, tmp_path):
    context = SolverContext(None, (100, 1000), 5, source_motion="axial", quadrants=1,
                            adaptive_frequency_sampling=True)
    built = durable_request(adapter.build_parametric_request(msh, context, precision="float64"))
    assert built.wire["frequencies_hz"] == live_execution_frequencies(context).tolist()
    assert built.wire["excitation_port_ids"] == ["excitation:source"]
    batch = durable_request(adapter.build_parametric_request(
        msh, context, frequencies_hz=[750.123456789, 125.5], cancel_path=tmp_path / "cancel"))
    assert batch.wire["frequencies_hz"] == [750.123456789, 125.5]
    assert batch.wire["cancel_path"] == str(tmp_path / "cancel")
    assert not (tmp_path / "cancel").exists()
    assert context.adaptive_frequency_sampling  # Planner state belongs to the caller.
    with pytest.raises(ValueError, match="baffle"):
        adapter.build_parametric_request(msh, SolverContext(None, (100, 1000), 3, sim_type=1))


@pytest.fixture
def imported(make_mesh):
    msh = make_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1]],
                    [[0, 1, 2], [3, 5, 4], [0, 3, 1]], [101, 105, 1])
    frame = {"origin_m": [.2, .3, .4], "axis": [1, 0, 0], "u": [0, 1, 0], "v": [0, 0, 1]}
    record = {"source_tags": {"front:id": 101, "rear/id": 105},
              "anchor": {"throat_frame": frame}, "symmetry": {"cut_planes": []}}
    return msh, record


def test_imported_original_global_frame_tags_rear_axes_and_independent_channels(imported, durable_request):
    msh, record = imported
    channels = [DriveChannel(id="stored:front", source_ids=["front:id"], motion="axial"),
                DriveChannel(id="rear", source_ids=["rear/id"], motion="axial"),
                DriveChannel(id="together", source_ids=["front:id", "rear/id"], motion="axial")]
    before = copy.deepcopy(record)
    built = durable_request(adapter.build_imported_request(msh, SolverContext(None, (100, 1000), 3),
                                                             record, channels, precision="float64"))
    assert record == before
    assert len(built.bases) == 2
    np.testing.assert_array_equal(built.mesh.tags, [101, 105, 1])
    np.testing.assert_array_equal(built.mesh.points_m, read_surface(msh).points_m)
    params = [c["parameters"] for c in built.wire["compiled_system"]["components"]]
    assert params[0]["motion_axis"] == [0, 0, 1]
    assert params[1]["motion_axis"] == [0, 0, -1]
    assert tuple(built.channel_ports) == ("stored:front", "rear", "together")
    assert built.channel_ports["together"] == ("excitation:axial:front%3Aid", "excitation:axial:rear%2Fid")
    np.testing.assert_allclose(built.layout.points_m["horizontal"][0], [.2 + 2, .3, .4], atol=1e-14)


def test_imported_shared_source_can_have_normal_and_axial_bases(imported, durable_request, compiled_result):
    msh, record = imported
    channels = [DriveChannel(id="normal", source_ids=["front:id"]),
                DriveChannel(id="axial", source_ids=["front:id"], motion="axial")]
    built = durable_request(adapter.build_imported_request(msh, SolverContext(None, (100, 1000), 3),
                                                             record, channels, precision="float64"))
    assert len(built.bases) == 2
    rows = parse_compiled_frequency(compiled_result(built), built, frequency_hz=100)
    assert rows["normal"].pressure_complex[0, 0] == pytest.approx((1 + 2j) * acceleration_scale(100))
    assert rows["axial"].pressure_complex[0, 0] == pytest.approx((2 + 2j) * acceleration_scale(100))


def test_old_imported_symmetry_record_and_unknown_source_refusal(imported, durable_request):
    msh, record = imported
    context = SolverContext(None, (100, 1000), 3, quadrants=14)
    record["symmetry"] = {"cut_planes": ["x0"]}  # Older persisted records have no domain_planes.
    channel = DriveChannel(id="normal", source_ids=["front:id"])
    built = durable_request(adapter.build_imported_request(msh, context, record, [channel]))
    assert built.wire["solver_options"]["symmetry"] == "x"
    with pytest.raises(ValueError, match="disagree"):
        adapter.build_imported_request(msh, SolverContext(None, (100, 1000), 3), record, [channel])
    with pytest.raises(ValueError, match="source identity"):
        adapter.build_imported_request(msh, context, record, [DriveChannel(id="ch", source_ids=["lost"])])


def test_adapter_builds_without_importing_optional_engine_or_hbb(build, monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith(("beat_engine", "hornlab_beat_bem")):
            raise AssertionError(f"Adapter tried to import {name}")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    build()


def test_ground_clearance_is_passed_and_checked_before_submission(build):
    built = build(ground=GroundPlane("y", .5), ground_plane_min_clearance_m=.4)
    assert built.wire["solver_options"]["ground_plane_min_clearance_m"] == .4
    with pytest.raises(ValueError, match="ground clearance"):
        build(ground=GroundPlane("y", .5), ground_plane_min_clearance_m=.6)
    with pytest.raises(ValueError, match="non-negative"):
        build(ground_plane_min_clearance_m=-1)


@pytest.mark.parametrize("direction", [[0, 0, 0], [0, 0, np.nan], [False, 0, 1], [1, 2]])
def test_invalid_motion_axes_fail_explicitly(build, direction):
    with pytest.raises(ValueError, match="axis|Axial motion"):
        build(sources=[adapter.SourceBasis("s", 2, "axial", direction, "p")],
              channel_ports={"ch": ["p"]})


@pytest.mark.parametrize("direction", [[0, 0, 1e300], [0, 0, -1e-300]])
def test_axis_normalization_precedes_solver_precision_cast(build, direction):
    built = build(sources=[adapter.SourceBasis("s", 2, "axial", direction, "p")],
                  channel_ports={"ch": ["p"]}, precision="float32")
    axis = built.wire["compiled_system"]["components"][0]["parameters"]["motion_axis"]
    assert axis == [0, 0, np.sign(direction[2])]


def test_source_and_tag_aliases_cannot_silently_change_stored_identity(build):
    with pytest.raises(ValueError, match="share a physical tag"):
        build(sources=[adapter.SourceBasis("a", 2, port_id="a"), adapter.SourceBasis("b", 2, port_id="b")],
              channel_ports={"ch": ["a", "b"]})
    with pytest.raises(ValueError, match="same source twice"):
        build(sources=[adapter.SourceBasis("a", 2, port_id="a"),
                       adapter.SourceBasis("a", 2, "axial", [0, 0, 1], "b")],
              channel_ports={"ch": ["a", "b"]})
    with pytest.raises(ValueError, match="real numbers"):
        build(frequencies_hz=[True])


@pytest.mark.parametrize("mutation", [
    lambda text: text.replace("2.2 0 8", "4.1 0 8"),
    lambda text: text.replace("40 0 0 0", "40 nan 0 0"),
    lambda text: text.replace("9 1 0 0", "40 1 0 0"),
    lambda text: text.replace("51 2 2 2 2 40 9 71", "51 2 2 2 2 40 40 71"),
    lambda text: text.replace("51 2 2 2 2 40 9 71", "51 2 2 2 2 40 9 999"),
    lambda text: text.replace("51 2 2 2 2 40 9 71", "51 2 2 0 2 40 9 71"),
])
def test_bad_meshes_never_reach_official_submission(msh, mutation):
    with pytest.raises(ValueError):
        read_surface(mutation(msh))


@pytest.mark.parametrize("symmetry", ["xy", "y", "x"])
def test_legacy_symmetry_aliases_cannot_select_a_different_cut_plane(build, symmetry):
    with pytest.raises(ValueError, match=f"Unsupported BEAT symmetry '{symmetry}'"):
        build(symmetry=symmetry)


@pytest.mark.parametrize("quadrants,mode", [(1, "xy"), (12, "x"), (14, "x"), (1234, "off")])
def test_current_wg_plane_names_select_the_intended_images(msh, quadrants, mode):
    context = SolverContext(None, (100, 1000), 3, quadrants=quadrants)
    built = adapter.build_parametric_request(msh, context)
    assert built.wire["solver_options"]["symmetry"] == mode


def test_float32_frequency_aliases_map_every_row_by_requested_float64_order(build, compiled_result):
    from server.solver.beat_adapter.results import map_sweep
    from .conftest import EventStream

    frequencies = [500.0, 500.000001]
    built = build(frequencies_hz=frequencies, precision="float32")
    assert built.wire["frequencies_hz"] == frequencies
    echoes = np.asarray(frequencies, dtype=np.float32)
    assert echoes[0] == echoes[1]
    rows = [compiled_result(built, frequency=float(echo), boundary_pressure=[[value + 2j] * 4])
            for echo, value in zip(echoes, [3, 7])]
    stream = EventStream([{"type": "result", "result": raw} for raw in rows]
                         + [{"type": "completed", "solved_count": 2}])
    callbacks = []
    result = map_sweep(stream, built.wire["frequencies_hz"], layout=built.layout,
                       source_area_m2=built.channel_loading["stored-channel"].area_m2,
                       excitation_port_id="excitation:source", precision="float32",
                       boundary_loading=built.channel_loading["stored-channel"],
                       on_frequency_result=lambda index, frequency, entry: callbacks.append((index, frequency)))
    np.testing.assert_array_equal(result.frequencies_hz, frequencies)
    assert callbacks == list(enumerate(frequencies))
    assert [entry["frequency_hz"] for entry in result.solver_log] == frequencies
    np.testing.assert_allclose(result.impedance, [(value + 2j) * acceleration_scale(frequency)
                                                for value, frequency in zip([3, 7], frequencies)])
    assert not result.is_partial and stream.closed == 1


@pytest.mark.parametrize("precision", ["float32", "float64"])
def test_exact_float64_duplicate_requested_frequencies_match_hbb_refusal(build, precision):
    with pytest.raises(ValueError, match="distinct in Float64"):
        build(frequencies_hz=[500., 500.], precision=precision)


@pytest.mark.parametrize("element_type", [3, 4, 5, 6, 7, 8, 9, 11, 16, 99])
def test_unsupported_gmsh_element_types_never_leave_surface_holes(msh, element_type):
    extra = f"53 {element_type} 2 1 1 40 9 71 3"
    msh = msh.replace("$Elements\n2\n", "$Elements\n3\n").replace("$EndElements", extra + "\n$EndElements")
    with pytest.raises(ValueError, match=f"Unsupported Gmsh element type {element_type};"):
        read_surface(msh)


def test_gmsh_points_and_lines_can_accompany_surface_triangles(msh):
    msh = msh.replace("$Elements\n2\n", "$Elements\n4\n").replace(
        "$EndElements", "53 15 2 1 1 40\n54 1 2 1 1 40 9\n$EndElements")
    assert read_surface(msh).element_ids == (51, 52)


def test_face_area_arithmetic_stays_float64_on_float32_solver_mesh(make_mesh):
    from dataclasses import replace

    msh = make_mesh([[0, 0, 0], [.123456789, 0, .01234567], [0, .987654321, .07654321]],
                    [[0, 1, 2]], [2])
    mesh = read_surface(msh)
    single = replace(mesh, points_m=mesh.points_m.astype(np.float32))
    points = single.points_m.astype(np.float64)
    expected = np.linalg.norm(np.cross(points[1] - points[0], points[2] - points[0])) / 2
    assert single.areas_m2.dtype == np.float64
    assert single.areas_m2[0] == expected
    built = adapter.build_request(msh, sources=[adapter.SourceBasis("source", 2, port_id="p")],
                                  channel_ports={"ch": ["p"]}, frame=FRAME,
                                  frequencies_hz=[500], layout=build_observations(sphere_grid=None))
    assert built.channel_loading["ch"].area_m2 == expected


@pytest.mark.parametrize("imported_path", [False, True])
def test_ground_min_clearance_reaches_wire_from_wg_entry_points(msh, imported, imported_path):
    context = SolverContext(None, (100, 1000), 3, ground_plane=GroundPlane("y", .5))
    options = {"ground_plane_min_clearance_m": .4}
    if imported_path:
        msh, record = imported
        built = adapter.build_imported_request(msh, context, record,
                                                [DriveChannel(id="ch", source_ids=["front:id"])], **options)
    else:
        built = adapter.build_parametric_request(msh, context, **options)
    assert built.wire["solver_options"]["symmetry"] == "ground"
    assert built.wire["solver_options"]["ground_plane_min_clearance_m"] == .4


@pytest.mark.parametrize("motion", ["normal", "axial"])
def test_optional_official_schema_validates_request(build, official_contract, motion):
    source = adapter.SourceBasis("source", 2, motion, [0, 0, 1] if motion == "axial" else None, "p")
    official_contract(build(sources=[source], channel_ports={"ch": ["p"]}))


def test_pure_request_parity_runs_without_optional_checkout_or_packages(build, monkeypatch, compiled_result):
    from . import conftest as fixtures

    monkeypatch.setattr(fixtures, "_official_contract", lambda: None)
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith(("beat_engine", "hornlab_beat_bem")):
            raise AssertionError(f"Pure parity imported optional package {name}")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    built = build(sources=[adapter.SourceBasis("source", 2, "axial", [0, 0, -1], "p")],
                  channel_ports={"ch": ["p"]}, frequencies_hz=[500.])
    assert built.wire["solver_options"]["wavelength_kh_q2_max"] == 2.
    assert built.wire["compiled_system"]["components"][0]["parameters"]["motion_axis"] == [0., 0., -1.]
    row = parse_compiled_frequency(compiled_result(built, boundary_pressure=[[3 + 2j] * 4]),
                                    built, frequency_hz=500.)["ch"]
    assert row.impedance == pytest.approx((3 + 2j) * acceleration_scale(500.))


def test_ground_clearance_is_sent_only_with_a_ground_plane(build):
    assert "ground_plane_min_clearance_m" not in build(ground_plane_min_clearance_m=.4).wire["solver_options"]
