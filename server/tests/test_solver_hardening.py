import unittest
from unittest.mock import patch

import numpy as np

from solver.bem_solver import BEMSolver
from solver.solve_optimized import solve_optimized, _numpy_dtype_for_precision, _normalize_bem_precision


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
    self.bem_precision = kwargs.get("bem_precision", "single")
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
                verbose=False,
                mesh_validation_mode="off",
            )
        calc_stats.assert_not_called()
        validate_freq.assert_not_called()
        self.assertFalse(results["metadata"]["mesh_validation"]["enabled"])

    def test_compat_precision_and_warmup_are_ignored_while_burton_miller_applies(self):
        mesh = _mesh_stub()

        with patch(
            _SOLVE_FREQ_TARGET,
            return_value=_SOLVE_FREQ_RETURN,
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ), patch("solver.solve_optimized.logger.info") as logger_info:
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[100.0, 1000.0],
                num_frequencies=2,
                sim_type="2",
                enable_warmup=True,
                bem_precision="double",
                use_burton_miller=False,
                verbose=False,
                mesh_validation_mode="off",
            )

        self.assertEqual(results["metadata"]["performance"]["bem_precision"], "single")
        self.assertEqual(results["metadata"]["failure_count"], 0)
        self.assertTrue(
            any(
                len(call.args) >= 1
                and "Ignoring compatibility bem_precision=%s" in str(call.args[0])
                for call in logger_info.call_args_list
                if call.args
            )
        )
        self.assertTrue(
            any(
                len(call.args) >= 1
                and "Ignoring compatibility enable_warmup=%s" in str(call.args[0])
                for call in logger_info.call_args_list
                if call.args
            )
        )

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
                    verbose=False,
                    mesh_validation_mode="off",
                    cancellation_callback=lambda: (_ for _ in ()).throw(CancelSolve("cancelled")),
                )

        solve_freq_mock.assert_not_called()

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


class PerformanceMetadataTest(_OpenCLRuntimePatchedTestCase):
    def test_performance_metadata_contains_only_ui_contract_fields(self):
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
                verbose=False,
                mesh_validation_mode="off",
            )

        performance = results["metadata"]["performance"]
        self.assertIn("total_time_seconds", performance)
        self.assertIn("bem_precision", performance)
        self.assertEqual(performance["bem_precision"], "single")
        self.assertIsInstance(performance["total_time_seconds"], float)
        self.assertGreater(performance["total_time_seconds"], 0)

    def test_performance_metadata_omits_removed_fields(self):
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
                verbose=False,
                mesh_validation_mode="off",
            )

        performance = results["metadata"]["performance"]
        self.assertNotIn("warmup_time_seconds", performance)
        self.assertNotIn("gmres_strong_form_supported", performance)
        self.assertNotIn("frequency_solve_time", performance)
        self.assertNotIn("directivity_compute_time", performance)
        self.assertNotIn("time_per_frequency", performance)
        self.assertNotIn("gmres_iterations_per_frequency", performance)
        self.assertNotIn("avg_gmres_iterations", performance)
        self.assertNotIn("reduction_speedup", performance)


class SinglePrecisionTest(_OpenCLRuntimePatchedTestCase):
    def test_numpy_dtype_for_precision_returns_correct_dtype(self):
        self.assertEqual(_numpy_dtype_for_precision("single"), np.complex64)
        self.assertEqual(_numpy_dtype_for_precision("double"), np.complex128)
        self.assertEqual(_numpy_dtype_for_precision("SINGLE"), np.complex64)
        self.assertEqual(_numpy_dtype_for_precision("Single"), np.complex64)
        self.assertEqual(_numpy_dtype_for_precision("DOUBLE"), np.complex128)

    def test_normalize_bem_precision_defaults_to_single(self):
        self.assertEqual(_normalize_bem_precision(None), "single")
        self.assertEqual(_normalize_bem_precision(""), "single")

    def test_normalize_bem_precision_rejects_invalid(self):
        with self.assertRaises(ValueError):
            _normalize_bem_precision("triple")
        with self.assertRaises(ValueError):
            _normalize_bem_precision("float32")

    def test_single_precision_solver_produces_valid_results(self):
        mesh = _mesh_stub()
        with patch(
            _SOLVE_FREQ_TARGET,
            return_value=(90.0, complex(1.0, 0.5), 6.0, ("p", "u", "sp", "su"), 15),
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[200.0, 400.0],
                num_frequencies=2,
                sim_type="2",
                verbose=False,
                mesh_validation_mode="off",
                bem_precision="single",
            )

        self.assertEqual(results["metadata"]["performance"]["bem_precision"], "single")
        self.assertEqual(len(results["spl_on_axis"]["spl"]), 2)
        self.assertEqual(len(results["impedance"]["real"]), 2)
        self.assertEqual(len(results["impedance"]["imaginary"]), 2)
        self.assertEqual(len(results["di"]["di"]), 2)

        for spl in results["spl_on_axis"]["spl"]:
            self.assertIsNotNone(spl)
            self.assertFalse(np.isnan(spl))
        for r, i in zip(results["impedance"]["real"], results["impedance"]["imaginary"]):
            self.assertIsNotNone(r)
            self.assertIsNotNone(i)
            self.assertFalse(np.isnan(r))
            self.assertFalse(np.isnan(i))
        for di in results["di"]["di"]:
            self.assertIsNotNone(di)
            self.assertFalse(np.isnan(di))


