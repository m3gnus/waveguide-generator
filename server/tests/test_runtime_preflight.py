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
        ), patch(
            "services.runtime_preflight.read_bounded_solve_readiness",
            return_value={
                "ready": True,
                "status": "validated",
                "detail": "Bounded solve validation passed.",
            },
        ):
            report = collect_runtime_preflight()

        self.assertTrue(report["allRequiredReady"])
        self.assertTrue(report["requiredChecks"]["fastapi"]["ok"])
        self.assertTrue(report["requiredChecks"]["gmsh_python"]["ok"])
        self.assertTrue(report["requiredChecks"]["bempp_cl"]["ok"])
        self.assertTrue(report["requiredChecks"]["opencl_runtime"]["ok"])
        self.assertTrue(report["requiredChecks"]["bounded_solve_validation"]["ok"])
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
        ), patch(
            "services.runtime_preflight.read_bounded_solve_readiness",
            return_value={
                "ready": False,
                "status": "missing",
                "detail": "No bounded solve validation record found.",
            },
        ):
            report = collect_runtime_preflight()

        ok, failing = evaluate_required_checks(report)
        self.assertFalse(ok)
        self.assertEqual(len(failing), 5)
        self.assertFalse(report["allRequiredReady"])
        self.assertIsInstance(report["requiredChecks"]["opencl_runtime"]["detail"], str)
        self.assertEqual(
            report["requiredChecks"]["bounded_solve_validation"]["detail"],
            "No bounded solve validation record found.",
        )
        self.assertEqual(
            report["requiredChecks"]["opencl_runtime"]["detail"],
            "no OpenCL platforms found.",
        )

        text_summary = render_runtime_preflight_text(report)
        self.assertIn("Overall required runtime status: NOT READY", text_summary)
        self.assertIn("- fastapi: MISSING", text_summary)
        self.assertIn("- bounded_solve_validation: MISSING", text_summary)

    def test_collect_runtime_preflight_requires_bounded_solve_validation_even_when_dependencies_ready(self):
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
        ), patch(
            "services.runtime_preflight.read_bounded_solve_readiness",
            return_value={
                "ready": False,
                "status": "unvalidated",
                "detail": "Bounded solve validation record exists but has no completed solve attempt.",
            },
        ):
            report = collect_runtime_preflight()

        self.assertFalse(report["allRequiredReady"])
        self.assertFalse(report["requiredChecks"]["bounded_solve_validation"]["ok"])
        self.assertTrue(report["requiredChecks"]["bempp_cl"]["ok"])
        self.assertTrue(report["requiredChecks"]["opencl_runtime"]["ok"])

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
        ), patch(
            "services.runtime_preflight.read_bounded_solve_readiness",
            return_value={
                "ready": True,
                "status": "validated",
                "detail": "Bounded solve validation passed.",
            },
        ), patch("services.runtime_preflight.platform.system", return_value="Linux"):
            report = collect_runtime_doctor_report()

        self.assertEqual(report["schemaVersion"], 1)
        self.assertTrue(report["summary"]["requiredReady"])
        self.assertTrue(report["summary"]["solveReady"])
        self.assertEqual(report["summary"]["counts"]["installed"], 6)
        self.assertEqual(report["summary"]["counts"]["optional"], 1)

        components_by_id = {item["id"]: item for item in report["components"]}
        self.assertEqual(components_by_id["fastapi"]["status"], "installed")
        self.assertEqual(components_by_id["gmsh_python"]["status"], "installed")
        self.assertEqual(components_by_id["bempp_cl"]["status"], "installed")
        self.assertEqual(components_by_id["opencl_runtime"]["status"], "installed")
        self.assertEqual(components_by_id["bounded_solve_validation"]["status"], "installed")
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
        ), patch(
            "services.runtime_preflight.read_bounded_solve_readiness",
            return_value={
                "ready": False,
                "status": "failed",
                "detail": "Bounded solve validation failed: OpenCL error",
            },
        ), patch("services.runtime_preflight.platform.system", return_value="Linux"):
            report = collect_runtime_doctor_report()

        ok, failing = evaluate_runtime_doctor(report)
        self.assertFalse(ok)
        self.assertEqual(len(failing), 5)
        self.assertFalse(report["summary"]["requiredReady"])
        self.assertFalse(report["summary"]["solveReady"])
        self.assertIn("fastapi", report["summary"]["requiredIssues"])
        self.assertIn("gmsh_python", report["summary"]["requiredIssues"])
        self.assertIn("bempp_cl", report["summary"]["requiredIssues"])
        self.assertIn("opencl_runtime", report["summary"]["requiredIssues"])
        self.assertIn("bounded_solve_validation", report["summary"]["requiredIssues"])
        self.assertIn("bounded_solve_validation", report["summary"]["solveIssues"])
        self.assertIn("matplotlib", report["summary"]["optionalIssues"])

        components_by_id = {item["id"]: item for item in report["components"]}
        self.assertEqual(components_by_id["gmsh_python"]["status"], "unsupported")
        self.assertEqual(components_by_id["bempp_cl"]["status"], "missing")
        self.assertEqual(components_by_id["opencl_runtime"]["status"], "missing")
        self.assertEqual(components_by_id["bounded_solve_validation"]["status"], "missing")
        self.assertEqual(components_by_id["matplotlib"]["status"], "missing")
        self.assertGreater(len(components_by_id["opencl_runtime"]["guidance"]), 0)
        self.assertTrue(
            any("pocl" in line.lower() for line in components_by_id["opencl_runtime"]["guidance"])
        )

        text_summary = render_runtime_doctor_text(report)
        self.assertIn("Waveguide backend dependency doctor", text_summary)
        self.assertIn("Required dependency status: NOT READY", text_summary)

    def test_collect_runtime_doctor_report_opencl_missing_when_unavailable(self):
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
            "fallback_reason": "No OpenCL runtime available.",
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
        ), patch(
            "services.runtime_preflight.read_bounded_solve_readiness",
            return_value={
                "ready": False,
                "status": "missing",
                "detail": "No bounded solve validation record found.",
            },
        ), patch("services.runtime_preflight.platform.system", return_value="Linux"), patch(
            "services.runtime_preflight.platform.machine", return_value="x86_64"
        ):
            report = collect_runtime_doctor_report()

        components_by_id = {item["id"]: item for item in report["components"]}
        self.assertEqual(components_by_id["opencl_runtime"]["status"], "missing")
        self.assertEqual(components_by_id["bounded_solve_validation"]["status"], "missing")
        self.assertTrue(len(components_by_id["opencl_runtime"]["guidance"]) > 0)


if __name__ == "__main__":
    unittest.main()
