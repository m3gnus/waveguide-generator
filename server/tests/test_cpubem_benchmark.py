from __future__ import annotations

import unittest

import numpy as np

from solver.cpubem.benchmark import (
    build_observation_points,
    compare_outputs,
    summarize_runs,
)


class TestCpubemBenchmark(unittest.TestCase):
    def test_observation_points_match_two_polar_planes(self):
        planes, points = build_observation_points(3, 2.0)

        self.assertEqual(planes, ["horizontal", "vertical"])
        self.assertEqual(points.shape, (2, 3, 3))
        np.testing.assert_allclose(
            points[:, 0], [[0.0, 0.0, 2.0]] * 2, atol=1e-15
        )
        np.testing.assert_allclose(
            points[:, -1], [[0.0, 0.0, -2.0]] * 2, atol=1e-15
        )
        np.testing.assert_allclose(np.linalg.norm(points, axis=2), 2.0)

    def test_complex_output_comparison_keeps_amplitude_and_phase(self):
        reference = {
            "pressure": np.array([[[1.0 + 0.0j, 0.0 + 2.0j]]]),
            "impedance": np.array([3.0 + 4.0j]),
        }
        candidate = {
            "pressure": np.array([[[0.0 + 1.0j, 0.0 + 2.0j]]]),
            "impedance": np.array([3.0 + 4.0j]),
        }

        comparison = compare_outputs(reference, candidate)

        self.assertAlmostEqual(
            comparison["pressure_max_abs_error"], np.sqrt(2.0)
        )
        self.assertAlmostEqual(
            comparison["pressure_max_rel_error"], np.sqrt(2.0) / 2.0
        )
        self.assertAlmostEqual(
            comparison["pressure_max_phase_error_rad"], np.pi / 2.0
        )
        self.assertEqual(comparison["pressure_max_magnitude_db_error"], 0.0)
        self.assertEqual(comparison["pressure_max_normalized_db_error"], 0.0)
        self.assertEqual(comparison["impedance_max_abs_error"], 0.0)

    def test_summary_uses_median_backend_throughput(self):
        runs = [
            {
                "backend": "cpubem",
                "total_seconds": 10.0,
                "frequency_runs": [{}, {}],
            },
            {
                "backend": "cpubem",
                "total_seconds": 14.0,
                "frequency_runs": [{}, {}],
            },
            {
                "backend": "bempp-opencl",
                "total_seconds": 2.0,
                "frequency_runs": [{}, {}],
            },
        ]

        summary = summarize_runs(runs)

        self.assertEqual(summary["cpubem"]["repeats"], 2)
        self.assertEqual(summary["cpubem"]["median_total_seconds"], 12.0)
        self.assertEqual(summary["cpubem"]["median_seconds_per_frequency"], 6.0)
        self.assertEqual(
            summary["bempp-opencl"]["median_seconds_per_frequency"], 1.0
        )
