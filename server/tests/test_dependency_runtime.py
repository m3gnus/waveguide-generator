import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from api.routes_mesh import build_mesh_from_params
from api.routes_misc import health_check
from api.routes_simulation import submit_simulation
from contracts import MeshData, SimulationRequest, WaveguideParamsRequest


class DependencyRuntimeTest(unittest.TestCase):
    def test_solver_bootstrap_requires_real_mesher_package_availability(self):
        module_path = Path(__file__).resolve().parents[1] / "solver_bootstrap.py"
        fake_solver = types.ModuleType("solver")
        fake_solver.__path__ = []
        fake_deps = types.ModuleType("solver.deps")
        fake_deps.BEMPP_RUNTIME_READY = False
        fake_deps.HORNLAB_MESHER_AVAILABLE = False
        fake_deps.HORNLAB_MESHER_RUNTIME_READY = False
        fake_deps.get_dependency_status = lambda: {}
        fake_metal = types.ModuleType("solver.metal_solver")
        fake_metal.is_metal_solver_available = lambda: False
        fake_metal.metal_backend_status = lambda: {"available": False}
        fake_adapter = types.ModuleType("solver.mesher_adapter")
        fake_adapter.build_waveguide_mesh = lambda payload: {}

        with patch.dict(
            sys.modules,
            {
                "solver": fake_solver,
                "solver.deps": fake_deps,
                "solver.metal_solver": fake_metal,
                "solver.mesher_adapter": fake_adapter,
            },
        ):
            spec = importlib.util.spec_from_file_location(
                "_isolated_solver_bootstrap", module_path
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

        self.assertFalse(module.HORNLAB_MESHER_AVAILABLE)

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
            "summary": {"requiredReady": False, "requiredIssues": ["bempp_cl"], "solveReady": False},
            "components": [
                {
                    "id": "bempp_cl",
                    "name": "bempp-cl",
                    "category": "required",
                    "status": "missing",
                    "featureImpact": "/api/solve BEM simulation is unavailable.",
                    "guidance": [
                        "Install bempp-cl: pip install git+https://github.com/bempp/bempp-cl.git@d4f23c4b77b4e86e0b2c9da42db39fea2995bb33"
                    ],
                }
            ],
            "solveReadiness": {"ready": False, "status": "missing"},
        }

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report", return_value=dependency_doctor
        ), patch(
            "api.routes_misc.SOLVER_AVAILABLE", False
        ), patch("api.routes_misc.BEMPP_RUNTIME_READY", False), patch("api.routes_misc.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_misc.HORNLAB_MESHER_RUNTIME_READY", True
        ), patch("api.routes_misc.METAL_SOLVER_READY", False), patch(
            "api.routes_misc.metal_backend_status",
            return_value={"available": False, "reason": "not installed"},
        ):
            response = asyncio.run(health_check())

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["dependencies"], dependency_status)
        self.assertEqual(response["dependencyDoctor"], dependency_doctor)
        self.assertFalse(response["solverReady"])
        self.assertTrue(response["mesherReady"])
        self.assertEqual(
            response["capabilities"]["simulationBasic"]["controls"],
            [
                "mesh_validation_mode",
                "frequency_spacing",
                "verbose",
            ],
        )
        self.assertTrue(response["capabilities"]["simulationAdvanced"]["available"])
        self.assertEqual(
            response["capabilities"]["simulationAdvanced"]["controls"],
            [
                "solver_backend",
                "use_burton_miller",
            ],
        )
        self.assertIn(
            "solver backend",
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

        with patch("api.routes_mesh.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_mesh.build_waveguide_mesh", return_value={}
        ), patch("api.routes_mesh.HORNLAB_MESHER_RUNTIME_READY", False), patch(
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
            "selected_mode": "opencl_cpu",
            "interface": "opencl",
            "device_type": "cpu",
            "device_name": "Fake CPU",
            "fallback_reason": None,
            "available_modes": ["auto", "opencl_cpu", "opencl_gpu"],
            "selection_policy": "supported_opencl_modes",
            "supported_modes": ["opencl_cpu", "opencl_gpu"],
        }

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report",
            return_value={"components": [], "summary": {"requiredReady": True, "solveReady": True}},
        ), patch(
            "api.routes_misc.SOLVER_AVAILABLE", True
        ), patch("api.routes_misc.BEMPP_RUNTIME_READY", True), patch("api.routes_misc.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_misc.HORNLAB_MESHER_RUNTIME_READY", True
        ), patch(
            "solver.device_interface.selected_device_metadata", return_value=device_info
        ):
            response = asyncio.run(health_check())

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["deviceInterface"], device_info)
        self.assertTrue(response["solverReady"])
        self.assertIn("capabilities", response)

    def test_health_solver_ready_uses_doctor_solve_ready_gate(self):
        dependency_status = {"supportedMatrix": {}, "runtime": {}}
        dependency_doctor = {
            "components": [],
            "summary": {"requiredReady": True, "requiredIssues": [], "optionalIssues": ["bounded_solve_validation"], "solveReady": False},
            "solveReadiness": {"ready": False, "status": "missing"},
        }

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report", return_value=dependency_doctor
        ), patch("api.routes_misc.SOLVER_AVAILABLE", True), patch(
            "api.routes_misc.BEMPP_RUNTIME_READY", True
        ), patch("api.routes_misc.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_misc.HORNLAB_MESHER_RUNTIME_READY", True
        ), patch("api.routes_misc.METAL_SOLVER_READY", False), patch(
            "api.routes_misc.metal_backend_status",
            return_value={"available": False, "reason": "not installed"},
        ):
            response = asyncio.run(health_check())

        self.assertFalse(response["solverReady"])
        self.assertFalse(response["solverBackends"]["bempp"]["ready"])

    def test_health_solver_ready_accepts_metal_backend(self):
        dependency_status = {"supportedMatrix": {}, "runtime": {}}
        dependency_doctor = {
            "components": [],
            "summary": {"requiredReady": False, "requiredIssues": ["bempp_cl"], "solveReady": False},
            "solveReadiness": {"ready": False, "status": "missing"},
        }
        metal_status = {
            "available": True,
            "supportedPlatform": True,
            "nativeHelperAvailable": True,
            "reason": None,
        }

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report", return_value=dependency_doctor
        ), patch("api.routes_misc.SOLVER_AVAILABLE", False), patch(
            "api.routes_misc.BEMPP_RUNTIME_READY", False
        ), patch("api.routes_misc.METAL_SOLVER_READY", True), patch(
            "api.routes_misc.metal_backend_status", return_value=metal_status
        ), patch("api.routes_misc.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_misc.HORNLAB_MESHER_RUNTIME_READY", True
        ):
            response = asyncio.run(health_check())

        self.assertTrue(response["solverReady"])
        self.assertEqual(response["solver"], "metal-bem")
        self.assertFalse(response["solverBackends"]["bempp"]["ready"])
        self.assertFalse(response["solverBackends"]["bempp"]["available"])
        self.assertTrue(response["solverBackends"]["metal"]["ready"])

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
            solver_backend="bempp",
            options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": {
                "formula_type": "OSSE",
                "wall_thickness": 6.0,
                "enc_depth": 0.0,
            }}},
        )

        with patch("api.routes_simulation.SOLVER_AVAILABLE", False), patch(
            "api.routes_simulation.HORNLAB_MESHER_AVAILABLE", True
        ), patch("api.routes_simulation.HORNLAB_MESHER_RUNTIME_READY", True), patch(
            "api.routes_simulation.get_dependency_status", return_value=dependency_status
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 503)
        detail = str(ctx.exception.detail)
        self.assertIn("python=3.13.1 supported=True", detail)
        self.assertIn("bempp variant=bempp_cl version=0.5.1 supported=False", detail)
        self.assertIn("bempp-cl >=0.4,<0.5", detail)

    def test_hornlab_solve_requires_mesher_runtime(self):
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
            options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": {
                "formula_type": "OSSE",
                "wall_thickness": 6.0,
                "enc_depth": 0.0,
            }}},
        )

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report",
            return_value={
                "components": [],
                "summary": {
                    "requiredReady": False,
                    "requiredIssues": ["gmsh_python"],
                    "solveReady": True,
                    "solveIssues": [],
                },
            },
        ), patch(
            "api.routes_misc.SOLVER_AVAILABLE", True
        ), patch("api.routes_misc.BEMPP_RUNTIME_READY", True), patch(
            "api.routes_misc.HORNLAB_MESHER_AVAILABLE", True
        ), patch("api.routes_misc.HORNLAB_MESHER_RUNTIME_READY", False):
            health = asyncio.run(health_check())

        self.assertTrue(health["solverReady"])
        self.assertFalse(health["mesherReady"])

        with patch("api.routes_simulation.SOLVER_AVAILABLE", True), patch(
            "api.routes_simulation.BEMPP_RUNTIME_READY", True
        ), patch("api.routes_simulation.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_simulation.HORNLAB_MESHER_RUNTIME_READY", False
        ), patch("api.routes_simulation.get_dependency_status", return_value=dependency_status) as dependency_mock, patch(
            "api.routes_simulation.create_simulation_job"
        ) as create_simulation_job:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("hornlab-waveguide-mesher dependency check failed", str(ctx.exception.detail))
        create_simulation_job.assert_not_called()
        dependency_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