class StableEntrypointCompatibilityTest(unittest.TestCase):
    def test_use_optimized_false_is_accepted_but_ignored(self):
        with patch("solver.bem_solver.BEMPP_AVAILABLE", True), patch(
            "solver.bem_solver.selected_device_metadata",
            return_value={
                "selected": "opencl",
                "selected_mode": "opencl_cpu",
                "fallback_reason": None,
            },
        ), patch(
            "solver.bem_solver.solve_optimized",
            return_value={"status": "ok"},
        ) as solve_mock, patch("solver.bem_solver.logger.info") as logger_info:
            solver = BEMSolver()
            results = solver.solve(
                mesh={"grid": object()},
                frequency_range=[200.0, 400.0],
                num_frequencies=2,
                sim_type="2",
                use_optimized=False,
                advanced_settings={"use_burton_miller": False},
            )

        self.assertEqual(results, {"status": "ok"})
        solve_mock.assert_called_once()
        self.assertEqual(solve_mock.call_args.kwargs["use_burton_miller"], False)
        self.assertTrue(
            any(
                "Ignoring compatibility flag use_optimized" in str(call.args[0])
                for call in logger_info.call_args_list
                if call.args
            )
        )

    def test_legacy_advanced_settings_are_accepted_but_ignored(self):
        with patch("solver.bem_solver.BEMPP_AVAILABLE", True), patch(
            "solver.bem_solver.selected_device_metadata",
            return_value={
                "selected": "opencl",
                "selected_mode": "opencl_cpu",
                "fallback_reason": None,
            },
        ), patch(
            "solver.bem_solver.solve_optimized",
            return_value={"status": "ok"},
        ) as solve_mock, patch("solver.bem_solver.logger.info") as logger_info:
            solver = BEMSolver()
            results = solver.solve(
                mesh={"grid": object()},
                frequency_range=[200.0, 400.0],
                num_frequencies=2,
                sim_type="2",
                advanced_settings={
                    "use_burton_miller": False,
                    "enable_warmup": False,
                    "bem_precision": "double",
                },
            )

        self.assertEqual(results, {"status": "ok"})
        solve_mock.assert_called_once()
        self.assertEqual(solve_mock.call_args.kwargs["use_burton_miller"], False)
        self.assertNotIn("enable_warmup", solve_mock.call_args.kwargs)
        self.assertNotIn("bem_precision", solve_mock.call_args.kwargs)
        self.assertTrue(
            any(
                len(call.args) >= 2
                and "Ignoring compatibility advanced_settings override(s): %s." in str(call.args[0])
                and str(call.args[1]) == "bem_precision, enable_warmup"
                for call in logger_info.call_args_list
                if call.args
            )
        )

    def test_legacy_device_mode_is_accepted_but_ignored(self):
        with patch("solver.bem_solver.BEMPP_AVAILABLE", True), patch(
            "solver.bem_solver.selected_device_metadata",
            return_value={
                "selected": "opencl",
                "selected_mode": "opencl_cpu",
                "fallback_reason": None,
            },
        ) as device_metadata_mock, patch(
            "solver.bem_solver.solve_optimized",
            return_value={"status": "ok"},
        ) as solve_mock, patch("solver.bem_solver.logger.info") as logger_info:
            solver = BEMSolver()
            results = solver.solve(
                mesh={"grid": object()},
                frequency_range=[200.0, 400.0],
                num_frequencies=2,
                sim_type="2",
                device_mode="opencl_gpu",
            )

        self.assertEqual(results, {"status": "ok"})
        device_metadata_mock.assert_called_once_with("auto")
        solve_mock.assert_called_once()
        self.assertEqual(solve_mock.call_args.kwargs["device_mode"], "auto")
        self.assertTrue(
            any(
                len(call.args) >= 2
                and "Ignoring compatibility device_mode=%s" in str(call.args[0])
                and str(call.args[1]) == "opencl_gpu"
                for call in logger_info.call_args_list
                if call.args
            )
        )


if __name__ == "__main__":
    unittest.main()
