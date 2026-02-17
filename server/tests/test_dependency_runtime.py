import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app
from app import MeshData, SimulationRequest, WaveguideParamsRequest


class DependencyRuntimeTest(unittest.TestCase):
    def test_health_reports_dependency_payload(self):
        dependency_status = {
            "supportedMatrix": {
                "python": {"range": ">=3.10,<3.14"},
                "gmsh_python": {"range": ">=4.15,<5.0", "required_for": "/api/mesh/build"},
                "bempp_cl": {"range": ">=0.4,<0.5", "required_for": "/api/solve"},
                "bempp_api_legacy": {"range": ">=0.3,<0.4", "required_for": "/api/solve (legacy fallback)"},
            },
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "4.15.0", "supported": True, "ready": True},
                "bempp": {"available": False, "variant": None, "version": None, "supported": False, "ready": False},
            },
        }

        with patch("app.get_dependency_status", return_value=dependency_status), patch(
            "app.SOLVER_AVAILABLE", False
        ), patch("app.BEMPP_RUNTIME_READY", False), patch("app.WAVEGUIDE_BUILDER_AVAILABLE", True), patch(
            "app.GMSH_OCC_RUNTIME_READY", True
        ):
            response = asyncio.run(app.health_check())

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["dependencies"], dependency_status)
        self.assertFalse(response["solverReady"])
        self.assertTrue(response["occBuilderReady"])

    def test_mesh_build_dependency_gate_returns_matrix_details(self):
        dependency_status = {
            "supportedMatrix": {
                "python": {"range": ">=3.10,<3.14"},
                "gmsh_python": {"range": ">=4.15,<5.0", "required_for": "/api/mesh/build"},
            },
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "5.1.0", "supported": False, "ready": False},
                "bempp": {"available": False, "variant": None, "version": None, "supported": False, "ready": False},
            },
        }
        request = WaveguideParamsRequest(formula_type="OSSE")

        with patch("app.WAVEGUIDE_BUILDER_AVAILABLE", True), patch(
            "app.build_waveguide_mesh", return_value={}
        ), patch("app.GMSH_OCC_RUNTIME_READY", False), patch(
            "app.get_dependency_status", return_value=dependency_status
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(app.build_mesh_from_params(request))

        self.assertEqual(ctx.exception.status_code, 503)
        detail = str(ctx.exception.detail)
        self.assertIn("python=3.13.1 supported=True", detail)
        self.assertIn("gmsh=5.1.0 supported=False", detail)
        self.assertIn("python >=3.10,<3.14", detail)
        self.assertIn("gmsh >=4.15,<5.0", detail)

    def test_solve_dependency_gate_returns_matrix_details(self):
        dependency_status = {
            "supportedMatrix": {
                "bempp_cl": {"range": ">=0.4,<0.5", "required_for": "/api/solve"},
                "bempp_api_legacy": {"range": ">=0.3,<0.4", "required_for": "/api/solve (legacy fallback)"},
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

        with patch("app.SOLVER_AVAILABLE", False), patch(
            "app.get_dependency_status", return_value=dependency_status
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(app.submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 503)
        detail = str(ctx.exception.detail)
        self.assertIn("python=3.13.1 supported=True", detail)
        self.assertIn("bempp variant=bempp_cl version=0.5.1 supported=False", detail)
        self.assertIn("bempp-cl >=0.4,<0.5", detail)
        self.assertIn("legacy bempp_api >=0.3,<0.4", detail)


if __name__ == "__main__":
    unittest.main()
