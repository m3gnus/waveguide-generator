import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from solver import device_interface as di


class DeviceInterfaceSelectionTest(unittest.TestCase):
    def tearDown(self):
        di.opencl_unavailable_reason.cache_clear()
        di.selected_device_interface.cache_clear()

    def test_falls_back_to_numba_when_opencl_unavailable(self):
        di.selected_device_interface.cache_clear()
        with patch("solver.device_interface.opencl_unavailable_reason", return_value="no cpu driver"):
            self.assertEqual(di.selected_device_interface("opencl"), "numba")

    def test_reports_selected_opencl_when_available(self):
        di.selected_device_interface.cache_clear()
        with patch("solver.device_interface.opencl_unavailable_reason", return_value=None):
            self.assertEqual(di.selected_device_interface("opencl"), "opencl")

    def test_metadata_contains_runtime_retry_defaults(self):
        di.selected_device_interface.cache_clear()
        with patch("solver.device_interface.opencl_unavailable_reason", return_value=None):
            info = di.selected_device_metadata()
        self.assertEqual(info["requested"], "opencl")
        self.assertEqual(info["selected"], "opencl")
        self.assertEqual(info["runtime_retry_attempted"], False)
        self.assertEqual(info["runtime_retry_outcome"], "not_needed")
        self.assertEqual(info["runtime_profile"], "default")

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

