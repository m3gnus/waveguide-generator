import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import MeshData, SimulationRequest, submit_simulation


class ApiValidationTest(unittest.TestCase):
    def test_surface_tags_length_validation_runs_before_solver_check(self):
        request = SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[],
                format='msh',
                boundaryConditions={},
                metadata={}
            ),
            frequency_range=[100.0, 1000.0],
            num_frequencies=10,
            sim_type='2',
            options={}
        )

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn('surfaceTags length', str(ctx.exception.detail))

    def test_sim_type_one_is_explicitly_deferred(self):
        request = SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format='msh',
                boundaryConditions={},
                metadata={}
            ),
            frequency_range=[100.0, 1000.0],
            num_frequencies=10,
            sim_type='1',
            options={}
        )

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("deferred", str(ctx.exception.detail))

    def test_invalid_mesh_validation_mode_is_rejected(self):
        request = SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format='msh',
                boundaryConditions={},
                metadata={}
            ),
            frequency_range=[100.0, 1000.0],
            num_frequencies=10,
            sim_type='2',
            options={},
            mesh_validation_mode='invalid'
        )

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("mesh_validation_mode", str(ctx.exception.detail))

    def test_occ_adaptive_requires_waveguide_params(self):
        request = SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format='msh',
                boundaryConditions={},
                metadata={}
            ),
            frequency_range=[100.0, 1000.0],
            num_frequencies=10,
            sim_type='2',
            options={"mesh": {"strategy": "occ_adaptive"}}
        )

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("waveguide_params", str(ctx.exception.detail))

    def test_occ_adaptive_runtime_gate_returns_503(self):
        request = SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format='msh',
                boundaryConditions={},
                metadata={}
            ),
            frequency_range=[100.0, 1000.0],
            num_frequencies=10,
            sim_type='2',
            options={
                "mesh": {
                    "strategy": "occ_adaptive",
                    "waveguide_params": {"formula_type": "OSSE"}
                }
            }
        )

        dependency_status = {
            "supportedMatrix": {
                "python": {"range": ">=3.10,<3.14"},
                "gmsh_python": {"range": ">=4.15,<5.0", "required_for": "/api/mesh/build"},
                "bempp_cl": {"range": ">=0.4,<0.5", "required_for": "/api/solve"},
                "bempp_api_legacy": {"range": ">=0.3,<0.4", "required_for": "/api/solve (legacy fallback)"},
            },
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "5.1.0", "supported": False, "ready": False},
                "bempp": {"available": True, "variant": "bempp_cl", "version": "0.4.2", "supported": True, "ready": True},
            },
        }

        with patch("app.SOLVER_AVAILABLE", True), patch("app.BEMPP_RUNTIME_READY", True), patch(
            "app.WAVEGUIDE_BUILDER_AVAILABLE", True
        ), patch("app.GMSH_OCC_RUNTIME_READY", False), patch(
            "app.get_dependency_status", return_value=dependency_status
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("Adaptive OCC mesh builder dependency check failed", str(ctx.exception.detail))

    def test_occ_adaptive_coerces_quadrants_to_full_domain(self):
        request = SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format='msh',
                boundaryConditions={},
                metadata={}
            ),
            frequency_range=[100.0, 1000.0],
            num_frequencies=10,
            sim_type='2',
            options={
                "mesh": {
                    "strategy": "occ_adaptive",
                    "waveguide_params": {"formula_type": "OSSE", "quadrants": 1}
                }
            }
        )

        with patch("app.SOLVER_AVAILABLE", True), patch("app.WAVEGUIDE_BUILDER_AVAILABLE", True), patch(
            "app.GMSH_OCC_RUNTIME_READY", True
        ), patch("app.asyncio.create_task") as create_task:
            create_task.side_effect = lambda coro: (coro.close(), None)[1]
            result = asyncio.run(submit_simulation(request))

        self.assertIn("job_id", result)
        self.assertEqual(request.options["mesh"]["waveguide_params"]["quadrants"], 1234)
        create_task.assert_called_once()

    def test_occ_adaptive_accepts_rosse_b_expression(self):
        request = SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format='msh',
                boundaryConditions={},
                metadata={}
            ),
            frequency_range=[100.0, 1000.0],
            num_frequencies=10,
            sim_type='2',
            options={
                "mesh": {
                    "strategy": "occ_adaptive",
                    "waveguide_params": {
                        "formula_type": "R-OSSE",
                        "R": "140",
                        "a": "45",
                        "b": "0.2+0.1*sin(p)",
                    },
                }
            }
        )

        dependency_status = {
            "supportedMatrix": {
                "python": {"range": ">=3.10,<3.14"},
                "gmsh_python": {"range": ">=4.15,<5.0", "required_for": "/api/mesh/build"},
                "bempp_cl": {"range": ">=0.4,<0.5", "required_for": "/api/solve"},
                "bempp_api_legacy": {"range": ">=0.3,<0.4", "required_for": "/api/solve (legacy fallback)"},
            },
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "5.1.0", "supported": False, "ready": False},
                "bempp": {"available": True, "variant": "bempp_cl", "version": "0.4.2", "supported": True, "ready": True},
            },
        }

        with patch("app.SOLVER_AVAILABLE", True), patch("app.BEMPP_RUNTIME_READY", True), patch(
            "app.WAVEGUIDE_BUILDER_AVAILABLE", True
        ), patch("app.GMSH_OCC_RUNTIME_READY", False), patch(
            "app.get_dependency_status", return_value=dependency_status
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("Adaptive OCC mesh builder dependency check failed", str(ctx.exception.detail))


class MeshArtifactEndpointTest(unittest.TestCase):
    def test_mesh_artifact_returns_404_for_unknown_job(self):
        from app import get_mesh_artifact

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(get_mesh_artifact("nonexistent-job"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_mesh_artifact_returns_404_when_no_artifact(self):
        from app import get_mesh_artifact, jobs

        jobs["test-no-artifact"] = {"status": "complete", "results": None}
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(get_mesh_artifact("test-no-artifact"))
            self.assertEqual(ctx.exception.status_code, 404)
            self.assertIn("No mesh artifact", str(ctx.exception.detail))
        finally:
            del jobs["test-no-artifact"]

    def test_mesh_artifact_returns_msh_text(self):
        from app import get_mesh_artifact, jobs

        msh_content = "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"
        jobs["test-with-artifact"] = {
            "status": "complete",
            "results": None,
            "mesh_artifact": msh_content,
        }
        try:
            resp = asyncio.run(get_mesh_artifact("test-with-artifact"))
            self.assertEqual(resp.body.decode(), msh_content)
            self.assertIn("text/plain", resp.media_type)
        finally:
            del jobs["test-with-artifact"]


if __name__ == "__main__":
    unittest.main()
