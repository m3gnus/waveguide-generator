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


def _dependency_status(
    *,
    gmsh_ready=True,
    gmsh_available=True,
    gmsh_supported=True,
    gmsh_version="4.15.0",
    mesher_ready=True,
    mesher_available=True,
    mesher_supported=True,
    mesher_version="0.1.0",
    metal_bem_ready=True,
    metal_bem_available=True,
    metal_bem_supported=True,
    metal_bem_version="0.2.0",
    bempp_bem_ready=True,
    bempp_bem_available=True,
    bempp_bem_supported=True,
    bempp_bem_version="0.1.0",
):
    return {
        "runtime": {
            "python": {"version": "3.13.1", "supported": True},
            "gmsh_python": {
                "available": gmsh_available,
                "version": gmsh_version,
                "supported": gmsh_supported,
                "ready": gmsh_ready,
            },
            "hornlab_waveguide_mesher": {
                "available": mesher_available,
                "version": mesher_version,
                "supported": mesher_supported,
                "ready": mesher_ready,
            },
            "hornlab_metal_bem": {
                "available": metal_bem_available,
                "version": metal_bem_version,
                "supported": metal_bem_supported,
                "ready": metal_bem_ready,
            },
            "hornlab_bempp_bem": {
                "available": bempp_bem_available,
                "version": bempp_bem_version,
                "supported": bempp_bem_supported,
                "ready": bempp_bem_ready,
            },
        },
        "supportedMatrix": {},
    }


def _metal_backend(
    *,
    available=True,
    helper_build="release",
    helper_available=True,
    reason=None,
):
    return {
        "available": available,
        "supportedPlatform": available,
        "nativeHelperAvailable": helper_available,
        "nativeHelperBuild": helper_build,
        "nativeHelperPath": (
            f"/pkg/.build/{helper_build}/HornlabMetalBemNative" if helper_available else None
        ),
        "reason": reason,
    }


def _bempp_backend(*, available=True, assembly_backend="numba", reason=None):
    return {
        "available": available,
        "packageInstalled": available,
        "openclAvailable": assembly_backend == "opencl",
        "assemblyBackend": assembly_backend,
        "reason": reason or (
            "hornlab-bempp-bem is installed."
            if available
            else "hornlab-bempp-bem is not installed."
        ),
    }


def _opencl_runtime(*, available=False):
    return {
        "available": available,
        "reason": "OpenCL runtime detected." if available else "pyopencl is unavailable",
    }


def _patch_runtime(
    *,
    dependency_status,
    metal_backend,
    bempp_backend=None,
    opencl_runtime=None,
    fastapi_runtime=None,
    matplotlib_runtime=None,
    system="Linux",
    machine="x86_64",
):
    bempp_backend = bempp_backend or _bempp_backend()
    opencl_runtime = opencl_runtime or _opencl_runtime()
    fastapi_runtime = fastapi_runtime or {"available": True, "version": "0.110.0"}
    matplotlib_runtime = matplotlib_runtime or {"available": True, "version": "3.9.2"}
    return (
        patch("services.runtime_preflight.get_dependency_status", return_value=dependency_status),
        patch("services.runtime_preflight.metal_backend_status", return_value=metal_backend),
        patch("services.runtime_preflight.bempp_backend_status", return_value=bempp_backend),
        patch("services.runtime_preflight.opencl_runtime_status", return_value=opencl_runtime),
        patch("services.runtime_preflight.read_fastapi_runtime", return_value=fastapi_runtime),
        patch("services.runtime_preflight.read_matplotlib_runtime", return_value=matplotlib_runtime),
        patch("services.runtime_preflight.platform.system", return_value=system),
        patch("services.runtime_preflight.platform.machine", return_value=machine),
    )


