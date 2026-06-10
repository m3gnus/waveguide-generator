import asyncio
import importlib.util
from importlib import metadata
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from api.routes_mesh import build_mesh_from_params
from api.routes_misc import health_check
from api.routes_simulation import submit_simulation
from contracts import MeshData, SimulationRequest, WaveguideParamsRequest
from solver.deps import SUPPORTED_DEPENDENCY_MATRIX, get_dependency_status


def _dependency_status(
    *,
    gmsh_ready=True,
    gmsh_available=True,
    gmsh_supported=True,
    gmsh_version="4.15.0",
    mesher_ready=True,
    metal_bem_ready=True,
    metal_bem_version="0.2.0",
):
    return {
        "supportedMatrix": {
            "python": {"range": ">=3.10,<3.15"},
            "hornlab_waveguide_mesher": {
                "range": "pinned git commit 2eb7b85",
                "required_for": "/api/mesh/build",
            },
            "hornlab_metal_bem": {
                "range": "pinned git commit 59528f5",
                "required_for": "/api/solve backend",
            },
            "gmsh_python": {"range": ">=4.11,<5.0", "required_for": "hornlab-waveguide-mesher"},
        },
        "runtime": {
            "python": {"version": "3.13.1", "supported": True},
            "gmsh_python": {
                "available": gmsh_available,
                "version": gmsh_version,
                "supported": gmsh_supported,
                "ready": gmsh_ready,
            },
            "hornlab_waveguide_mesher": {
                "available": mesher_ready,
                "version": "0.1.0" if mesher_ready else None,
                "supported": mesher_ready,
                "ready": mesher_ready,
            },
            "hornlab_metal_bem": {
                "available": metal_bem_ready,
                "version": metal_bem_version if metal_bem_ready else None,
                "supported": metal_bem_ready,
                "ready": metal_bem_ready,
            },
        },
    }


