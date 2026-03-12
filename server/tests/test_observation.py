import unittest

import numpy as np

from solver.observation import (
    infer_observation_frame,
    point_from_polar,
    resolve_safe_observation_distance,
)


class _GridStub:
    def __init__(self, vertices: np.ndarray, elements: np.ndarray, domain_indices: np.ndarray):
        self.vertices = vertices
        self.elements = elements
        self.domain_indices = domain_indices


class ObservationFrameTest(unittest.TestCase):
    def test_infers_y_axis_for_y_aligned_geometry(self):
        # Throat/source near y=0, mouth near y=1.
        vertices = np.array([
            [0.0, 0.1, -0.1, 0.0, 0.2, -0.2],  # x
            [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],    # y (axis)
            [0.1, -0.1, 0.0, 0.1, -0.1, 0.0],  # z
        ], dtype=np.float64)
        elements = np.array([
            [0, 1, 2, 3],
            [1, 2, 0, 4],
            [2, 0, 1, 5],
        ], dtype=np.int64)
        domain_indices = np.array([2, 2, 2, 1], dtype=np.int64)
        grid = _GridStub(vertices, elements, domain_indices)

        frame = infer_observation_frame(grid)
        axis = frame["axis"]

        self.assertGreater(float(np.dot(axis, np.array([0.0, 1.0, 0.0]))), 0.8)

    def test_infers_z_axis_for_z_aligned_geometry(self):
        # Throat/source near z=0, mouth near z=1.
        vertices = np.array([
            [0.0, 0.1, -0.1, 0.0, 0.2, -0.2],  # x
            [0.1, -0.1, 0.0, 0.1, -0.1, 0.0],  # y
            [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],    # z (axis)
        ], dtype=np.float64)
        elements = np.array([
            [0, 1, 2, 3],
            [1, 2, 0, 4],
            [2, 0, 1, 5],
        ], dtype=np.int64)
        domain_indices = np.array([2, 2, 2, 1], dtype=np.int64)
        grid = _GridStub(vertices, elements, domain_indices)

        frame = infer_observation_frame(grid)
        axis = frame["axis"]

        self.assertGreater(float(np.dot(axis, np.array([0.0, 0.0, 1.0]))), 0.8)

    def test_on_axis_point_is_in_front_of_mouth(self):
        vertices = np.array([
            [0.0, 0.1, -0.1, 0.0, 0.2, -0.2],
            [0.1, -0.1, 0.0, 0.1, -0.1, 0.0],
            [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
        ], dtype=np.float64)
        elements = np.array([
            [0, 1, 2, 3],
            [1, 2, 0, 4],
            [2, 0, 1, 5],
        ], dtype=np.int64)
        domain_indices = np.array([2, 2, 2, 1], dtype=np.int64)
        grid = _GridStub(vertices, elements, domain_indices)

        frame = infer_observation_frame(grid)
        obs = point_from_polar(
            origin_center=frame["origin_center"],
            axis=frame["axis"],
            u=frame["u"],
            v=frame["v"],
            radius_m=1.0,
            theta_rad=0.0,
            phi_rad=0.0,
        )

        self.assertGreater(float(np.dot(obs - frame["origin_center"], frame["axis"])), 0.99)

    def test_safe_observation_distance_is_pushed_ahead_of_large_geometry(self):
        vertices = np.array([
            [0.0, 0.1, -0.1, 0.0, 0.2, -0.2],
            [0.1, -0.1, 0.0, 0.1, -0.1, 0.0],
            [0.0, 0.0, 0.0, 1.12, 1.12, 1.12],
        ], dtype=np.float64)
        elements = np.array([
            [0, 1, 2, 3],
            [1, 2, 0, 4],
            [2, 0, 1, 5],
        ], dtype=np.int64)
        domain_indices = np.array([2, 2, 2, 1], dtype=np.int64)
        grid = _GridStub(vertices, elements, domain_indices)

        frame = infer_observation_frame(grid)
        resolved = resolve_safe_observation_distance(grid, 1.0, frame)

        self.assertTrue(resolved["adjusted"])
        self.assertGreater(resolved["effective_distance_m"], 1.12)
        self.assertGreaterEqual(
            resolved["effective_distance_m"],
            resolved["min_safe_distance_m"],
        )


if __name__ == "__main__":
    unittest.main()
