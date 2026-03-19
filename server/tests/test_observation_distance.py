import unittest
from unittest.mock import patch

import numpy as np

from solver.observation import infer_observation_frame
from solver.solve import solve
from solver.solve_optimized import solve_optimized


class _GridStub:
    def __init__(self):
        self.vertices = np.array(
            [[0.0, 0.1, -0.1], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0]], dtype=float
        )
        self.elements = np.array([[0], [1], [2]], dtype=np.int32)
        self.domain_indices = np.array([2], dtype=np.int32)


def _mesh_stub():
    grid = _GridStub()
    return {
        "grid": grid,
        "throat_elements": np.array([0], dtype=np.int32),
        "wall_elements": np.array([], dtype=np.int32),
        "mouth_elements": np.array([], dtype=np.int32),
        "original_vertices": grid.vertices.copy(),
        "original_indices": grid.elements.copy(),
        "original_surface_tags": np.array([2], dtype=np.int32),
        "mesh_metadata": {"fullCircle": True},
        "unit_detection": {"source": "metadata.units", "warnings": []},
    }


_SOLVE_FREQ_TARGET = "solver.solve_optimized.HornBEMSolver._solve_single_frequency"
_HORN_INIT_TARGET = "solver.solve_optimized.HornBEMSolver.__init__"


def _stub_horn_init(self, grid, physical_tags, **kwargs):
    """Minimal HornBEMSolver.__init__ stub for unit tests."""
    self.grid = grid
    self.physical_tags = physical_tags
    self.c = kwargs.get("sound_speed", 343.0)
    self.rho = kwargs.get("rho", 1.21)
    self.tag_throat = kwargs.get("tag_throat", 2)
    self.boundary_interface = kwargs.get("boundary_interface", "opencl")
    self.potential_interface = kwargs.get("potential_interface", "opencl")
    self.bem_precision = kwargs.get("bem_precision", "double")
    self.use_burton_miller = kwargs.get("use_burton_miller", True)
    self.p1_space = None
    self.dp0_space = None
    self.lhs_identity = None
    self.rhs_identity = None
    self.driver_dofs = np.array([0], dtype=np.int32)
    self.enclosure_dofs = np.array([], dtype=np.int32)
    self.throat_element_areas = np.array([0.5], dtype=float)
    self.throat_p1_dofs = np.array([[0, 1, 2]], dtype=np.int32)
    self.unit_velocity_fun = None


class ObservationDistanceForwardingTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._patchers = [
            patch("solver.solve.boundary_device_interface", return_value="opencl"),
            patch("solver.solve.potential_device_interface", return_value="opencl"),
            patch("solver.solve_optimized.boundary_device_interface", return_value="opencl"),
            patch("solver.solve_optimized.potential_device_interface", return_value="opencl"),
            patch(
                "solver.solve_optimized.selected_device_metadata",
                return_value={
                    "requested_mode": "auto",
                    "selected_mode": "opencl_cpu",
                    "interface": "opencl",
                    "device_type": "cpu",
                    "device_name": "Fake CPU",
                    "fallback_reason": None,
                    "available_modes": ["auto", "opencl_cpu", "opencl_gpu"],
                    "requested": "auto",
                    "selected": "opencl",
                    "runtime_selected": "opencl",
                    "runtime_retry_attempted": False,
                    "runtime_retry_outcome": "not_needed",
                    "runtime_profile": "default",
                },
            ),
            patch(
                "solver.solve.selected_device_metadata",
                return_value={
                    "requested_mode": "auto",
                    "selected_mode": "opencl_cpu",
                    "interface": "opencl",
                    "device_type": "cpu",
                    "device_name": "Fake CPU",
                    "fallback_reason": None,
                    "available_modes": ["auto", "opencl_cpu", "opencl_gpu"],
                    "requested": "auto",
                    "selected": "opencl",
                    "runtime_selected": "opencl",
                    "runtime_retry_attempted": False,
                    "runtime_retry_outcome": "not_needed",
                    "runtime_profile": "default",
                },
            ),
            patch(_HORN_INIT_TARGET, _stub_horn_init),
        ]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self._patchers):
            patcher.stop()
        super().tearDown()

    def test_legacy_solver_forwards_polar_distance_to_on_axis_observer(self):
        mesh = _mesh_stub()
        seen_distances = []

        def _solve_frequency_stub(*_args, **kwargs):
            seen_distances.append(kwargs.get("observation_distance_m"))
            return (90.0, complex(1.0, 0.0), 6.0)

        with patch("solver.solve.solve_frequency", side_effect=_solve_frequency_stub), patch(
            "solver.solve.calculate_directivity_patterns",
            return_value={"horizontal": [], "vertical": [], "diagonal": []},
        ):
            solve(
                mesh=mesh,
                frequency_range=[200.0, 400.0],
                num_frequencies=2,
                sim_type="2",
                polar_config={"distance": 2.5},
                mesh_validation_mode="off",
            )

        self.assertEqual(seen_distances, [2.5, 2.5])

    def test_legacy_solver_persists_effective_directivity_metadata(self):
        mesh = _mesh_stub()

        with patch("solver.solve.solve_frequency", return_value=(90.0, complex(1.0, 0.0), 6.0)), patch(
            "solver.solve.calculate_directivity_patterns",
            return_value={"horizontal": [], "vertical": [], "diagonal": []},
        ):
            results = solve(
                mesh=mesh,
                frequency_range=[200.0, 200.0],
                num_frequencies=1,
                sim_type="2",
                polar_config={
                    "distance": 2.5,
                    "angle_range": [10.0, 90.0, 9],
                    "enabled_axes": [" vertical ", "horizontal", "invalid", "horizontal"],
                    "norm_angle": "12.5",
                    "inclination": 42,
                    "observation_origin": "throat",
                },
                mesh_validation_mode="off",
            )

        metadata = results["metadata"]["directivity"]
        self.assertEqual(metadata["angle_range_degrees"], [10.0, 90.0])
        self.assertEqual(metadata["sample_count"], 9)
        self.assertEqual(metadata["angular_step_degrees"], 10.0)
        self.assertEqual(metadata["enabled_axes"], ["vertical", "horizontal"])
        self.assertEqual(metadata["normalization_angle_degrees"], 12.5)
        self.assertEqual(metadata["diagonal_angle_degrees"], 42.0)
        self.assertEqual(metadata["observation_origin"], "mouth")
        self.assertEqual(metadata["requested_distance_m"], 2.5)
        self.assertEqual(metadata["effective_distance_m"], 2.5)

    def test_optimized_solver_forwards_polar_distance_to_on_axis_observer(self):
        mesh = _mesh_stub()
        seen_distances = []

        def _solve_frequency_cached_stub(*_args, **kwargs):
            seen_distances.append(kwargs.get("observation_distance_m"))
            return (90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su"), 15)

        with patch(
            _SOLVE_FREQ_TARGET,
            side_effect=_solve_frequency_cached_stub,
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value={"horizontal": [], "vertical": [], "diagonal": []},
        ):
            solve_optimized(
                mesh=mesh,
                frequency_range=[200.0, 400.0],
                num_frequencies=2,
                sim_type="2",
                polar_config={"distance": 2.5},
                verbose=False,
                mesh_validation_mode="off",
            )

        self.assertEqual(seen_distances, [2.5, 2.5])

    def test_legacy_solver_reuses_observation_frame_across_frequencies(self):
        mesh = _mesh_stub()
        sentinel_frame = {
            "axis": np.array([0.0, 0.0, 1.0]),
            "mouth_center": np.zeros(3),
            "u": np.array([1.0, 0.0, 0.0]),
            "v": np.array([0.0, 1.0, 0.0]),
        }
        seen_frames = []
        directivity_frames = []

        def _solve_frequency_stub(*_args, **kwargs):
            seen_frames.append(kwargs.get("observation_frame"))
            return (90.0, complex(1.0, 0.0), 6.0)

        def _directivity_stub(*_args, **kwargs):
            directivity_frames.append(kwargs.get("observation_frame"))
            return {"horizontal": [], "vertical": [], "diagonal": []}

        with patch("solver.solve.infer_observation_frame", return_value=sentinel_frame) as infer_mock, patch(
            "solver.solve.solve_frequency",
            side_effect=_solve_frequency_stub,
        ), patch(
            "solver.solve.calculate_directivity_patterns",
            side_effect=_directivity_stub,
        ):
            solve(
                mesh=mesh,
                frequency_range=[200.0, 400.0],
                num_frequencies=2,
                sim_type="2",
                polar_config={"distance": 2.5},
                mesh_validation_mode="off",
            )

        self.assertEqual(infer_mock.call_count, 1)
        self.assertEqual(seen_frames, [sentinel_frame, sentinel_frame])
        self.assertEqual(directivity_frames, [sentinel_frame])

    def test_optimized_solver_reuses_observation_frame_across_frequencies(self):
        mesh = _mesh_stub()
        sentinel_frame = {
            "axis": np.array([0.0, 0.0, 1.0]),
            "origin_center": np.zeros(3),
            "mouth_center": np.zeros(3),
            "u": np.array([1.0, 0.0, 0.0]),
            "v": np.array([0.0, 1.0, 0.0]),
        }
        seen_frames = []
        directivity_frames = []

        def _solve_frequency_cached_stub(*_args, **kwargs):
            seen_frames.append(kwargs.get("observation_frame"))
            return (90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su"), 15)

        def _directivity_stub(*_args, **kwargs):
            directivity_frames.append(kwargs.get("observation_frame"))
            return {"horizontal": [], "vertical": [], "diagonal": []}

        with patch("solver.solve_optimized.infer_observation_frame", return_value=sentinel_frame) as infer_mock, patch(
            _SOLVE_FREQ_TARGET,
            side_effect=_solve_frequency_cached_stub,
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            side_effect=_directivity_stub,
        ):
            solve_optimized(
                mesh=mesh,
                frequency_range=[200.0, 400.0],
                num_frequencies=2,
                sim_type="2",
                polar_config={"distance": 2.5},
                verbose=False,
                mesh_validation_mode="off",
            )

        self.assertEqual(infer_mock.call_count, 1)
        self.assertEqual(seen_frames, [sentinel_frame, sentinel_frame])
        self.assertEqual(directivity_frames, [sentinel_frame])

    def test_optimized_solver_adjusts_distance_and_reuses_it_for_directivity(self):
        mesh = _mesh_stub()
        mesh["grid"].vertices = np.array(
            [[0.0, 0.1, -0.1], [0.0, 0.0, 0.0], [0.0, 0.0, 2.5]], dtype=float
        )
        sentinel_frame = {
            "axis": np.array([0.0, 0.0, 1.0]),
            "origin_center": np.zeros(3),
            "mouth_center": np.array([0.0, 0.0, 2.5]),
            "u": np.array([1.0, 0.0, 0.0]),
            "v": np.array([0.0, 1.0, 0.0]),
        }
        seen_distances = []
        seen_directivity_distances = []

        def _solve_frequency_cached_stub(*_args, **kwargs):
            seen_distances.append(kwargs.get("observation_distance_m"))
            return (90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su"), 15)

        def _directivity_stub(*args, **kwargs):
            polar_config = args[5] if len(args) > 5 else kwargs.get("polar_config", {})
            seen_directivity_distances.append((polar_config or {}).get("distance"))
            return {"horizontal": [], "vertical": [], "diagonal": []}

        with patch(
            _SOLVE_FREQ_TARGET,
            side_effect=_solve_frequency_cached_stub,
        ), patch(
            "solver.solve_optimized.infer_observation_frame",
            return_value=sentinel_frame,
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            side_effect=_directivity_stub,
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[200.0, 200.0],
                num_frequencies=1,
                sim_type="2",
                polar_config={
                    "distance": 1.0,
                    "angle_range": [0.0, 90.0, 10],
                    "enabled_axes": ["diagonal", "horizontal"],
                    "norm_angle": 7.5,
                    "inclination": 22.0,
                    "observation_origin": "throat",
                },
                verbose=False,
                mesh_validation_mode="off",
            )

        adjusted_distance = seen_distances[0]
        metadata = results["metadata"]["directivity"]
        self.assertGreater(adjusted_distance, 1.0)
        self.assertEqual(seen_directivity_distances, [adjusted_distance])
        self.assertTrue(results["metadata"]["observation"]["adjusted"])
        self.assertEqual(metadata["angle_range_degrees"], [0.0, 90.0])
        self.assertEqual(metadata["sample_count"], 10)
        self.assertEqual(metadata["angular_step_degrees"], 10.0)
        self.assertEqual(metadata["enabled_axes"], ["diagonal", "horizontal"])
        self.assertEqual(metadata["normalization_angle_degrees"], 7.5)
        self.assertEqual(metadata["diagonal_angle_degrees"], 22.0)
        self.assertEqual(metadata["observation_origin"], "throat")
        self.assertEqual(metadata["requested_distance_m"], 1.0)
        self.assertEqual(metadata["effective_distance_m"], adjusted_distance)


class ObservationOriginTest(unittest.TestCase):
    def _create_horn_grid(self, horn_length_m: float = 0.12):
        class HornGridStub:
            pass

        grid = HornGridStub()
        throat_y = 0.0
        mouth_y = horn_length_m
        grid.vertices = np.array(
            [
                [0.0, throat_y, 0.0],
                [0.01, throat_y, 0.0],
                [0.0, throat_y, 0.01],
                [0.05, mouth_y, 0.0],
                [-0.05, mouth_y, 0.0],
                [0.0, mouth_y, 0.05],
                [0.0, mouth_y + 0.001, 0.0],
            ],
            dtype=np.float64,
        ).T
        grid.elements = np.array(
            [[0, 1, 2], [3, 4, 5], [3, 5, 6]],
            dtype=np.int64,
        )
        grid.domain_indices = np.array([2, 1, 1], dtype=np.int64)
        return grid, horn_length_m

    def test_mouth_vs_throat_origin_produces_different_origins(self):
        grid, _ = self._create_horn_grid()
        frame_mouth = infer_observation_frame(grid, observation_origin="mouth")
        frame_throat = infer_observation_frame(grid, observation_origin="throat")

        self.assertIn("origin_center", frame_mouth)
        self.assertIn("origin_center", frame_throat)
        self.assertIn("mouth_center", frame_mouth)
        self.assertIn("source_center", frame_mouth)

        mouth_origin = np.asarray(frame_mouth["origin_center"], dtype=np.float64)
        throat_origin = np.asarray(frame_throat["origin_center"], dtype=np.float64)
        mouth_center = np.asarray(frame_mouth["mouth_center"], dtype=np.float64)
        source_center = np.asarray(frame_mouth["source_center"], dtype=np.float64)

        np.testing.assert_array_almost_equal(mouth_origin, mouth_center)
        np.testing.assert_array_almost_equal(throat_origin, source_center)

    def test_horn_length_equals_mouth_throat_distance(self):
        grid, horn_length = self._create_horn_grid()
        frame = infer_observation_frame(grid, observation_origin="mouth")

        mouth_center = np.asarray(frame["mouth_center"], dtype=np.float64)
        source_center = np.asarray(frame["source_center"], dtype=np.float64)
        axis = np.asarray(frame["axis"], dtype=np.float64)

        along_axis = mouth_center - source_center
        measured_length = float(np.abs(np.dot(along_axis, axis)))

        self.assertGreater(measured_length, 0.0, "Horn length should be positive")
        self.assertLess(
            abs(measured_length - horn_length),
            0.06,
            f"Measured {measured_length:.4f}m should be within 0.06m of expected {horn_length}m",
        )

    def test_observation_origin_throat_uses_source_center(self):
        grid, _ = self._create_horn_grid()
        frame = infer_observation_frame(grid, observation_origin="throat")

        origin_center = np.asarray(frame["origin_center"], dtype=np.float64)
        source_center = np.asarray(frame["source_center"], dtype=np.float64)

        np.testing.assert_array_almost_equal(origin_center, source_center)

    def test_observation_origin_mouth_uses_mouth_center(self):
        grid, _ = self._create_horn_grid()
        frame = infer_observation_frame(grid, observation_origin="mouth")

        origin_center = np.asarray(frame["origin_center"], dtype=np.float64)
        mouth_center = np.asarray(frame["mouth_center"], dtype=np.float64)

        np.testing.assert_array_almost_equal(origin_center, mouth_center)

    def test_observation_origin_case_insensitive(self):
        grid, _ = self._create_horn_grid()
        frame_lower = infer_observation_frame(grid, observation_origin="throat")
        frame_upper = infer_observation_frame(grid, observation_origin="THROAT")
        frame_mixed = infer_observation_frame(grid, observation_origin="  Throat  ")

        np.testing.assert_array_almost_equal(
            frame_lower["origin_center"], frame_upper["origin_center"]
        )
        np.testing.assert_array_almost_equal(
            frame_lower["origin_center"], frame_mixed["origin_center"]
        )


if __name__ == "__main__":
    unittest.main()