class DependencyRuntimeTest(unittest.TestCase):
    def test_solver_bootstrap_requires_real_mesher_package_availability(self):
        module_path = Path(__file__).resolve().parents[1] / "solver_bootstrap.py"
        fake_solver = types.ModuleType("solver")
        fake_solver.__path__ = []
        fake_deps = types.ModuleType("solver.deps")
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
        self.assertFalse(module.METAL_SOLVER_READY)
        self.assertFalse(module.SOLVER_AVAILABLE)

    def test_solver_deps_rejects_same_module_from_wrong_distribution(self):
        module_path = Path(__file__).resolve().parents[1] / "solver" / "deps.py"
        fake_package = types.ModuleType("hornlab_mesher")
        fake_config_builder = types.ModuleType("hornlab_mesher.config_builder")
        fake_config_builder.build_from_config = lambda *_args, **_kwargs: None

        def fake_version(name):
            if name == "hornlab-waveguide-mesher":
                raise metadata.PackageNotFoundError(name)
            raise metadata.PackageNotFoundError(name)

        with patch.dict(
            sys.modules,
            {
                "hornlab_mesher": fake_package,
                "hornlab_mesher.config_builder": fake_config_builder,
            },
        ), patch("importlib.metadata.version", side_effect=fake_version):
            spec = importlib.util.spec_from_file_location("_isolated_solver_deps", module_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

        self.assertFalse(module.HORNLAB_MESHER_AVAILABLE)
        self.assertIsNone(module.HORNLAB_MESHER_VERSION)

    def test_dependency_matrix_pins_metal_bem_and_mesher_without_bempp(self):
        self.assertEqual(
            set(SUPPORTED_DEPENDENCY_MATRIX.keys()),
            {"python", "hornlab_waveguide_mesher", "hornlab_metal_bem", "gmsh_python"},
        )
        self.assertIn("2eb7b85", SUPPORTED_DEPENDENCY_MATRIX["hornlab_waveguide_mesher"]["range"])
        self.assertIn("59528f5", SUPPORTED_DEPENDENCY_MATRIX["hornlab_metal_bem"]["range"])
        self.assertEqual(
            SUPPORTED_DEPENDENCY_MATRIX["hornlab_metal_bem"]["required_for"],
            "/api/solve backend",
        )

        status = get_dependency_status()
        self.assertEqual(
            set(status["runtime"].keys()),
            {"python", "gmsh_python", "hornlab_waveguide_mesher", "hornlab_metal_bem"},
        )
        self.assertNotIn("bempp", status["runtime"])
        self.assertNotIn("bempp_cl", status["supportedMatrix"])

    def test_health_reports_dependency_payload(self):
        dependency_status = _dependency_status(metal_bem_ready=False)
        dependency_doctor = {
            "schemaVersion": 1,
            "generatedAt": "2026-06-11T10:00:00Z",
            "platform": {"system": "Darwin", "machine": "arm64"},
            "summary": {
                "requiredReady": False,
                "requiredIssues": ["hornlab_metal_bem"],
                "solveReady": False,
            },
            "components": [
                {
                    "id": "hornlab_metal_bem",
                    "name": "HornLab Metal BEM",
                    "category": "required",
                    "status": "missing",
                    "featureImpact": "/api/solve BEM simulation is unavailable.",
                    "guidance": [
                        "Install backend requirements: pip install -r server/requirements.txt"
                    ],
                }
            ],
        }

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report", return_value=dependency_doctor
        ), patch("api.routes_misc.METAL_SOLVER_READY", False), patch(
            "api.routes_misc.metal_backend_status",
            return_value={"available": False, "reason": "hornlab-metal-bem is not installed."},
        ), patch("api.routes_misc.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_misc.HORNLAB_MESHER_RUNTIME_READY", True
        ):
            response = asyncio.run(health_check())

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["solver"], "unavailable")
        self.assertFalse(response["solverReady"])
        self.assertTrue(response["mesherReady"])
        self.assertEqual(response["dependencies"], dependency_status)
        self.assertEqual(response["dependencyDoctor"]["schemaVersion"], 1)
        self.assertEqual(response["dependencyDoctor"]["summary"], dependency_doctor["summary"])
        self.assertEqual(
            response["dependencyDoctor"]["components"], dependency_doctor["components"]
        )
        self.assertEqual(set(response["solverBackends"].keys()), {"metal"})
        self.assertFalse(response["solverBackends"]["metal"]["ready"])
        self.assertNotIn("deviceInterface", response)
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
            ["solver_backend"],
        )
        self.assertIn(
            "solver backend",
            response["capabilities"]["simulationAdvanced"]["reason"],
        )

    def test_mesh_build_dependency_gate_returns_matrix_details(self):
        dependency_status = _dependency_status(
            gmsh_ready=False,
            gmsh_supported=False,
            gmsh_version="5.1.0",
        )
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

    def test_health_solver_ready_tracks_metal_runtime_not_doctor_gate(self):
        """The doctor's solveReady gate no longer feeds solverReady; metal runtime does."""
        dependency_status = _dependency_status()
        dependency_doctor = {
            "schemaVersion": 1,
            "components": [],
            "summary": {
                "requiredReady": False,
                "requiredIssues": ["metal_release_helper"],
                "solveReady": False,
            },
        }
        metal_status = {
            "available": True,
            "supportedPlatform": True,
            "nativeHelperAvailable": True,
            "nativeHelperBuild": "debug",
            "reason": None,
        }

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report", return_value=dependency_doctor
        ), patch("api.routes_misc.METAL_SOLVER_READY", True), patch(
            "api.routes_misc.metal_backend_status", return_value=metal_status
        ), patch("api.routes_misc.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_misc.HORNLAB_MESHER_RUNTIME_READY", True
        ):
            response = asyncio.run(health_check())

        self.assertTrue(response["solverReady"])
        self.assertEqual(response["solver"], "metal-bem")
        self.assertTrue(response["solverBackends"]["metal"]["ready"])

    def test_health_solver_ready_accepts_metal_backend(self):
        dependency_status = _dependency_status()
        dependency_doctor = {
            "schemaVersion": 1,
            "components": [],
            "summary": {"requiredReady": True, "requiredIssues": [], "solveReady": True},
        }
        metal_status = {
            "available": True,
            "supportedPlatform": True,
            "nativeHelperAvailable": True,
            "nativeHelperBuild": "release",
            "reason": None,
        }

        with patch("api.routes_misc.get_dependency_status", return_value=dependency_status), patch(
            "api.routes_misc.collect_runtime_doctor_report", return_value=dependency_doctor
        ), patch("api.routes_misc.METAL_SOLVER_READY", True), patch(
            "api.routes_misc.metal_backend_status", return_value=metal_status
        ), patch("api.routes_misc.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_misc.HORNLAB_MESHER_RUNTIME_READY", True
        ):
            response = asyncio.run(health_check())

        self.assertTrue(response["solverReady"])
        self.assertEqual(response["solver"], "metal-bem")
        self.assertEqual(set(response["solverBackends"].keys()), {"metal"})
        self.assertTrue(response["solverBackends"]["metal"]["ready"])
        self.assertEqual(response["solverBackends"]["metal"]["status"], metal_status)
        self.assertNotIn("deviceInterface", response)

    def test_solve_metal_not_ready_returns_503(self):
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
            solver_backend="metal",
            options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": {
                "formula_type": "OSSE",
                "wall_thickness": 6.0,
                "enc_depth": 0.0,
            }}},
        )
        metal_status = {
            "available": False,
            "supportedPlatform": False,
            "nativeHelperAvailable": False,
            "reason": "hornlab-metal-bem is not installed.",
        }

        with patch("api.routes_simulation.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_simulation.HORNLAB_MESHER_RUNTIME_READY", True
        ), patch(
            "api.routes_simulation.build_waveguide_mesh", MagicMock()
        ), patch("api.routes_simulation.METAL_SOLVER_READY", False), patch(
            "api.routes_simulation.metal_backend_status", return_value=metal_status
        ), patch("api.routes_simulation.create_simulation_job") as create_simulation_job:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 503)
        detail = str(ctx.exception.detail)
        self.assertIn("Metal BEM solver not available", detail)
        self.assertIn("reason=hornlab-metal-bem is not installed.", detail)
        self.assertIn("supported_platform=False", detail)
        create_simulation_job.assert_not_called()

    def test_hornlab_solve_requires_mesher_runtime(self):
        dependency_status = _dependency_status(
            gmsh_ready=False,
            gmsh_available=False,
            gmsh_supported=False,
            gmsh_version=None,
        )
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
                "schemaVersion": 1,
                "components": [],
                "summary": {
                    "requiredReady": False,
                    "requiredIssues": ["gmsh_python"],
                    "solveReady": True,
                    "solveIssues": [],
                },
            },
        ), patch("api.routes_misc.METAL_SOLVER_READY", True), patch(
            "api.routes_misc.metal_backend_status",
            return_value={"available": True, "supportedPlatform": True, "reason": None},
        ), patch(
            "api.routes_misc.HORNLAB_MESHER_AVAILABLE", True
        ), patch("api.routes_misc.HORNLAB_MESHER_RUNTIME_READY", False):
            health = asyncio.run(health_check())

        self.assertTrue(health["solverReady"])
        self.assertFalse(health["mesherReady"])

        with patch("api.routes_simulation.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_simulation.HORNLAB_MESHER_RUNTIME_READY", False
        ), patch(
            "api.routes_simulation.build_waveguide_mesh", MagicMock()
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
