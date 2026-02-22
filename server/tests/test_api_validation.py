import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

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

    def test_invalid_device_mode_is_rejected(self):
        with self.assertRaises(ValidationError):
            SimulationRequest(
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
                device_mode='opencl_magic'
            )

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

    def test_occ_adaptive_requires_enclosure_or_wall_shell(self):
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
                        "formula_type": "OSSE",
                        "enc_depth": 0.0,
                        "wall_thickness": 0.0,
                    },
                }
            },
        )

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Increase enclosure depth or wall thickness", str(ctx.exception.detail))

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


class PolarConfigValidationTest(unittest.TestCase):
    def _mesh(self):
        return MeshData(
            vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            indices=[0, 1, 2],
            surfaceTags=[2],
            format='msh',
            boundaryConditions={},
            metadata={}
        )

    def test_empty_enabled_axes_is_rejected(self):
        with self.assertRaises(ValidationError):
            SimulationRequest(
                mesh=self._mesh(),
                frequency_range=[100.0, 1000.0],
                num_frequencies=8,
                sim_type='2',
                options={},
                polar_config={"enabled_axes": []}
            )

    def test_valid_enabled_axes_subset_is_accepted(self):
        request = SimulationRequest(
            mesh=self._mesh(),
            frequency_range=[100.0, 1000.0],
            num_frequencies=8,
            sim_type='2',
            options={},
            polar_config={"enabled_axes": ["vertical"]}
        )
        self.assertEqual(request.polar_config.enabled_axes, ["vertical"])


class OccAdaptiveBemMeshContractTest(unittest.TestCase):
    """occ_adaptive BEM path must pass wall_thickness through to build_waveguide_mesh unchanged.

    The outer wall shell is part of the BEM mesh (tag 1 in the ABEC/ATH convention).
    app.py normalizes all non-source tags to 1 after build_waveguide_mesh returns, so
    the solver always sees exactly tag 1 (rigid wall) and tag 2 (source disc).
    """

    def _make_occ_adaptive_request(self, extra_params=None):
        wp = {
            "formula_type": "R-OSSE",
            "R": "140",
            "a": "50",
            "r0": 12.7,
            "a0": 15.5,
            "k": 0.6,
            "r": 0.4,
            "b": "0.2",
            "m": 0.86,
            "q": 3.5,
            "n_angular": 20,
            "n_length": 8,
            "wall_thickness": 6.0,
            "enc_depth": 0.0,
            "throat_res": 5.0,
            "mouth_res": 15.0,
            "quadrants": 1234,
        }
        if extra_params:
            wp.update(extra_params)
        return SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format="msh",
                boundaryConditions={},
                metadata={},
            ),
            frequency_range=[100.0, 1000.0],
            num_frequencies=1,
            sim_type="2",
            options={"mesh": {"strategy": "occ_adaptive", "waveguide_params": wp}},
        )

    def test_occ_adaptive_preserves_wall_thickness_in_build_call(self):
        """run_simulation must NOT zero wall_thickness for occ_adaptive BEM builds.

        The outer wall shell (wall_thickness > 0) is part of the BEM mesh: it forms the
        topologically connected rigid-wall boundary that encloses the horn cavity.
        waveguide_builder assigns all wall surfaces to tag 1 (SD1G0); app.py normalises
        any remaining non-source tags to 1 after the build, so the BEM solver always
        sees exactly tag 1 (rigid wall) and tag 2 (source disc).
        """
        from app import run_simulation, jobs

        request = self._make_occ_adaptive_request({"wall_thickness": 6.0, "enc_depth": 0.0})

        captured_params = []

        def fake_build(params, **kwargs):
            captured_params.append(dict(params))
            return {
                "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
                "stats": {"nodeCount": 0, "elementCount": 0},
                "canonical_mesh": {"vertices": [], "indices": [], "surfaceTags": []},
            }

        job_id = "test-preserve-wall"
        jobs[job_id] = {
            "status": "queued", "progress": 0.0, "stage": "queued",
            "stage_message": "", "results": None, "error": None,
        }
        try:
            with patch("app.WAVEGUIDE_BUILDER_AVAILABLE", True), patch(
                "app.GMSH_OCC_RUNTIME_READY", True
            ), patch("app.build_waveguide_mesh", side_effect=fake_build):
                asyncio.run(run_simulation(job_id, request))
        finally:
            jobs.pop(job_id, None)

        self.assertTrue(
            len(captured_params) > 0,
            "build_waveguide_mesh must be called during occ_adaptive run_simulation.",
        )
        call_params = captured_params[0]
        self.assertEqual(
            call_params.get("wall_thickness"),
            6.0,
            "occ_adaptive BEM build must preserve wall_thickness (outer wall is part of BEM mesh).",
        )


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
