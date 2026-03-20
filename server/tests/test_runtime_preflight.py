import unittest
from unittest.mock import patch

from services.runtime_preflight import (
    collect_runtime_doctor_report,
    collect_runtime_preflight,
    evaluate_required_checks,
    evaluate_runtime_doctor,
    render_runtime_doctor_text,
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
            "selected_mode": "opencl_cpu",
            "device_name": "CPU-1",
            "supported_modes": ["opencl_cpu", "opencl_gpu"],
            "selection_policy": "supported_opencl_modes",
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
        self.assertIn("selected_mode=opencl_cpu", report["requiredChecks"]["opencl_runtime"]["detail"])
        self.assertIn("supported_modes=opencl_cpu,opencl_gpu", report["requiredChecks"]["opencl_runtime"]["detail"])
        self.assertIn("policy=supported_opencl_modes", report["requiredChecks"]["opencl_runtime"]["detail"])

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

    def test_collect_runtime_doctor_report_classifies_required_and_optional_components(self):
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
            "selected_mode": "opencl_cpu",
            "device_name": "CPU-1",
            "supported_modes": ["opencl_cpu", "opencl_gpu"],
            "selection_policy": "supported_opencl_modes",
            "fallback_reason": None,
            "warning": None,
        }

        with patch("services.runtime_preflight.get_dependency_status", return_value=dependency_status), patch(
            "services.runtime_preflight.read_fastapi_runtime",
            return_value={"available": True, "version": "0.110.0"},
        ), patch(
            "services.runtime_preflight.read_matplotlib_runtime",
            return_value={"available": True, "version": "3.9.2"},
        ), patch(
            "services.runtime_preflight.read_opencl_device_metadata",
            return_value=device_metadata,
        ), patch("services.runtime_preflight.platform.system", return_value="Linux"):
            report = collect_runtime_doctor_report()

        self.assertEqual(report["schemaVersion"], 1)
        self.assertTrue(report["summary"]["requiredReady"])
        self.assertEqual(report["summary"]["counts"]["installed"], 5)
        self.assertEqual(report["summary"]["counts"]["optional"], 1)

        components_by_id = {item["id"]: item for item in report["components"]}
        self.assertEqual(components_by_id["fastapi"]["status"], "installed")
        self.assertEqual(components_by_id["gmsh_python"]["status"], "installed")
        self.assertEqual(components_by_id["bempp_cl"]["status"], "installed")
        self.assertEqual(components_by_id["opencl_runtime"]["status"], "installed")
        self.assertEqual(components_by_id["matplotlib"]["category"], "optional")
        self.assertEqual(components_by_id["matplotlib"]["status"], "installed")

    def test_collect_runtime_doctor_report_surfaces_guidance_for_missing_and_unsupported(self):
        dependency_status = {
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "5.1.0", "supported": False, "ready": False},
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
            "services.runtime_preflight.read_matplotlib_runtime",
            return_value={"available": False, "version": None},
        ), patch(
            "services.runtime_preflight.read_opencl_device_metadata",
            return_value=device_metadata,
        ), patch("services.runtime_preflight.platform.system", return_value="Linux"):
            report = collect_runtime_doctor_report()

        ok, failing = evaluate_runtime_doctor(report)
        self.assertFalse(ok)
        self.assertEqual(len(failing), 4)
        self.assertFalse(report["summary"]["requiredReady"])
        self.assertIn("fastapi", report["summary"]["requiredIssues"])
        self.assertIn("gmsh_python", report["summary"]["requiredIssues"])
        self.assertIn("bempp_cl", report["summary"]["requiredIssues"])
        self.assertIn("opencl_runtime", report["summary"]["requiredIssues"])
        self.assertIn("matplotlib", report["summary"]["optionalIssues"])

        components_by_id = {item["id"]: item for item in report["components"]}
        self.assertEqual(components_by_id["gmsh_python"]["status"], "unsupported")
        self.assertEqual(components_by_id["bempp_cl"]["status"], "missing")
        self.assertEqual(components_by_id["opencl_runtime"]["status"], "missing")
        self.assertEqual(components_by_id["matplotlib"]["status"], "missing")
        self.assertGreater(len(components_by_id["opencl_runtime"]["guidance"]), 0)
        self.assertTrue(
            any("pocl" in line.lower() for line in components_by_id["opencl_runtime"]["guidance"])
        )

        text_summary = render_runtime_doctor_text(report)
        self.assertIn("Waveguide backend dependency doctor", text_summary)
        self.assertIn("Required dependency status: NOT READY", text_summary)

    def test_collect_runtime_doctor_report_marks_apple_silicon_opencl_unsupported(self):
        dependency_status = {
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "4.15.0", "supported": True, "ready": True},
                "bempp": {"available": True, "variant": "bempp_cl", "version": "0.4.2", "supported": True, "ready": True},
            },
            "supportedMatrix": {},
        }
        device_metadata = {
            "opencl_available": False,
            "selected_mode": None,
            "device_name": None,
            "supported_modes": [],
            "selection_policy": "supported_opencl_modes",
            "fallback_reason": (
                "Apple Silicon OpenCL solve is currently unsupported for /api/solve: "
                "the maintained bounded Tritonia repro still fails on the pocl CPU runtime."
            ),
            "warning": None,
        }

        with patch("services.runtime_preflight.get_dependency_status", return_value=dependency_status), patch(
            "services.runtime_preflight.read_fastapi_runtime",
            return_value={"available": True, "version": "0.110.0"},
        ), patch(
            "services.runtime_preflight.read_matplotlib_runtime",
            return_value={"available": True, "version": "3.9.2"},
        ), patch(
            "services.runtime_preflight.read_opencl_device_metadata",
            return_value=device_metadata,
        ), patch("services.runtime_preflight.platform.system", return_value="Darwin"), patch(
            "services.runtime_preflight.platform.machine", return_value="arm64"
        ):
            report = collect_runtime_doctor_report()

        components_by_id = {item["id"]: item for item in report["components"]}
        self.assertEqual(components_by_id["opencl_runtime"]["status"], "missing")
        self.assertIn("Apple Silicon", components_by_id["opencl_runtime"]["detail"])
        self.assertTrue(
            any("unsupported" in line.lower() for line in components_by_id["opencl_runtime"]["guidance"])
        )


if __name__ == "__main__":
    unittest.main()
