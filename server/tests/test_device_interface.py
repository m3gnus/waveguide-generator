import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from solver import device_interface as di


class DeviceInterfaceSelectionTest(unittest.TestCase):
    def tearDown(self):
        di.clear_device_selection_caches()

    def test_falls_back_to_numba_when_requested_mode_unavailable(self):
        di.clear_device_selection_caches()
        with patch("solver.device_interface._available_concrete_modes", return_value=["numba"]), patch(
            "solver.device_interface._mode_unavailable_reason", return_value="no cpu driver"
        ), patch(
            "solver.device_interface._mode_availability",
            return_value={
                "auto": {"available": True, "reason": None, "priority": ["opencl_gpu", "opencl_cpu", "numba"]},
                "opencl_cpu": {"available": False, "reason": "no cpu driver"},
                "opencl_gpu": {"available": False, "reason": "no gpu driver"},
                "numba": {"available": True, "reason": None},
            },
        ):
            self.assertEqual(di.selected_device_interface("opencl_cpu"), "numba")

    def test_reports_selected_opencl_when_available(self):
        di.clear_device_selection_caches()
        with patch("solver.device_interface._available_concrete_modes", return_value=["opencl_cpu", "numba"]), patch(
            "solver.device_interface._mode_availability",
            return_value={
                "auto": {"available": True, "reason": None, "priority": ["opencl_gpu", "opencl_cpu", "numba"]},
                "opencl_cpu": {"available": True, "reason": None},
                "opencl_gpu": {"available": False, "reason": "no gpu driver"},
                "numba": {"available": True, "reason": None},
            },
        ), patch(
            "solver.device_interface._ensure_selected_mode_applied", return_value=("opencl", "cpu", "Fake CPU")
        ):
            self.assertEqual(di.selected_device_interface("opencl_cpu"), "opencl")

    def test_auto_prefers_opencl_gpu_then_cpu_then_numba(self):
        di.clear_device_selection_caches()
        with patch("solver.device_interface._available_concrete_modes", return_value=["opencl_cpu", "opencl_gpu", "numba"]), patch(
            "solver.device_interface._mode_availability",
            return_value={
                "auto": {"available": True, "reason": None, "priority": ["opencl_gpu", "opencl_cpu", "numba"]},
                "opencl_cpu": {"available": True, "reason": None},
                "opencl_gpu": {"available": True, "reason": None},
                "numba": {"available": True, "reason": None},
            },
        ):
            profile = di._selected_device_profile("auto")
        self.assertEqual(profile["selected_mode"], "opencl_gpu")

        di.clear_device_selection_caches()
        with patch("solver.device_interface._available_concrete_modes", return_value=["opencl_cpu", "numba"]), patch(
            "solver.device_interface._mode_availability",
            return_value={
                "auto": {"available": True, "reason": None, "priority": ["opencl_gpu", "opencl_cpu", "numba"]},
                "opencl_cpu": {"available": True, "reason": None},
                "opencl_gpu": {"available": False, "reason": "no gpu driver"},
                "numba": {"available": True, "reason": None},
            },
        ):
            profile = di._selected_device_profile("auto")
        self.assertEqual(profile["selected_mode"], "opencl_cpu")

        di.clear_device_selection_caches()
        with patch("solver.device_interface._available_concrete_modes", return_value=["numba"]), patch(
            "solver.device_interface._mode_availability",
            return_value={
                "auto": {"available": True, "reason": None, "priority": ["opencl_gpu", "opencl_cpu", "numba"]},
                "opencl_cpu": {"available": False, "reason": "no cpu driver"},
                "opencl_gpu": {"available": False, "reason": "no gpu driver"},
                "numba": {"available": True, "reason": None},
            },
        ):
            profile = di._selected_device_profile("auto")
        self.assertEqual(profile["selected_mode"], "numba")

    def test_metadata_contains_runtime_retry_defaults(self):
        di.clear_device_selection_caches()
        mocked_profile = {
            "requested_mode": "auto",
            "selected_mode": "opencl_cpu",
            "selected_interface": "opencl",
            "selected_device_type": "cpu",
            "fallback_reason": None,
            "concrete_modes": ["opencl_cpu", "numba"],
            "available_modes": ["auto", "opencl_cpu", "numba"],
            "mode_availability": {
                "auto": {"available": True, "reason": None, "priority": ["opencl_gpu", "opencl_cpu", "numba"]},
                "opencl_cpu": {"available": True, "reason": None},
                "opencl_gpu": {"available": False, "reason": "no gpu driver"},
                "numba": {"available": True, "reason": None},
            },
            "opencl_diagnostics": {"base_ready": True, "gpu_available": False, "gpu_reason": "no gpu driver"},
            "benchmark": {"ran": False, "winner_mode": None, "samples": {}, "policy": "deterministic_priority"},
        }
        with patch("solver.device_interface._selected_device_profile", return_value=mocked_profile), patch(
            "solver.device_interface._ensure_selected_mode_applied", return_value=("opencl", "cpu", "Fake CPU")
        ):
            info = di.selected_device_metadata("auto")
        self.assertEqual(info["requested_mode"], "auto")
        self.assertEqual(info["selected_mode"], "opencl_cpu")
        self.assertEqual(info["requested"], "auto")
        self.assertEqual(info["selected"], "opencl")
        self.assertEqual(info["runtime_retry_attempted"], False)
        self.assertEqual(info["runtime_retry_outcome"], "not_needed")
        self.assertEqual(info["runtime_profile"], "default")
        self.assertIn("mode_availability", info)
        self.assertIn("opencl_diagnostics", info)

    def test_configure_opencl_safe_profile_returns_unavailable_when_runtime_missing(self):
        with patch("solver.device_interface.bempp_api", None):
            profile = di.configure_opencl_safe_profile()
        self.assertFalse(profile["applied"])
        self.assertIn("unavailable", str(profile["detail"]).lower())

    def test_configure_opencl_safe_profile_applies_cpu_settings(self):
        fake_bempp_api = SimpleNamespace(
            BOUNDARY_OPERATOR_DEVICE_TYPE="gpu",
            POTENTIAL_OPERATOR_DEVICE_TYPE="gpu",
            GLOBAL_PARAMETERS=SimpleNamespace(
                assembly=SimpleNamespace(dense=SimpleNamespace(workgroup_size_multiple=8))
            ),
        )
        call_flags = {"cpu_device_called": False, "cpu_context_called": False}

        def _default_cpu_device():
            call_flags["cpu_device_called"] = True
            return SimpleNamespace(name="Fake CPU")

        def _default_cpu_context():
            call_flags["cpu_context_called"] = True
            return object()

        bempp_module = ModuleType("bempp_cl")
        core_module = ModuleType("bempp_cl.core")
        opencl_module = ModuleType("bempp_cl.core.opencl_kernels")
        opencl_module.default_cpu_device = _default_cpu_device
        opencl_module.default_cpu_context = _default_cpu_context

        with patch("solver.device_interface.bempp_api", fake_bempp_api), patch(
            "solver.device_interface.BEMPP_VARIANT", "bempp_cl"
        ), patch.dict(
            "sys.modules",
            {
                "bempp_cl": bempp_module,
                "bempp_cl.core": core_module,
                "bempp_cl.core.opencl_kernels": opencl_module,
            },
        ):
            profile = di.configure_opencl_safe_profile()

        self.assertTrue(profile["applied"])
        self.assertEqual(profile["profile"], "safe_cpu")
        self.assertEqual(profile["device_name"], "Fake CPU")
        self.assertEqual(profile["workgroup_size_multiple"], 1)
        self.assertTrue(call_flags["cpu_device_called"])
        self.assertTrue(call_flags["cpu_context_called"])
        self.assertEqual(fake_bempp_api.BOUNDARY_OPERATOR_DEVICE_TYPE, "cpu")
        self.assertEqual(fake_bempp_api.POTENTIAL_OPERATOR_DEVICE_TYPE, "cpu")


if __name__ == "__main__":
    unittest.main()
