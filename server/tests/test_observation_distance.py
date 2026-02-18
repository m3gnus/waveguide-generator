import unittest
from unittest.mock import patch

import numpy as np

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


class ObservationDistanceForwardingTest(unittest.TestCase):
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

    def test_optimized_solver_forwards_polar_distance_to_on_axis_observer(self):
        mesh = _mesh_stub()
        seen_distances = []

        def _solve_frequency_cached_stub(*_args, **kwargs):
            seen_distances.append(kwargs.get("observation_distance_m"))
            return (90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su"))

        with patch(
            "solver.solve_optimized.solve_frequency_cached",
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
                enable_symmetry=False,
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
            "mouth_center": np.zeros(3),
            "u": np.array([1.0, 0.0, 0.0]),
            "v": np.array([0.0, 1.0, 0.0]),
        }
        seen_frames = []
        directivity_frames = []

        def _solve_frequency_cached_stub(*_args, **kwargs):
            seen_frames.append(kwargs.get("observation_frame"))
            return (90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su"))

        def _directivity_stub(*_args, **kwargs):
            directivity_frames.append(kwargs.get("observation_frame"))
            return {"horizontal": [], "vertical": [], "diagonal": []}

        with patch("solver.solve_optimized.infer_observation_frame", return_value=sentinel_frame) as infer_mock, patch(
            "solver.solve_optimized.solve_frequency_cached",
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
                enable_symmetry=False,
                verbose=False,
                mesh_validation_mode="off",
            )

        self.assertEqual(infer_mock.call_count, 1)
        self.assertEqual(seen_frames, [sentinel_frame, sentinel_frame])
        self.assertEqual(directivity_frames, [sentinel_frame])


if __name__ == "__main__":
    unittest.main()
