"""Frozen donor coordinates and pure HBB frame/request helpers as controls."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from hornlab_beat_bem import ObservationConfig, ObservationFrame, SolveConfig
from hornlab_beat_bem.sweep import _request_payload, _validated_frame_translation

from server.solver.beat_adapter.observations import build_observations

EXPECTED = json.loads((Path(__file__).parent / "fixtures/hbb_observations.json").read_text())


def test_cuts_match_frozen_hbb_order_and_non_45_degree_diagonal():
    layout = build_observations(angle_range=(-90.0, 90.0, 5),
                                planes=("diagonal", "vertical", "horizontal"),
                                inclination_deg=30, sphere_grid=None, precision="float64")
    np.testing.assert_array_equal(layout.angles_deg, EXPECTED["polar_angles_deg"])
    assert tuple(layout.points_m) == layout.planes
    for plane in layout.planes:
        np.testing.assert_allclose(layout.points_m[plane],
                                   EXPECTED["cuts_radius_2_inclination_30"][plane], atol=1e-15, rtol=0)
    assert [item["id"] for item in layout.exterior_outputs()] == [
        "pressure:diagonal", "pressure:vertical", "pressure:horizontal"]
    for output in layout.exterior_outputs():
        assert output["quantity"] == "exterior_pressure" and output["target_ids"] == []
        np.testing.assert_array_equal(output["options"]["points_m"],
                                      layout.points_m[output["id"].split(":")[1]])


@pytest.mark.parametrize("theta_max,key", [(180, "sphere_3x4_radius_2"),
                                           (90, "hemisphere_3x4_radius_2")])
def test_small_complete_sphere_matches_donor(theta_max, key):
    layout = build_observations(sphere_grid=(3, 4), sphere_theta_max_deg=theta_max, precision="float64")
    np.testing.assert_allclose(layout.points_m["sphere"], EXPECTED[key], atol=1e-15, rtol=0)
    np.testing.assert_array_equal(layout.sphere_theta_deg, np.repeat([0, theta_max / 2, theta_max], 4))
    np.testing.assert_array_equal(layout.sphere_phi_deg, np.tile([0, 90, 180, 270], 3))


@pytest.mark.parametrize("precision,tolerance", [("float64", 3e-15), ("float32", 7e-7)])
def test_37x72_sphere_has_exact_axes_endpoints_and_theta_major_order(precision, tolerance):
    layout = build_observations(precision=precision)
    assert layout.points_m["sphere"].shape == (2664, 3)
    np.testing.assert_array_equal(layout.sphere_theta_deg, np.repeat(np.linspace(0, 180, 37), 72))
    np.testing.assert_array_equal(layout.sphere_phi_deg, np.tile(np.arange(72) * 5.0, 37))
    points = layout.points_m["sphere"]
    np.testing.assert_allclose(points[EXPECTED["sphere_37x72_indices"]],
                               EXPECTED["sphere_37x72_radius_2_samples"], atol=tolerance, rtol=0)
    np.testing.assert_allclose(np.linalg.norm(points, axis=1), 2.0, atol=tolerance, rtol=0)
    assert layout.sphere_theta_deg[-1] == 180.0
    assert layout.sphere_phi_deg[-1] == 355.0  # no wrap column
    np.testing.assert_allclose(points.reshape(37, 72, 3)[:, :, 2],
                               np.broadcast_to(points[::72, 2, None], (37, 72)), atol=0, rtol=0)


def test_frame_origin_matches_hbb_mesh_translation_and_request(tmp_path):
    origin = np.array([0.2, -0.4, 0.25])
    frame = ObservationFrame(axis=np.array([0, 0, 1]), origin=origin,
                             u=np.array([1, 0, 0]), v=np.array([0, 1, 0]))
    observation = ObservationConfig(planes=["diagonal", "horizontal"], distance_m=2,
                                    angle_min_deg=-90, angle_max_deg=90, angle_count=5,
                                    inclination_deg=30, sphere_grid=(3, 4), origin="throat")
    config = SolveConfig(observation=observation, frame_override=frame)
    translation = _validated_frame_translation(frame)
    payload = _request_payload(tmp_path / "unused.msh", np.array([500.0]), config, translation)
    assert payload["config"]["step_size"] == 45
    assert payload["config"]["diagonal_inclination_deg"] == 30
    layout = build_observations(angle_range=(-90, 90, 5), planes=observation.planes,
                                origin_m=origin, inclination_deg=30, sphere_grid=(3, 4), precision="float64")
    for name in observation.planes:
        # Moving HBB's mesh by -origin is identical to observing origin+local
        # points on the original WG mesh; no translation inside official BEAT.
        np.testing.assert_allclose(layout.points_m[name] + translation,
                                   EXPECTED["cuts_radius_2_inclination_30"][name], atol=5e-16, rtol=0)


def test_imported_global_frame_maps_all_cuts_and_sphere_together():
    local = build_observations(precision="float64")
    rotation = np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], dtype=float)
    origin = np.array([0.2, -0.4, 0.25])
    mapped = build_observations(origin_m=origin, u=rotation[:, 0], v=rotation[:, 1],
                                axis=rotation[:, 2], precision="float64")
    for name in local.points_m:
        np.testing.assert_array_equal(mapped.points_m[name], local.points_m[name] @ rotation.T + origin)


def test_polar_metadata_retains_hbb_float32_angle_rounding():
    layout = build_observations(angle_range=(-10, 10, 4), sphere_grid=None, precision="float64")
    np.testing.assert_array_equal(layout.angles_deg, np.linspace(-10, 10, 4).astype(np.float32).astype(float))
    assert len(build_observations(angle_range=(0, 0, 1)).angles_deg) == 1


@pytest.mark.parametrize("kwargs", [
    {"angle_range": (10, 90, 3)}, {"angle_range": (-181, 90, 3)},
    {"angle_range": (0, 180, True)}, {"angle_range": (0, 180, 1)},
    {"distance_m": 0}, {"distance_m": float("nan")}, {"inclination_deg": float("inf")},
    {"planes": ()}, {"planes": ("horizontal", "horizontal")}, {"planes": ("other",)},
    {"sphere_grid": (1, 72)}, {"sphere_grid": (37, 2)}, {"sphere_grid": (37, 3.5)},
    {"sphere_theta_max_deg": 0}, {"sphere_theta_max_deg": 181},
    {"sphere_grid": (1000, 1000)}, {"precision": "single"},
    {"origin_m": (0, float("nan"), 0)}, {"axis": (0, 0, -1)}, {"u": (1, 1, 0)},
])
def test_invalid_observation_contract_is_refused(kwargs):
    with pytest.raises(ValueError):
        build_observations(**kwargs)
