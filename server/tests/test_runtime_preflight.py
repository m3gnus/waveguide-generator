import unittest
from unittest.mock import patch

from services.runtime_preflight import (
    collect_runtime_preflight,
    evaluate_required_checks,
    render_runtime_preflight_text,
)


class RuntimePreflightTest(unittest.TestCase):
    def test_collect_runtime_preflight_marks_required_checks_ready(self):
        dependency_status = {
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "4.15.0", "supported": True, "ready": True},
                "bempp": {"available": True, "variant": "bempp_cl", "version": "0.4.2", "supported": True, "ready": True},
            },
            "supportedMatrix": {},
        }
        device_metadata = {
            "opencl_available": True,
            "selected_mode": "opencl_gpu",
            "device_name": "GPU-1",
            "fallback_reason": None,
            "warning": None,
        }

        with patch("services.runtime_preflight.get_dependency_status", return_value=dependency_status), patch(
            "services.runtime_preflight.read_fastapi_runtime",
            return_value={"available": True, "version": "0.110.0"},
        ), patch(
            "services.runtime_preflight.read_opencl_device_metadata",
            return_value=device_metadata,
        ):
            report = collect_runtime_preflight()

        self.assertTrue(report["allRequiredReady"])
        self.assertTrue(report["requiredChecks"]["fastapi"]["ok"])
        self.assertTrue(report["requiredChecks"]["gmsh_python"]["ok"])
        self.assertTrue(report["requiredChecks"]["bempp_cl"]["ok"])
        self.assertTrue(report["requiredChecks"]["opencl_runtime"]["ok"])
        self.assertIn("selected_mode=opencl_gpu", report["requiredChecks"]["opencl_runtime"]["detail"])

    def test_collect_runtime_preflight_reports_missing_required_checks(self):
        dependency_status = {
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": False, "version": None, "supported": False, "ready": False},
                "bempp": {"available": False, "variant": None, "version": None, "supported": False, "ready": False},
            },
            "supportedMatrix": {},
        }
        device_metadata = {
            "opencl_available": False,
            "fallback_reason": "no OpenCL platforms found.",
            "warning": "no OpenCL platforms found.",
        }

        with patch("services.runtime_preflight.get_dependency_status", return_value=dependency_status), patch(
            "services.runtime_preflight.read_fastapi_runtime",
            return_value={"available": False, "version": None},
        ), patch(
            "services.runtime_preflight.read_opencl_device_metadata",
            return_value=device_metadata,
        ):
            report = collect_runtime_preflight()

        ok, failing = evaluate_required_checks(report)
        self.assertFalse(ok)
        self.assertEqual(len(failing), 4)
        self.assertFalse(report["allRequiredReady"])
        self.assertIsInstance(report["requiredChecks"]["opencl_runtime"]["detail"], str)
        self.assertEqual(
            report["requiredChecks"]["opencl_runtime"]["detail"],
            "no OpenCL platforms found.",
        )

        text_summary = render_runtime_preflight_text(report)
        self.assertIn("Overall required runtime status: NOT READY", text_summary)
        self.assertIn("- fastapi: MISSING", text_summary)


if __name__ == "__main__":
    unittest.main()
