from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from solver.cpubem.benchmark import (
    build_comparisons,
    build_observation_points,
    compare_outputs,
    configure_benchmark_cache,
    summarize_runs,
)


class TestCpubemBenchmark(unittest.TestCase):
    def test_cache_configuration_survives_missing_platformdirs(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "solver.cpubem.benchmark.REPO_ROOT", Path(temp_dir)
        ), patch.dict(os.environ), patch.dict(sys.modules, {"platformdirs": None}):
            cache_root = configure_benchmark_cache()

            self.assertEqual(cache_root, Path(temp_dir) / "server" / ".benchmark-cache")
            self.assertEqual(os.environ["LOCALAPPDATA"], str(cache_root / "localappdata"))
            self.assertEqual(os.environ["NUMBA_CACHE_DIR"], str(cache_root / "numba"))

    def test_symmetry_comparison_is_recorded_as_skipped(self):
        outputs = {
            "cpubem": {
                "pressure": np.ones((1, 1, 1), dtype=np.complex128),
                "impedance": np.ones(1, dtype=np.complex128),
            },
            "bempp-opencl": {
                "pressure": np.ones((1, 1, 1), dtype=np.complex128),
                "impedance": np.ones(1, dtype=np.complex128),
            },
        }

        comparisons = build_comparisons(outputs, "yz+xz")

        self.assertEqual(
            comparisons["cpubem_vs_bempp-opencl"],
            {
                "skipped": True,
                "reason": "bempp symmetry mirrors the reduced mesh; domains differ",
            },
        )

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
