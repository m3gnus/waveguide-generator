import unittest
from unittest.mock import patch

import numpy as np

from solver.solve import solve as solve_legacy
from solver.solve_optimized import solve_optimized


class DummyGrid:
    def __init__(self):
        # Simple single-triangle mesh in meters.
        self.vertices = np.array(
            [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]], dtype=float
        )
        self.elements = np.array([[0], [1], [2]], dtype=np.int32)
        self.volumes = np.array([0.5], dtype=float)
        self.domain_indices = np.array([2], dtype=np.int32)


def _directivity_stub():
    return {"horizontal": [], "vertical": [], "diagonal": []}


def _mesh_stub():
    grid = DummyGrid()
    return {
        "grid": grid,
        "throat_elements": np.array([0], dtype=np.int32),
        "wall_elements": np.array([], dtype=np.int32),
        "mouth_elements": np.array([], dtype=np.int32),
        "surface_tags": np.array([2], dtype=np.int32),
        "original_vertices": grid.vertices.copy(),
        "original_indices": grid.elements.copy(),
        "original_surface_tags": np.array([2], dtype=np.int32),
        "mesh_metadata": {"fullCircle": True},
        "unit_detection": {"source": "metadata.units", "warnings": []},
    }


# Patch target: HornBEMSolver._solve_single_frequency inside solve_optimized module
_SOLVE_FREQ_TARGET = "solver.solve_optimized.HornBEMSolver._solve_single_frequency"
# HornBEMSolver.__init__ needs to be stubbed when using DummyGrid in unit tests
# (DummyGrid doesn't have number_of_elements, which bempp_api.function_space requires)
_HORN_INIT_TARGET = "solver.solve_optimized.HornBEMSolver.__init__"
# Return value expected by the new solver: (spl, impedance_complex, di, solution_tuple, iter_count)
_SOLVE_FREQ_RETURN = (90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su"), 15)


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
    # Skip bempp space/operator construction (requires real bempp Grid)
    self.p1_space = None
    self.dp0_space = None
    self.lhs_identity = None
    self.rhs_identity = None
    self.driver_dofs = np.array([0], dtype=np.int32)
    self.enclosure_dofs = np.array([], dtype=np.int32)
    self.throat_element_areas = np.array([0.5], dtype=float)
    self.throat_p1_dofs = np.array([[0, 1, 2]], dtype=np.int32)
    self.unit_velocity_fun = None
    self.symmetry_info = None
    self.symmetry_planes = None
    self.mirror_grids = []
    self.mirror_spaces = []



class _OpenCLRuntimePatchedTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._boundary_patcher = patch(
            "solver.solve_optimized.boundary_device_interface", return_value="opencl"
        )
        self._potential_patcher = patch(
            "solver.solve_optimized.potential_device_interface", return_value="opencl"
        )
        self._metadata_patcher = patch(
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
        )
        self._horn_init_patcher = patch(_HORN_INIT_TARGET, _stub_horn_init)
        self._boundary_patcher.start()
        self._potential_patcher.start()
        self._metadata_patcher.start()
        self._horn_init_patcher.start()

    def tearDown(self):
        self._metadata_patcher.stop()
        self._potential_patcher.stop()
        self._boundary_patcher.stop()
        self._horn_init_patcher.stop()
        super().tearDown()


class SolverHardeningTest(_OpenCLRuntimePatchedTestCase):
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
            _SOLVE_FREQ_TARGET,
            return_value=_SOLVE_FREQ_RETURN,
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
            _SOLVE_FREQ_TARGET,
            return_value=_SOLVE_FREQ_RETURN,
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
            _SOLVE_FREQ_TARGET,
            return_value=_SOLVE_FREQ_RETURN,
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

    def test_advanced_settings_control_precision_warmup_and_symmetry_tolerance(self):
        mesh = _mesh_stub()
        symmetry_result = {
            "policy": {
                "requested": True,
                "applied": False,
                "reason": "no_symmetry_detected",
                "detected_symmetry_type": "full",
                "throat_center": [0.0, 0.0, 0.0],
            },
            "symmetry": {"symmetry_type": "full", "reduction_factor": 1.0},
        }

        with patch(
            "solver.solve_optimized.evaluate_symmetry_policy",
            return_value=symmetry_result,
        ) as evaluate_symmetry_policy, patch(
            _SOLVE_FREQ_TARGET,
            return_value=_SOLVE_FREQ_RETURN,
        ) as solve_freq_mock, patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[100.0, 1000.0],
                num_frequencies=2,
                sim_type="2",
                enable_symmetry=True,
                symmetry_tolerance=0.025,
                enable_warmup=False,
                bem_precision="single",
                use_burton_miller=False,
                verbose=False,
                mesh_validation_mode="off",
            )

        self.assertEqual(evaluate_symmetry_policy.call_args.kwargs["tolerance"], 0.025)
        self.assertEqual(results["metadata"]["performance"]["bem_precision"], "single")
        self.assertEqual(results["metadata"]["failure_count"], 0)

    def test_frequency_failures_do_not_use_placeholder_metrics(self):
        mesh = _mesh_stub()
        calls = {"count": 0}

        def _solve_side_effect(*_args, **_kwargs):
            if calls["count"] == 0:
                calls["count"] += 1
                raise RuntimeError("forced failure")
            return (91.5, complex(2.0, 0.5), 7.0, ("p", "u", "sp", "su"), 12)

        with patch(
            _SOLVE_FREQ_TARGET,
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

    def test_cancellation_callback_stops_optimized_solver_before_frequency_loop(self):
        mesh = _mesh_stub()

        class CancelSolve(RuntimeError):
            pass

        with patch(
            _SOLVE_FREQ_TARGET
        ) as solve_freq_mock, patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ):
            with self.assertRaises(CancelSolve):
                solve_optimized(
                    mesh=mesh,
                    frequency_range=[200.0, 300.0],
                    num_frequencies=2,
                    sim_type="2",
                    enable_symmetry=False,
                    verbose=False,
                    mesh_validation_mode="off",
                    cancellation_callback=lambda: (_ for _ in ()).throw(CancelSolve("cancelled")),
                )

        solve_freq_mock.assert_not_called()

    def test_cancellation_callback_stops_legacy_solver_before_frequency_loop(self):
        mesh = _mesh_stub()

        class CancelSolve(RuntimeError):
            pass

        with patch("solver.solve.boundary_device_interface", return_value="opencl"), patch(
            "solver.solve.potential_device_interface", return_value="opencl"
        ), patch(
            "solver.solve.selected_device_metadata",
            return_value={"selected": "opencl", "selected_mode": "opencl_cpu"},
        ), patch("solver.solve.solve_frequency") as solve_frequency, patch(
            "solver.solve.calculate_directivity_patterns",
            return_value=_directivity_stub(),
        ):
            with self.assertRaises(CancelSolve):
                solve_legacy(
                    mesh=mesh,
                    frequency_range=[200.0, 300.0],
                    num_frequencies=2,
                    sim_type="2",
                    mesh_validation_mode="off",
                    cancellation_callback=lambda: (_ for _ in ()).throw(CancelSolve("cancelled")),
                )

        solve_frequency.assert_not_called()

    def test_opencl_invalid_buffer_size_no_numba_fallback_records_failure(self):
        mesh = _mesh_stub()
        call_count = {"n": 0}

        def _solve_side_effect(*args, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise RuntimeError("create_buffer failed: INVALID_BUFFER_SIZE")
            return (91.5, complex(2.0, 0.5), 7.0, ("p", "u", "sp", "su"), 10)

        with patch(
            _SOLVE_FREQ_TARGET,
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
                frequency_range=[200.0, 300.0],
                num_frequencies=2,
                sim_type="2",
                enable_symmetry=False,
                verbose=False,
                mesh_validation_mode="off",
            )

        self.assertEqual(results["metadata"]["failure_count"], 1)
        self.assertEqual(results["metadata"]["warning_count"], 1)
        self.assertEqual(
            results["metadata"]["warnings"][0]["code"],
            "opencl_runtime_unavailable",
        )
        device_meta = results["metadata"]["device_interface"]
        self.assertEqual(device_meta["runtime_retry_attempted"], True)
        self.assertEqual(device_meta["runtime_profile"], "safe_cpu")
        self.assertEqual(device_meta["runtime_retry_outcome"], "opencl_retry_failed")
        self.assertEqual(device_meta["runtime_selected"], "opencl_unavailable")

    def test_opencl_invalid_buffer_size_recovers_with_safe_profile_retry(self):
        mesh = _mesh_stub()
        call_count = {"n": 0}

        def _solve_side_effect(*args, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("create_buffer failed: INVALID_BUFFER_SIZE")
            return (91.5, complex(2.0, 0.5), 7.0, ("p", "u", "sp", "su"), 10)

        with patch(
            _SOLVE_FREQ_TARGET,
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

        self.assertEqual(results["metadata"]["failure_count"], 0)
        self.assertEqual(results["metadata"]["warning_count"], 0)
        device_meta = results["metadata"]["device_interface"]
        self.assertEqual(device_meta["runtime_retry_attempted"], True)
        self.assertEqual(device_meta["runtime_profile"], "safe_cpu")
        self.assertEqual(device_meta["runtime_retry_outcome"], "opencl_recovered")
        self.assertEqual(device_meta["runtime_selected"], "opencl")


class StrongFormGmresTest(_OpenCLRuntimePatchedTestCase):
    def test_gmres_iteration_count_in_metadata(self):
        mesh = _mesh_stub()
        with patch(
            _SOLVE_FREQ_TARGET,
            return_value=(90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su"), 18),
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[200.0, 400.0],
                num_frequencies=2,
                sim_type="2",
                enable_symmetry=False,
                verbose=False,
                mesh_validation_mode="off",
            )

        perf = results["metadata"]["performance"]
        self.assertIn("gmres_iterations_per_frequency", perf)
        self.assertIn("avg_gmres_iterations", perf)
        self.assertIn("warmup_time_seconds", perf)
        self.assertEqual(perf["gmres_iterations_per_frequency"], [18, 18])
        self.assertEqual(perf["avg_gmres_iterations"], 18.0)

    def test_failed_frequency_has_none_in_iteration_list(self):
        mesh = _mesh_stub()
        calls = {"count": 0}

        def _solve_side_effect(*_args, **_kwargs):
            if calls["count"] == 0:
                calls["count"] += 1
                raise RuntimeError("forced failure")
            return (91.5, complex(2.0, 0.5), 7.0, ("p", "u", "sp", "su"), 20)

        with patch(
            _SOLVE_FREQ_TARGET,
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

        iters = results["metadata"]["performance"]["gmres_iterations_per_frequency"]
        self.assertIsNone(iters[0])
        self.assertEqual(iters[1], 20)
        self.assertEqual(results["metadata"]["performance"]["avg_gmres_iterations"], 20.0)

    def test_warmup_failure_does_not_abort_solve(self):
        """Warm-up failing (e.g. bad grid in tests) must not prevent frequency solve."""
        mesh = _mesh_stub()
        with patch(
            _SOLVE_FREQ_TARGET,
            return_value=(90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su"), 15),
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
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

        # Solve succeeded despite warm-up failing on the dummy grid
        self.assertEqual(results["metadata"]["failure_count"], 0)
        self.assertEqual(len(results["spl_on_axis"]["spl"]), 1)
        self.assertIsNotNone(results["spl_on_axis"]["spl"][0])


if __name__ == "__main__":
    unittest.main()
