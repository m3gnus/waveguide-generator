"""Boundary pressure preserves historical driver loading, including signed sources."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from server.jobs.models import DriverSpec
from server.solver.beat_adapter.driver_loading import BoundaryLoading
from server.solver.beat_adapter.mesh import read_surface
from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.request import SourceBasis, build_request
from server.solver.beat_adapter.results import (
    ResultContractError, acceleration_scale, map_sweep, parse_compiled_frequency,
)
from server.solver.ground_plane import GroundPlane
from server.solver.driver_lem import channel_drive_scaling, self_impedance_from_surface_average
from .conftest import EventStream
from .test_request import FRAME


@pytest.mark.parametrize("shape", ["flat", "curved", "tilted", "rear"])
@pytest.mark.parametrize("symmetry,copies", [("full", 1), ("yz", 2), ("yz+xz", 4), ("ground", 1)])
@pytest.mark.parametrize("motion", ["normal", "axial"])
def test_old_pressure_acoustic_and_electrical_loading_survive_generalized_force(
    shape, symmetry, copies, motion, make_mesh, durable_request, compiled_result,
):
    points = np.array([[0., 0., 0.], [.2, 0., 0.], [0., .1, 0.], [.2, .1, 0.]])
    faces = [[0, 1, 2], [1, 3, 2]]
    if shape == "curved":
        points[3, 2] = .15
    elif shape == "tilted":
        points[:, 2] = points[:, 1] * 2
    elif shape == "rear":
        faces = [list(reversed(face)) for face in faces]
    # Every case lies in the positive x/y quadrant. Source winding may reverse.
    msh = make_mesh(points, faces, [101, 101], node_ids=[10, 7, 22, 30])
    built = durable_request(build_request(
        msh, sources=[SourceBasis("driver", 101, motion, [0, 0, 1] if motion == "axial" else None, "port")],
        channel_ports={"driver-ch": ["port"]}, frame=FRAME, frequencies_hz=[500],
        layout=build_observations(sphere_grid=None), precision="float64",
        symmetry="full" if symmetry == "ground" else symmetry,
        ground=GroundPlane("y", .4) if symmetry == "ground" else None,
        surface_traces=True))
    pressure = np.array([2 + 4j, 3 - 2j, 9 + 1j, 5 - 3j])
    triangles = points[np.asarray(faces)]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    areas = np.linalg.norm(cross, axis=1) / 2
    normals = cross / (2 * areas[:, None])
    # Independent HBB Julia formula: P1 triangle means, physical areas, real copies.
    force_old = sum(sum(pressure[node] for node in face) / 3 * area * copies
                    for face, area in zip(faces, areas))
    mean_old = force_old / (sum(areas) * copies)
    force_generalized = sum(sum(pressure[node] for node in face) / 3 * area * (normal[2] if motion == "axial" else 1) * copies
                            for face, area, normal in zip(faces, areas, normals))
    raw = compiled_result(built, boundary_pressure=pressure[None, :], forces=[force_generalized])
    row = parse_compiled_frequency(raw, built, frequency_hz=500)["driver-ch"]
    scale = acceleration_scale(500)
    assert row.impedance == pytest.approx(mean_old * scale)
    assert row.radiation_impedance == pytest.approx(force_generalized)
    assert row.generalized_impedances == {"port": pytest.approx(force_generalized)}
    np.testing.assert_allclose(row.surface_pressure_complex, pressure * scale)
    old_from_generalized = force_generalized / (sum(areas) * copies)
    if shape == "flat" or motion == "normal":
        assert old_from_generalized == pytest.approx(mean_old)
    else:
        assert abs(old_from_generalized - mean_old) > .1
    full_area = sum(areas) * copies
    acoustic = self_impedance_from_surface_average(np.array([500]), np.array([row.impedance]), full_area)
    np.testing.assert_allclose(acoustic, [np.conj(mean_old) / full_area])
    spec = DriverSpec(sd_cm2=210, bl_t_m=10.5, re_ohm=5.3, le_mh=.5,
                      mmd_g=12, cms_m_per_n=4e-4, rms_kg_per_s=1.2)
    expected_scale, expected_payload = channel_drive_scaling(
        np.array([500]), np.array([mean_old * scale]), full_area, spec, drive_voltage_v=2.83, rg_ohm=.1)
    mapped_scale, mapped_payload = channel_drive_scaling(
        np.array([500]), np.array([row.impedance]), full_area, spec, drive_voltage_v=2.83, rg_ohm=.1)
    np.testing.assert_allclose(mapped_scale, expected_scale)
    assert mapped_payload["electrical_impedance_ohm"] == expected_payload["electrical_impedance_ohm"]


def test_generalized_force_on_a_rear_axis_is_separate_from_pressure_loading(make_mesh, durable_request, compiled_result):
    msh = make_mesh([[0, 0, 0], [.2, 0, 0], [0, .1, 0]], [[2, 1, 0]], [101])
    built = durable_request(build_request(
        msh, sources=[SourceBasis("rear", 101, "axial", [0, 0, -1], "rear")],
        channel_ports={"ch": ["rear"]}, frame=FRAME, frequencies_hz=[500],
        layout=build_observations(sphere_grid=None), precision="float64"))
    raw = compiled_result(built, boundary_pressure=[[2 - 3j] * 3], forces=[.01 * (2 - 3j)])
    row = parse_compiled_frequency(raw, built, frequency_hz=500)["ch"]
    assert row.impedance == pytest.approx((2 - 3j) * acceleration_scale(500))
    assert row.surface_pressure_complex is None  # Pressure-only loading is still computed.


def test_multisource_channels_sum_independent_pressures_and_keep_diagonal_forces(
    make_mesh, durable_request, compiled_result, monkeypatch,
):
    from server.solver.beat_adapter import results as mapping
    decoded = []
    original_decode = mapping.decode_complex_values
    def decode(values, shape):
        decoded.append(values)
        return original_decode(values, shape)
    monkeypatch.setattr(mapping, "decode_complex_values", decode)
    msh = make_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 2], [2, 0, 2], [0, 1, 2]],
                    [[0, 1, 2], [3, 5, 4]], [101, 102])
    built = durable_request(build_request(
        msh, sources=[SourceBasis("a", 101, "axial", [0, 0, 1], "a"),
                      SourceBasis("b", 102, "axial", [0, 0, -1], "b")],
        channel_ports={"one": ["a"], "two": ["b"], "both": ["a", "b"]},
        frame=FRAME, frequencies_hz=[500], layout=build_observations(sphere_grid=(3, 4)),
        precision="float64", surface_traces=True))
    # Include cross pressure on the other patch: the channel loading needs it.
    boundary = np.array([[2, 2, 2, 7, 7, 7], [11, 11, 11, 3, 3, 3]], dtype=complex)
    raw = compiled_result(built, boundary_pressure=boundary, forces=[1 + 2j, 3 - 4j])
    rows = parse_compiled_frequency(raw, built, frequency_hz=500)
    assert len(decoded) == len(raw["quantities"])
    both = rows["both"]
    np.testing.assert_allclose(both.pressure_complex, rows["one"].pressure_complex + rows["two"].pressure_complex)
    np.testing.assert_allclose(both.sphere_pressure_complex, rows["one"].sphere_pressure_complex + rows["two"].sphere_pressure_complex)
    np.testing.assert_allclose(both.surface_pressure_complex, boundary.sum(axis=0) * acceleration_scale(500))
    assert both.impedance == pytest.approx(((13 * .5 + 10 * 1) / 1.5) * acceleration_scale(500))
    assert both.radiation_impedance is None  # Self-force diagonals cannot supply cross-force loading.
    assert both.generalized_impedances == {"a": 1 + 2j, "b": 3 - 4j}
    assert rows["one"].impedance == pytest.approx(2 * acceleration_scale(500))
    assert rows["two"].impedance == pytest.approx(3 * acceleration_scale(500))
    bad = copy.deepcopy(raw)
    bad["excitation_port_ids"].reverse()
    with pytest.raises(ResultContractError, match="excitation identity"):
        parse_compiled_frequency(bad, built, frequency_hz=500)


def test_boundary_loading_in_sweep_preserves_cancellation_and_generalized_force(
    make_mesh, durable_request, compiled_result,
):
    msh = make_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], [2])
    built = durable_request(build_request(
        msh, sources=[SourceBasis("source", 2, "axial", [0, 0, 1], "port")],
        channel_ports={"ch": ["port"]}, frame=FRAME, frequencies_hz=[500, 100],
        layout=build_observations(sphere_grid=None), precision="float64"))
    stream = EventStream([{"type": "result", "result": compiled_result(built)},
                          {"type": "cancelled", "solved_count": 1}])
    result = map_sweep(stream, [500, 100], layout=built.layout, source_area_m2=.5,
                       excitation_port_id="port", precision="float64", source_motion="axial",
                       boundary_loading=built.channel_loading["ch"])
    assert result.cancelled and result.is_partial
    assert stream.closed == 1
    assert result.impedance[0] == pytest.approx((3 + 3j) * acceleration_scale(500))
    np.testing.assert_array_equal(result.radiation_impedance, [5 - 2j])


@pytest.mark.parametrize("pressure", [[1, 2], [1, np.nan, 3], [1, 2, np.inf]])
def test_invalid_boundary_traces_fail_before_driver_loading(pressure, make_mesh):
    msh = make_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], [2])
    loading = BoundaryLoading.from_mesh(read_surface(msh), [2])
    with pytest.raises(ValueError, match="Boundary pressure"):
        loading.mean_pressure(np.array(pressure))
