import unittest
from unittest.mock import patch

import numpy as np

from solver.solve_optimized import solve_optimized


class DummyGrid:
    def __init__(self):
        # Simple single-triangle mesh in meters.
        self.vertices = np.array(
            [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]], dtype=float
        )
        self.elements = np.array([[0], [1], [2]], dtype=np.int32)


def _directivity_stub():
    return {"horizontal": [], "vertical": [], "diagonal": []}


def _mesh_stub():
    grid = DummyGrid()
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


class SolverHardeningTest(unittest.TestCase):
    def test_mesh_validation_strict_blocks_invalid_setup(self):
        mesh = _mesh_stub()
        with patch(
            "solver.solve_optimized.calculate_mesh_statistics",
            return_value={"max_edge_length": 1.0, "num_elements": 1},
        ), patch(
            "solver.solve_optimized.validate_frequency_range",
            return_value={
                "is_valid": False,
                "warnings": ["Requested max frequency exceeds mesh capability."],
                "recommendations": [],
                "max_valid_frequency": 500.0,
                "recommended_max_frequency": 400.0,
                "elements_per_wavelength_at_max": 2.0,
            },
        ), patch(
            "solver.solve_optimized.solve_frequency_cached",
            return_value=(90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su")),
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ):
            with self.assertRaises(ValueError):
                solve_optimized(
                    mesh=mesh,
                    frequency_range=[100.0, 1000.0],
                    num_frequencies=2,
                    sim_type="2",
                    enable_symmetry=False,
                    verbose=False,
                    mesh_validation_mode="strict",
                )

    def test_mesh_validation_warn_allows_run_and_surfaces_warning(self):
        mesh = _mesh_stub()
        with patch(
            "solver.solve_optimized.calculate_mesh_statistics",
            return_value={"max_edge_length": 1.0, "num_elements": 1},
        ), patch(
            "solver.solve_optimized.validate_frequency_range",
            return_value={
                "is_valid": False,
                "warnings": ["Requested max frequency exceeds mesh capability."],
                "recommendations": ["Refine mesh."],
                "max_valid_frequency": 500.0,
                "recommended_max_frequency": 400.0,
                "elements_per_wavelength_at_max": 2.0,
            },
        ), patch(
            "solver.solve_optimized.solve_frequency_cached",
            return_value=(90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su")),
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[100.0, 1000.0],
                num_frequencies=2,
                sim_type="2",
                enable_symmetry=False,
                verbose=False,
                mesh_validation_mode="warn",
            )

        self.assertEqual(results["metadata"]["mesh_validation"]["mode"], "warn")
        self.assertFalse(results["metadata"]["mesh_validation"]["is_valid"])
        self.assertGreater(len(results["metadata"]["mesh_validation"]["warnings"]), 0)

    def test_mesh_validation_off_skips_validation_calls(self):
        mesh = _mesh_stub()
        with patch("solver.solve_optimized.calculate_mesh_statistics") as calc_stats, patch(
            "solver.solve_optimized.validate_frequency_range"
        ) as validate_freq, patch(
            "solver.solve_optimized.solve_frequency_cached",
            return_value=(90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su")),
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[100.0, 1000.0],
                num_frequencies=2,
                sim_type="2",
                enable_symmetry=False,
                verbose=False,
                mesh_validation_mode="off",
            )
        calc_stats.assert_not_called()
        validate_freq.assert_not_called()
        self.assertFalse(results["metadata"]["mesh_validation"]["enabled"])

    def test_frequency_failures_do_not_use_placeholder_metrics(self):
        mesh = _mesh_stub()
        calls = {"count": 0}

        def _solve_side_effect(*_args, **_kwargs):
            if calls["count"] == 0:
                calls["count"] += 1
                raise RuntimeError("forced failure")
            return (91.5, complex(2.0, 0.5), 7.0, ("p", "u", "sp", "su"))

        with patch(
            "solver.solve_optimized.solve_frequency_cached",
            side_effect=_solve_side_effect,
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[200.0, 300.0],
                num_frequencies=2,
                sim_type="2",
                enable_symmetry=False,
                verbose=False,
                mesh_validation_mode="off",
            )

        self.assertIsNone(results["spl_on_axis"]["spl"][0])
        self.assertIsNone(results["impedance"]["real"][0])
        self.assertIsNone(results["di"]["di"][0])
        self.assertEqual(results["metadata"]["failure_count"], 1)
        self.assertTrue(results["metadata"]["partial_success"])
        self.assertEqual(results["metadata"]["failures"][0]["code"], "frequency_solve_failed")

    def test_opencl_invalid_buffer_size_retries_with_numba(self):
        mesh = _mesh_stub()
        attempted_interfaces = []

        def _solve_side_effect(*args, **_kwargs):
            cached_ops = args[5]
            attempted_interfaces.append(cached_ops.boundary_interface)
            if len(attempted_interfaces) <= 2:
                raise RuntimeError("create_buffer failed: INVALID_BUFFER_SIZE")
            return (91.5, complex(2.0, 0.5), 7.0, ("p", "u", "sp", "su"))

        with patch(
            "solver.solve_optimized.solve_frequency_cached",
            side_effect=_solve_side_effect,
        ), patch(
            "solver.solve_optimized.boundary_device_interface",
            return_value="opencl",
        ), patch(
            "solver.solve_optimized.potential_device_interface",
            return_value="opencl",
        ), patch(
            "solver.solve_optimized.configure_opencl_safe_profile",
            return_value={"profile": "safe_cpu", "applied": True, "detail": None},
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value={"horizontal": [], "vertical": [], "diagonal": []},
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[200.0, 200.0],
                num_frequencies=1,
                sim_type="2",
                enable_symmetry=False,
                verbose=False,
                mesh_validation_mode="off",
            )

        self.assertEqual(attempted_interfaces, ["opencl", "opencl", "numba"])
        self.assertEqual(results["metadata"]["failure_count"], 0)
        self.assertEqual(results["metadata"]["warning_count"], 1)
        self.assertEqual(
            results["metadata"]["warnings"][0]["code"],
            "opencl_runtime_fallback_to_numba",
        )
        device_meta = results["metadata"]["device_interface"]
        self.assertEqual(device_meta["runtime_retry_attempted"], True)
        self.assertEqual(device_meta["runtime_profile"], "safe_cpu")
        self.assertEqual(device_meta["runtime_retry_outcome"], "fell_back_to_numba")
        self.assertEqual(device_meta["runtime_selected"], "numba")

    def test_opencl_invalid_buffer_size_recovers_with_safe_profile_retry(self):
        mesh = _mesh_stub()
        attempted_interfaces = []

        def _solve_side_effect(*args, **_kwargs):
            cached_ops = args[5]
            attempted_interfaces.append(cached_ops.boundary_interface)
            if len(attempted_interfaces) == 1:
                raise RuntimeError("create_buffer failed: INVALID_BUFFER_SIZE")
            return (91.5, complex(2.0, 0.5), 7.0, ("p", "u", "sp", "su"))

        with patch(
            "solver.solve_optimized.solve_frequency_cached",
            side_effect=_solve_side_effect,
        ), patch(
            "solver.solve_optimized.boundary_device_interface",
            return_value="opencl",
        ), patch(
            "solver.solve_optimized.potential_device_interface",
            return_value="opencl",
        ), patch(
            "solver.solve_optimized.configure_opencl_safe_profile",
            return_value={"profile": "safe_cpu", "applied": True, "detail": None},
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value={"horizontal": [], "vertical": [], "diagonal": []},
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[200.0, 200.0],
                num_frequencies=1,
                sim_type="2",
                enable_symmetry=False,
                verbose=False,
                mesh_validation_mode="off",
            )

        self.assertEqual(attempted_interfaces, ["opencl", "opencl"])
        self.assertEqual(results["metadata"]["failure_count"], 0)
        self.assertEqual(results["metadata"]["warning_count"], 0)
        device_meta = results["metadata"]["device_interface"]
        self.assertEqual(device_meta["runtime_retry_attempted"], True)
        self.assertEqual(device_meta["runtime_profile"], "safe_cpu")
        self.assertEqual(device_meta["runtime_retry_outcome"], "opencl_recovered")
        self.assertEqual(device_meta["runtime_selected"], "opencl")


if __name__ == "__main__":
    unittest.main()
