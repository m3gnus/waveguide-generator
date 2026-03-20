import asyncio
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException

from api.routes_mesh import build_mesh_from_params
from api.routes_misc import health_check
from api.routes_simulation import submit_simulation
from contracts import MeshData, SimulationRequest, WaveguideParamsRequest


class DependencyRuntimeTest(unittest.TestCase):
    def test_health_reports_dependency_payload(self):
        dependency_status = {
            "supportedMatrix": {
                "python": {"range": ">=3.10,<3.15"},
                "gmsh_python": {"range": ">=4.11,<5.0", "required_for": "/api/mesh/build"},
                "bempp_cl": {"range": ">=0.4,<0.5", "required_for": "/api/solve"},
            },
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "4.15.0", "supported": True, "ready": True},
                "bempp": {"available": False, "variant": None, "version": None, "supported": False, "ready": False},
            },
        }
        dependency_doctor = {
            "schemaVersion": "waveguide-runtime-doctor.v1",
            "generatedAt": "2026-03-19T10:00:00Z",
            "platform": {"system": "Linux"},
            "summary": {"requiredReady": False, "requiredIssues": ["bempp_cl"]},
            "components": [
                {
                    "id": "bempp_cl",
                    "name": "bempp-cl",
                    "category": "required",
                    "status": "missing",
                    "featureImpact": "/api/solve BEM simulation is unavailable.",
                    "guidance": ["Install bempp-cl: pip install git+https://github.com/bempp/bempp-cl.git"],
                }
            ],
        }

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report", return_value=dependency_doctor
        ), patch(
            "api.routes_misc.SOLVER_AVAILABLE", False
        ), patch("api.routes_misc.BEMPP_RUNTIME_READY", False), patch("api.routes_misc.WAVEGUIDE_BUILDER_AVAILABLE", True), patch(
            "api.routes_misc.GMSH_OCC_RUNTIME_READY", True
        ):
            response = asyncio.run(health_check())

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["dependencies"], dependency_status)
        self.assertEqual(response["dependencyDoctor"], dependency_doctor)
        self.assertFalse(response["solverReady"])
        self.assertTrue(response["occBuilderReady"])
        self.assertEqual(
            response["capabilities"]["simulationBasic"]["controls"],
            [
                "device_mode",
                "mesh_validation_mode",
                "frequency_spacing",
                "verbose",
            ],
        )
        self.assertTrue(response["capabilities"]["simulationAdvanced"]["available"])
        self.assertEqual(
            response["capabilities"]["simulationAdvanced"]["controls"],
            [
                "use_burton_miller",
            ],
        )
        self.assertIn(
            "Burton-Miller",
            response["capabilities"]["simulationAdvanced"]["reason"],
        )

    def test_mesh_build_dependency_gate_returns_matrix_details(self):
        dependency_status = {
            "supportedMatrix": {
                "python": {"range": ">=3.10,<3.15"},
                "gmsh_python": {"range": ">=4.11,<5.0", "required_for": "/api/mesh/build"},
            },
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "5.1.0", "supported": False, "ready": False},
                "bempp": {"available": False, "variant": None, "version": None, "supported": False, "ready": False},
            },
        }
        request = WaveguideParamsRequest(formula_type="OSSE")

        with patch("api.routes_mesh.WAVEGUIDE_BUILDER_AVAILABLE", True), patch(
            "api.routes_mesh.build_waveguide_mesh", return_value={}
        ), patch("api.routes_mesh.GMSH_OCC_RUNTIME_READY", False), patch(
            "api.routes_mesh.get_dependency_status", return_value=dependency_status
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(build_mesh_from_params(request))

        self.assertEqual(ctx.exception.status_code, 503)
        detail = str(ctx.exception.detail)
        self.assertIn("python=3.13.1 supported=True", detail)
        self.assertIn("gmsh=5.1.0 supported=False", detail)
        self.assertIn("python >=3.10,<3.15", detail)
        self.assertIn("gmsh >=4.11,<5.0", detail)

    def test_health_includes_device_interface_metadata_when_solver_available(self):
        dependency_status = {
            "supportedMatrix": {},
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "4.15.0", "supported": True, "ready": True},
                "bempp": {"available": True, "variant": "bempp_cl", "version": "0.4.2", "supported": True, "ready": True},
            },
        }
        device_info = {
            "requested_mode": "auto",
            "selected_mode": "opencl_gpu",
            "interface": "opencl",
            "device_type": "gpu",
            "device_name": "Fake GPU",
            "fallback_reason": None,
            "available_modes": ["auto", "opencl_cpu", "opencl_gpu"],
        }

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report",
            return_value={"components": [], "summary": {"requiredReady": True}},
        ), patch(
            "api.routes_misc.SOLVER_AVAILABLE", True
        ), patch("api.routes_misc.BEMPP_RUNTIME_READY", True), patch("api.routes_misc.WAVEGUIDE_BUILDER_AVAILABLE", True), patch(
            "api.routes_misc.GMSH_OCC_RUNTIME_READY", True
        ), patch(
            "solver.device_interface.selected_device_metadata", return_value=device_info
        ):
            response = asyncio.run(health_check())

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["deviceInterface"], device_info)
        self.assertIn("capabilities", response)

    def test_solve_dependency_gate_returns_matrix_details(self):
        dependency_status = {
            "supportedMatrix": {
                "bempp_cl": {"range": ">=0.4,<0.5", "required_for": "/api/solve"},
            },
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "4.15.0", "supported": True, "ready": True},
                "bempp": {"available": True, "variant": "bempp_cl", "version": "0.5.1", "supported": False, "ready": False},
            },
        }
        request = SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format="msh",
                boundaryConditions={},
                metadata={},
            ),
            frequency_range=[100.0, 1000.0],
            num_frequencies=8,
            sim_type="2",
            options={},
        )

        with patch("api.routes_simulation.SOLVER_AVAILABLE", False), patch(
            "api.routes_simulation.get_dependency_status", return_value=dependency_status
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 503)
        detail = str(ctx.exception.detail)
        self.assertIn("python=3.13.1 supported=True", detail)
        self.assertIn("bempp variant=bempp_cl version=0.5.1 supported=False", detail)
        self.assertIn("bempp-cl >=0.4,<0.5", detail)

    def test_canonical_solve_remains_available_when_occ_runtime_is_unavailable(self):
        dependency_status = {
            "supportedMatrix": {
                "python": {"range": ">=3.10,<3.15"},
                "gmsh_python": {"range": ">=4.11,<5.0", "required_for": "/api/mesh/build"},
                "bempp_cl": {"range": ">=0.4,<0.5", "required_for": "/api/solve"},
            },
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": False, "version": None, "supported": False, "ready": False},
                "bempp": {"available": True, "variant": "bempp_cl", "version": "0.4.2", "supported": True, "ready": True},
            },
        }
        request = SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format="msh",
                boundaryConditions={},
                metadata={},
            ),
            frequency_range=[100.0, 1000.0],
            num_frequencies=8,
            sim_type="2",
            options={},
        )
        job_uuid = uuid.UUID("22222222-2222-2222-2222-222222222222")

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report",
            return_value={"components": [], "summary": {"requiredReady": False, "requiredIssues": ["gmsh_python"]}},
        ), patch(
            "api.routes_misc.SOLVER_AVAILABLE", True
        ), patch("api.routes_misc.BEMPP_RUNTIME_READY", True), patch(
            "api.routes_misc.WAVEGUIDE_BUILDER_AVAILABLE", True
        ), patch("api.routes_misc.GMSH_OCC_RUNTIME_READY", False):
            health = asyncio.run(health_check())

        self.assertTrue(health["solverReady"])
        self.assertFalse(health["occBuilderReady"])

        with patch("api.routes_simulation.SOLVER_AVAILABLE", True), patch(
            "api.routes_simulation.BEMPP_RUNTIME_READY", True
        ), patch("api.routes_simulation.WAVEGUIDE_BUILDER_AVAILABLE", True), patch(
            "api.routes_simulation.GMSH_OCC_RUNTIME_READY", False
        ), patch("api.routes_simulation.get_dependency_status", return_value=dependency_status) as dependency_mock, patch(
            "api.routes_simulation.create_simulation_job", return_value=str(job_uuid)
        ) as create_simulation_job:
            response = asyncio.run(submit_simulation(request))

        self.assertEqual(response, {"job_id": str(job_uuid)})
        create_simulation_job.assert_called_once()
        dependency_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