class RuntimePreflightTest(unittest.TestCase):
    def _collect_preflight(self, **kwargs):
        patches = _patch_runtime(**kwargs)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            return collect_runtime_preflight()

    def _collect_doctor(self, **kwargs):
        patches = _patch_runtime(**kwargs)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            return collect_runtime_doctor_report()

    def test_collect_runtime_preflight_marks_required_checks_ready(self):
        report = self._collect_preflight(
            dependency_status=_dependency_status(),
            metal_backend=_metal_backend(),
            system="Linux",
            machine="x86_64",
        )

        self.assertTrue(report["allRequiredReady"])
        self.assertEqual(
            set(report["requiredChecks"].keys()),
            {"fastapi", "gmsh_python", "hornlab_waveguide_mesher", "hornlab_bempp_bem"},
        )
        self.assertTrue(report["requiredChecks"]["fastapi"]["ok"])
        self.assertTrue(report["requiredChecks"]["gmsh_python"]["ok"])
        self.assertTrue(report["requiredChecks"]["hornlab_waveguide_mesher"]["ok"])
        self.assertTrue(report["requiredChecks"]["hornlab_bempp_bem"]["ok"])
        self.assertEqual(
            report["requiredChecks"]["hornlab_bempp_bem"]["requiredFor"],
            "/api/solve on non-Apple-Silicon hosts",
        )
        self.assertIn("version=0.1.0", report["requiredChecks"]["hornlab_bempp_bem"]["detail"])
        self.assertIn(
            "assembly_backend=numba", report["requiredChecks"]["hornlab_bempp_bem"]["detail"]
        )

        ok, failing = evaluate_required_checks(report)
        self.assertTrue(ok)
        self.assertEqual(failing, [])

    def test_collect_runtime_preflight_reports_missing_required_checks(self):
        report = self._collect_preflight(
            dependency_status=_dependency_status(
                gmsh_ready=False,
                gmsh_available=False,
                gmsh_supported=False,
                gmsh_version=None,
                mesher_ready=False,
                mesher_available=False,
                mesher_supported=False,
                mesher_version=None,
                metal_bem_ready=False,
                metal_bem_available=False,
                metal_bem_supported=False,
                metal_bem_version=None,
                bempp_bem_ready=False,
                bempp_bem_available=False,
                bempp_bem_supported=False,
                bempp_bem_version=None,
            ),
            metal_backend=_metal_backend(
                available=False,
                helper_available=False,
                helper_build=None,
                reason="hornlab-metal-bem is not installed.",
            ),
            bempp_backend=_bempp_backend(
                available=False,
                reason="hornlab-bempp-bem is not installed.",
            ),
            fastapi_runtime={"available": False, "version": None},
            system="Linux",
            machine="x86_64",
        )

        ok, failing = evaluate_required_checks(report)
        self.assertFalse(ok)
        self.assertEqual(len(failing), 4)
        self.assertFalse(report["allRequiredReady"])
        self.assertFalse(report["requiredChecks"]["fastapi"]["ok"])
        self.assertFalse(report["requiredChecks"]["gmsh_python"]["ok"])
        self.assertFalse(report["requiredChecks"]["hornlab_waveguide_mesher"]["ok"])
        self.assertFalse(report["requiredChecks"]["hornlab_bempp_bem"]["ok"])
        self.assertEqual(
            report["requiredChecks"]["hornlab_bempp_bem"]["detail"],
            "hornlab-bempp-bem is not installed.",
        )

        text_summary = render_runtime_preflight_text(report)
        self.assertIn("Overall required runtime status: NOT READY", text_summary)
        self.assertIn("- fastapi: MISSING", text_summary)
        self.assertIn("- hornlab_bempp_bem: MISSING", text_summary)

    def test_collect_runtime_preflight_requires_hornlab_mesher_package(self):
        report = self._collect_preflight(
            dependency_status=_dependency_status(
                mesher_ready=False,
                mesher_available=False,
                mesher_supported=False,
                mesher_version=None,
            ),
            metal_backend=_metal_backend(),
            system="Linux",
            machine="x86_64",
        )

        ok, failing = evaluate_required_checks(report)
        self.assertFalse(ok)
        self.assertFalse(report["allRequiredReady"])
        self.assertFalse(report["requiredChecks"]["hornlab_waveguide_mesher"]["ok"])
        self.assertTrue(
            any("hornlab_waveguide_mesher" in item for item in failing),
            failing,
        )

    def test_collect_runtime_preflight_requires_release_metal_helper_on_apple_silicon(self):
        report = self._collect_preflight(
            dependency_status=_dependency_status(),
            metal_backend=_metal_backend(helper_build="debug"),
            system="Darwin",
            machine="arm64",
        )

        ok, failing = evaluate_required_checks(report)
        self.assertFalse(ok)
        self.assertFalse(report["allRequiredReady"])
        self.assertIn("metal_release_helper", report["requiredChecks"])
        self.assertFalse(report["requiredChecks"]["metal_release_helper"]["ok"])
        self.assertTrue(report["requiredChecks"]["hornlab_metal_bem"]["ok"])
        self.assertIn("build=debug", report["requiredChecks"]["metal_release_helper"]["detail"])
        self.assertTrue(any("metal_release_helper" in item for item in failing), failing)

    def test_collect_runtime_preflight_accepts_release_metal_helper_on_apple_silicon(self):
        report = self._collect_preflight(
            dependency_status=_dependency_status(),
            metal_backend=_metal_backend(helper_build="release"),
            system="Darwin",
            machine="arm64",
        )

        ok, failing = evaluate_required_checks(report)
        self.assertTrue(ok)
        self.assertEqual(failing, [])
        self.assertTrue(report["allRequiredReady"])
        self.assertTrue(report["requiredChecks"]["metal_release_helper"]["ok"])
        self.assertEqual(
            report["requiredChecks"]["metal_release_helper"]["requiredFor"], "/api/solve"
        )
        self.assertIn("build=release", report["requiredChecks"]["metal_release_helper"]["detail"])

        text_summary = render_runtime_preflight_text(report)
        self.assertIn("Overall required runtime status: READY", text_summary)
        self.assertIn("- metal_release_helper: OK", text_summary)

    def test_metal_release_helper_check_is_absent_off_apple_silicon(self):
        for system, machine in (("Linux", "x86_64"), ("Darwin", "x86_64"), ("Windows", "AMD64")):
            with self.subTest(system=system, machine=machine):
                report = self._collect_preflight(
                    dependency_status=_dependency_status(),
                    # Even a debug helper must not fail preflight off Apple Silicon.
                    metal_backend=_metal_backend(helper_build="debug"),
                    system=system,
                    machine=machine,
                )
                self.assertNotIn("metal_release_helper", report["requiredChecks"])
                self.assertTrue(report["allRequiredReady"])

    def test_collect_runtime_doctor_report_classifies_required_and_optional_components(self):
        report = self._collect_doctor(
            dependency_status=_dependency_status(),
            metal_backend=_metal_backend(),
            system="Linux",
            machine="x86_64",
        )

        self.assertEqual(report["schemaVersion"], 1)
        self.assertTrue(report["summary"]["requiredReady"])
        self.assertTrue(report["summary"]["solveReady"])
        self.assertTrue(report["summary"]["meshBuildReady"])
        self.assertEqual(report["summary"]["counts"]["installed"], 6)
        self.assertEqual(report["summary"]["counts"]["missing"], 1)
        self.assertEqual(report["summary"]["counts"]["optional"], 3)

        components_by_id = {item["id"]: item for item in report["components"]}
        self.assertEqual(
            set(components_by_id.keys()),
            {
                "fastapi",
                "gmsh_python",
                "hornlab_waveguide_mesher",
                "hornlab_metal_bem",
                "hornlab_bempp_bem",
                "opencl_runtime",
                "matplotlib",
            },
        )
        self.assertEqual(components_by_id["fastapi"]["status"], "installed")
        self.assertEqual(components_by_id["gmsh_python"]["status"], "installed")
        self.assertEqual(components_by_id["hornlab_waveguide_mesher"]["status"], "installed")
        self.assertEqual(components_by_id["hornlab_metal_bem"]["status"], "installed")
        self.assertEqual(components_by_id["hornlab_metal_bem"]["category"], "optional")
        self.assertEqual(components_by_id["hornlab_bempp_bem"]["status"], "installed")
        self.assertEqual(components_by_id["hornlab_bempp_bem"]["category"], "required")
        self.assertEqual(
            components_by_id["hornlab_bempp_bem"]["requiredFor"],
            "/api/solve on non-Apple-Silicon hosts",
        )
        self.assertEqual(components_by_id["opencl_runtime"]["category"], "optional")
        self.assertEqual(components_by_id["opencl_runtime"]["status"], "missing")
        self.assertEqual(components_by_id["matplotlib"]["category"], "optional")
        self.assertEqual(components_by_id["matplotlib"]["status"], "installed")

        ok, failing = evaluate_runtime_doctor(report)
        self.assertTrue(ok)
        self.assertEqual(failing, [])

    def test_collect_runtime_doctor_report_surfaces_guidance_for_missing_and_unsupported(self):
        report = self._collect_doctor(
            dependency_status=_dependency_status(
                gmsh_ready=False,
                gmsh_available=True,
                gmsh_supported=False,
                gmsh_version="5.1.0",
                mesher_ready=False,
                mesher_available=False,
                mesher_supported=False,
                mesher_version=None,
                metal_bem_ready=False,
                metal_bem_available=False,
                metal_bem_supported=False,
                metal_bem_version=None,
                bempp_bem_ready=False,
                bempp_bem_available=False,
                bempp_bem_supported=False,
                bempp_bem_version=None,
            ),
            metal_backend=_metal_backend(
                available=False,
                helper_available=False,
                helper_build=None,
                reason="hornlab-metal-bem is not installed.",
            ),
            bempp_backend=_bempp_backend(
                available=False,
                reason="hornlab-bempp-bem is not installed.",
            ),
            fastapi_runtime={"available": False, "version": None},
            matplotlib_runtime={"available": False, "version": None},
            system="Linux",
            machine="x86_64",
        )

        ok, failing = evaluate_runtime_doctor(report)
        self.assertFalse(ok)
        self.assertEqual(len(failing), 4)
        self.assertFalse(report["summary"]["requiredReady"])
        self.assertFalse(report["summary"]["solveReady"])
        self.assertIn("fastapi", report["summary"]["requiredIssues"])
        self.assertIn("gmsh_python", report["summary"]["requiredIssues"])
        self.assertIn("hornlab_waveguide_mesher", report["summary"]["requiredIssues"])
        self.assertIn("hornlab_bempp_bem", report["summary"]["requiredIssues"])
        self.assertEqual(report["summary"]["solveIssues"], ["hornlab_bempp_bem"])
        self.assertIn("gmsh_python", report["summary"]["meshBuildIssues"])
        self.assertIn("hornlab_waveguide_mesher", report["summary"]["meshBuildIssues"])
        self.assertEqual(
            report["summary"]["optionalIssues"],
            ["hornlab_metal_bem", "opencl_runtime", "matplotlib"],
        )

        components_by_id = {item["id"]: item for item in report["components"]}
        self.assertEqual(components_by_id["fastapi"]["status"], "missing")
        self.assertEqual(components_by_id["gmsh_python"]["status"], "unsupported")
        self.assertEqual(components_by_id["hornlab_waveguide_mesher"]["status"], "missing")
        self.assertEqual(components_by_id["hornlab_metal_bem"]["status"], "missing")
        self.assertEqual(components_by_id["hornlab_bempp_bem"]["status"], "missing")
        self.assertEqual(components_by_id["opencl_runtime"]["status"], "missing")
        self.assertEqual(components_by_id["matplotlib"]["status"], "missing")

        self.assertTrue(
            any(
                "pip install -r server/requirements-bempp.txt" in line
                for line in components_by_id["hornlab_bempp_bem"]["guidance"]
            ),
            components_by_id["hornlab_bempp_bem"]["guidance"],
        )
        self.assertTrue(
            any(
                ">=4.11.1,<5.0" in line
                for line in components_by_id["gmsh_python"]["guidance"]
            ),
            components_by_id["gmsh_python"]["guidance"],
        )
        # Headless-Linux fallback guidance is included when system is Linux.
        self.assertTrue(
            any(
                "python-packages-dev-nox" in line
                for line in components_by_id["gmsh_python"]["guidance"]
            ),
            components_by_id["gmsh_python"]["guidance"],
        )
        self.assertTrue(
            any(
                "pip install -r server/requirements.txt" in line
                for line in components_by_id["hornlab_waveguide_mesher"]["guidance"]
            ),
            components_by_id["hornlab_waveguide_mesher"]["guidance"],
        )
        self.assertGreater(len(components_by_id["matplotlib"]["guidance"]), 0)

        text_summary = render_runtime_doctor_text(report)
        self.assertIn("Waveguide backend dependency doctor", text_summary)
        self.assertIn("Required dependency status: NOT READY", text_summary)
        self.assertIn(
            "- hornlab_bempp_bem: MISSING [required] (requiredFor=/api/solve on non-Apple-Silicon hosts)",
            text_summary,
        )
        self.assertIn("guidance: Install BEMPP fallback requirements: pip install -r server/requirements-bempp.txt", text_summary)

    def test_collect_runtime_doctor_requires_release_metal_helper_on_apple_silicon(self):
        report = self._collect_doctor(
            dependency_status=_dependency_status(),
            metal_backend=_metal_backend(helper_build="debug"),
            system="Darwin",
            machine="arm64",
        )

        ok, failing = evaluate_runtime_doctor(report)
        self.assertFalse(ok)
        self.assertIn("metal_release_helper", report["summary"]["requiredIssues"])
        self.assertTrue(report["summary"]["solveReady"])
        self.assertEqual(report["summary"]["solveIssues"], [])
        self.assertTrue(any("metal_release_helper" in item for item in failing), failing)

        component = {item["id"]: item for item in report["components"]}["metal_release_helper"]
        self.assertEqual(component["category"], "required")
        self.assertEqual(component["requiredFor"], "/api/solve")
        self.assertEqual(component["status"], "missing")
        self.assertTrue(any("build:metal-helper" in line for line in component["guidance"]))

    def test_collect_runtime_doctor_accepts_release_metal_helper_on_apple_silicon(self):
        report = self._collect_doctor(
            dependency_status=_dependency_status(),
            metal_backend=_metal_backend(helper_build="release"),
            system="Darwin",
            machine="arm64",
        )

        ok, failing = evaluate_runtime_doctor(report)
        self.assertTrue(ok)
        self.assertEqual(failing, [])
        self.assertTrue(report["summary"]["requiredReady"])
        self.assertTrue(report["summary"]["solveReady"])

        component = {item["id"]: item for item in report["components"]}["metal_release_helper"]
        self.assertEqual(component["status"], "installed")
        self.assertEqual(component["guidance"], [])

    def test_collect_runtime_doctor_omits_metal_release_helper_off_apple_silicon(self):
        report = self._collect_doctor(
            dependency_status=_dependency_status(),
            metal_backend=_metal_backend(helper_build="debug"),
            system="Linux",
            machine="x86_64",
        )

        component_ids = {item["id"] for item in report["components"]}
        self.assertNotIn("metal_release_helper", component_ids)
        self.assertNotIn("metal_release_helper", report["summary"]["requiredIssues"])


if __name__ == "__main__":
    unittest.main()
