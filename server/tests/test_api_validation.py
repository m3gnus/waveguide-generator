import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from api.routes_misc import render_directivity
from api.routes_simulation import (
    get_job_status,
    get_mesh_artifact,
    get_results,
    stop_simulation,
    submit_simulation,
)
from contracts import DirectivityRenderRequest, JobMetadataPatch, MeshData, SimulationRequest
import services.job_runtime as _jrt
import services.simulation_runner as _sim_runner


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

    def test_missing_source_tag_is_rejected_before_solver_check(self):
        request = SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[1],
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
        self.assertIn('source tag 2', str(ctx.exception.detail))

    def test_sim_type_one_is_rejected_as_unsupported(self):
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
        self.assertIn("sim_type='2'", str(ctx.exception.detail))
        self.assertIn("removed", str(ctx.exception.detail))

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

    def test_invalid_advanced_bem_precision_is_rejected(self):
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
                advanced_settings={'bem_precision': 'fp16'}
            )

    def test_hornlab_mesher_requires_waveguide_params(self):
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
            options={"mesh": {"strategy": "hornlab_mesher"}}
        )

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("waveguide_params", str(ctx.exception.detail))

    def test_hornlab_mesher_requires_enclosure_or_wall_shell(self):
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
                    "strategy": "hornlab_mesher",
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

    def test_hornlab_mesher_runtime_gate_returns_503(self):
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
                    "strategy": "hornlab_mesher",
                    "waveguide_params": {"formula_type": "OSSE"}
                }
            }
        )

        dependency_status = {
            "supportedMatrix": {
                "python": {"range": ">=3.10,<3.15"},
                "gmsh_python": {"range": ">=4.11,<5.0", "required_for": "/api/mesh/build"},
                "bempp_cl": {"range": ">=0.4,<0.5", "required_for": "/api/solve"},
            },
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "5.1.0", "supported": False, "ready": False},
                "bempp": {"available": True, "variant": "bempp_cl", "version": "0.4.2", "supported": True, "ready": True},
            },
        }

        with patch("api.routes_simulation.SOLVER_AVAILABLE", True), patch("api.routes_simulation.BEMPP_RUNTIME_READY", True), patch(
            "api.routes_simulation.HORNLAB_MESHER_AVAILABLE", True
        ), patch("api.routes_simulation.HORNLAB_MESHER_RUNTIME_READY", False), patch(
            "api.routes_simulation.get_dependency_status", return_value=dependency_status
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("hornlab-waveguide-mesher dependency check failed", str(ctx.exception.detail))

    def test_hornlab_mesher_submission_preserves_reduced_domain_for_metal_without_mutating_input(self):
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
            solver_backend="metal",
            options={
                "mesh": {
                    "strategy": "hornlab_mesher",
                    "waveguide_params": {"formula_type": "OSSE", "quadrants": 1}
                }
            }
        )

        job_id = "11111111-1111-1111-1111-111111111111"

        with patch("api.routes_simulation.SOLVER_AVAILABLE", True), patch(
            "api.routes_simulation.METAL_SOLVER_READY", True
        ), patch(
            "api.routes_simulation.HORNLAB_MESHER_AVAILABLE", True
        ), patch("api.routes_simulation.HORNLAB_MESHER_RUNTIME_READY", True), patch(
            "api.routes_simulation.create_simulation_job", return_value=job_id
        ) as create_simulation_job:
            result = asyncio.run(submit_simulation(request))

        self.assertEqual(result["job_id"], job_id)
        # Original request object must not be mutated.
        self.assertEqual(request.options["mesh"]["waveguide_params"]["quadrants"], 1)
        submitted_request = create_simulation_job.call_args.args[0].model_dump()
        # Queued payload preserves the validated symmetry-reduction domain.
        self.assertEqual(submitted_request["options"]["mesh"]["waveguide_params"]["quadrants"], 1)

    def test_hornlab_mesher_submission_forces_full_domain_for_bempp_without_mutating_input(self):
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
            solver_backend="bempp",
            options={
                "mesh": {
                    "strategy": "hornlab_mesher",
                    "waveguide_params": {"formula_type": "OSSE", "quadrants": 1}
                }
            }
        )

        job_id = "11111111-1111-1111-1111-111111111112"

        with patch("api.routes_simulation.SOLVER_AVAILABLE", True), patch(
            "api.routes_simulation.HORNLAB_MESHER_AVAILABLE", True
        ), patch("api.routes_simulation.HORNLAB_MESHER_RUNTIME_READY", True), patch(
            "api.routes_simulation.create_simulation_job", return_value=job_id
        ) as create_simulation_job:
            result = asyncio.run(submit_simulation(request))

        self.assertEqual(result["job_id"], job_id)
        self.assertEqual(request.options["mesh"]["waveguide_params"]["quadrants"], 1)
        submitted_request = create_simulation_job.call_args.args[0].model_dump()
        self.assertEqual(submitted_request["options"]["mesh"]["waveguide_params"]["quadrants"], 1234)

    def test_occ_adaptive_strategy_is_rejected(self):
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
                        "quadrants": 14,
                        "wall_thickness": 6.0,
                    }
                }
            }
        )

        job_id = "22222222-2222-2222-2222-222222222222"

        with patch("api.routes_simulation.SOLVER_AVAILABLE", True), patch(
            "api.routes_simulation.HORNLAB_MESHER_AVAILABLE", True
        ), patch("api.routes_simulation.HORNLAB_MESHER_RUNTIME_READY", True), patch(
            "api.routes_simulation.create_simulation_job", return_value=job_id
        ) as create_simulation_job:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("hornlab_mesher", str(ctx.exception.detail))
        create_simulation_job.assert_not_called()

    def test_hornlab_mesher_accepts_rosse_b_expression(self):
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
                    "strategy": "hornlab_mesher",
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
                "python": {"range": ">=3.10,<3.15"},
                "gmsh_python": {"range": ">=4.11,<5.0", "required_for": "/api/mesh/build"},
                "bempp_cl": {"range": ">=0.4,<0.5", "required_for": "/api/solve"},
            },
            "runtime": {
                "python": {"version": "3.13.1", "supported": True},
                "gmsh_python": {"available": True, "version": "5.1.0", "supported": False, "ready": False},
                "bempp": {"available": True, "variant": "bempp_cl", "version": "0.4.2", "supported": True, "ready": True},
            },
        }

        with patch("api.routes_simulation.SOLVER_AVAILABLE", True), patch("api.routes_simulation.BEMPP_RUNTIME_READY", True), patch(
            "api.routes_simulation.HORNLAB_MESHER_AVAILABLE", True
        ), patch("api.routes_simulation.HORNLAB_MESHER_RUNTIME_READY", False), patch(
            "api.routes_simulation.get_dependency_status", return_value=dependency_status
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(submit_simulation(request))

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("hornlab-waveguide-mesher dependency check failed", str(ctx.exception.detail))


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


class JobMetadataPatchValidationTest(unittest.TestCase):
    def test_unknown_metadata_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            JobMetadataPatch.model_validate({"rating": 5})

    def test_script_snapshot_must_be_object_or_null(self):
        with self.assertRaises(ValidationError):
            JobMetadataPatch.model_validate({"script_snapshot": ["not", "an", "object"]})

    def test_label_is_trimmed_and_blank_label_clears_metadata(self):
        labelled = JobMetadataPatch.model_validate({"label": "  run A  "})
        cleared = JobMetadataPatch.model_validate({"label": "   "})

        self.assertEqual(labelled.label, "run A")
        self.assertIsNone(cleared.label)


class HornLabMesherBemMeshContractTest(unittest.TestCase):
    """HornLab mesher BEM path must pass wall_thickness through to build_waveguide_mesh unchanged.

    The outer wall shell is part of the BEM mesh (tag 1 in the ABEC/ATH convention).
    The queued request must preserve the selected symmetry-reduction domain, and
    the canonical mesh tags must pass through to solver mesh preparation unchanged.
    """

    def _make_hornlab_mesher_request(self, extra_params=None):
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
            solver_backend="bempp",
            options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": wp}},
        )

    def test_hornlab_mesher_preserves_wall_thickness_in_build_call(self):
        """run_simulation must NOT zero wall_thickness for HornLab mesher BEM builds.

        The outer wall shell (wall_thickness > 0) is part of the BEM mesh: it forms the
        topologically connected rigid-wall boundary that encloses the horn cavity.
        The runner must forward the queued request to `build_waveguide_mesh`
        without repairing or zeroing wall shell geometry on the way through.
        """
        request = self._make_hornlab_mesher_request({"wall_thickness": 6.0, "enc_depth": 0.0})

        captured_params = []

        def fake_build(params, **kwargs):
            captured_params.append(dict(params))
            return {
                "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
                "stats": {"nodeCount": 3, "elementCount": 1},
                "canonical_mesh": {
                    "vertices": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                    "indices": [0, 1, 2],
                    "surfaceTags": [2],
                },
            }

        fake_mesh = {
            "grid": type(
                "G",
                (),
                {"vertices": [[0, 0, 0]], "elements": [[0]], "domain_indices": [2]},
            )(),
            "surface_tags": [2],
        }

        async def fake_subprocess_solve(*_args, **_kwargs):
            return {"frequencies": [100.0], "directivity": {}}

        job_id = "test-preserve-wall"
        _jrt.jobs[job_id] = {
            "status": "queued", "progress": 0.0, "stage": "queued",
            "stage_message": "", "results": None, "error": None,
        }
        try:
            with patch("services.simulation_runner.BEMSolver"), patch(
                "services.simulation_runner.HORNLAB_MESHER_AVAILABLE", True
            ), patch(
                "services.simulation_runner.HORNLAB_MESHER_RUNTIME_READY", True
            ), patch(
                "services.simulation_runner.build_waveguide_mesh",
                side_effect=fake_build,
            ), patch(
                "solver.mesh.load_msh_for_bem",
                return_value=fake_mesh,
            ), patch(
                "services.simulation_runner._run_solve_in_subprocess",
                side_effect=fake_subprocess_solve,
            ), patch(
                "services.simulation_runner.db"
            ):
                asyncio.run(_sim_runner.run_simulation(job_id, request))

            self.assertEqual(_jrt.jobs[job_id]["status"], "complete")
        finally:
            _jrt.jobs.pop(job_id, None)

        self.assertTrue(
            len(captured_params) > 0,
            "build_waveguide_mesh must be called during hornlab_mesher run_simulation.",
        )
        call_params = captured_params[0]
        self.assertEqual(
            call_params.get("wall_thickness"),
            6.0,
            "HornLab mesher BEM build must preserve wall_thickness (outer wall is part of BEM mesh).",
        )

    def test_hornlab_mesher_forces_full_domain_quadrants_for_bempp(self):
        """Non-1234 quadrants are accepted but BEMPP solves must use full-domain meshing."""
        request = self._make_hornlab_mesher_request({"quadrants": 14})

        fake_mesher_result = {
            "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            "stats": {"nodeCount": 5, "elementCount": 4},
            "canonical_mesh": {
                "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0.5, 0, 0.5, 0.5, 0.5, 0],
                "indices": [0, 1, 2, 1, 2, 3, 2, 3, 4, 0, 2, 4],
                "surfaceTags": [1, 1, 2, 1],
                "metadata": {},
            },
        }

        fake_mesh = {"grid": type("G", (), {"vertices": [[0,0,0]], "elements": [[0]], "domain_indices": [2]})(), "surface_tags": [2]}

        async def fake_subprocess_solve(*_args, **_kwargs):
            return {"frequencies": [100.0], "directivity": {}}

        job_id = "test-hornlab-quadrants-accepted"
        _jrt.jobs[job_id] = {
            "status": "queued", "progress": 0.0, "stage": "queued",
            "stage_message": "", "results": None, "error": None,
        }
        try:
            with patch("services.simulation_runner.BEMSolver"), patch(
                "services.simulation_runner.HORNLAB_MESHER_AVAILABLE", True
            ), patch(
                "services.simulation_runner.HORNLAB_MESHER_RUNTIME_READY", True
            ), patch(
                "services.simulation_runner.build_waveguide_mesh",
                return_value=fake_mesher_result,
            ) as build_mesh, patch(
                "solver.mesh.load_msh_for_bem",
                return_value=fake_mesh,
            ), patch(
                "services.simulation_runner._run_solve_in_subprocess",
                side_effect=fake_subprocess_solve,
            ), patch(
                "services.simulation_runner.db"
            ):
                asyncio.run(_sim_runner.run_simulation(job_id, request))

            build_mesh.assert_called_once()
            forwarded_params = build_mesh.call_args.args[0]
            self.assertEqual(forwarded_params.get("quadrants"), 1234,
                "BEMPP solve path must force full-domain quadrants before meshing")
            # The job should not be in error state — non-1234 imports are tolerated.
            self.assertNotEqual(_jrt.jobs[job_id].get("status"), "error",
                "Non-1234 quadrants should be accepted for HornLab mesher path")
        finally:
            _jrt.jobs.pop(job_id, None)

    def test_hornlab_mesher_preserves_canonical_surface_tags_for_solver_mesh(self):
        request = self._make_hornlab_mesher_request()

        fake_mesher_result = {
            "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            "stats": {"nodeCount": 5, "elementCount": 4},
            "canonical_mesh": {
                "vertices": [
                    0.0, 0.0, 0.0,
                    1.0, 0.0, 0.0,
                    1.0, 1.0, 0.0,
                    0.0, 1.0, 0.0,
                    0.5, 0.5, 0.5,
                ],
                "indices": [
                    0, 1, 4,
                    1, 2, 4,
                    2, 3, 4,
                    3, 0, 4,
                ],
                "surfaceTags": [1, 2, 3, 4],
                "metadata": {
                    "identityTriangleCounts": {
                        "inner_wall": 1,
                        "outer_wall": 0,
                        "mouth_rim": 0,
                        "throat_return": 0,
                        "rear_cap": 0,
                        "horn_wall": 0,
                        "throat_disc": 1,
                        "enc_front": 0,
                        "enc_side": 0,
                        "enc_rear": 0,
                        "enc_edge": 0,
                    }
                },
            },
        }

        fake_mesh = {"grid": type("G", (), {"vertices": [[0,0,0]], "elements": [[0]], "domain_indices": [2]})(), "surface_tags": [2]}

        async def fake_subprocess_solve(*_args, **_kwargs):
            return {"frequencies": [100.0], "directivity": {}}

        job_id = "test-hornlab-canonical-tags"
        _jrt.jobs[job_id] = {
            "status": "queued", "progress": 0.0, "stage": "queued",
            "stage_message": "", "results": None, "error": None,
        }
        try:
            with patch("services.simulation_runner.BEMSolver"), patch(
                "services.simulation_runner.HORNLAB_MESHER_AVAILABLE", True
            ), patch(
                "services.simulation_runner.HORNLAB_MESHER_RUNTIME_READY", True
            ), patch(
                "services.simulation_runner.build_waveguide_mesh", return_value=fake_mesher_result
            ), patch(
                "solver.mesh.load_msh_for_bem", return_value=fake_mesh
            ), patch(
                "services.simulation_runner._run_solve_in_subprocess",
                side_effect=fake_subprocess_solve,
            ):
                asyncio.run(_sim_runner.run_simulation(job_id, request))

            # Canonical surface tags are now captured in mesh_stats (not passed to prepare_mesh).
            mesh_stats = _jrt.jobs[job_id].get("mesh_stats", {})
            self.assertEqual(mesh_stats.get("tag_counts"), {1: 1, 2: 1, 3: 1, 4: 1})
            self.assertEqual(_jrt.jobs[job_id]["status"], "complete")
        finally:
            _jrt.jobs.pop(job_id, None)

    def test_hornlab_mesher_publishes_mesh_stats_after_canonical_mesh_build(self):
        request = self._make_hornlab_mesher_request()

        fake_mesher_result = {
            "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            "stats": {"nodeCount": 5, "elementCount": 4},
            "canonical_mesh": {
                "vertices": [
                    0.0, 0.0, 0.0,
                    1.0, 0.0, 0.0,
                    1.0, 1.0, 0.0,
                    0.0, 1.0, 0.0,
                    0.5, 0.5, 0.5,
                ],
                "indices": [
                    0, 1, 4,
                    1, 2, 4,
                    2, 3, 4,
                    3, 0, 4,
                ],
                "surfaceTags": [1, 2, 3, 4],
                "metadata": {
                    "identityTriangleCounts": {
                        "inner_wall": 1,
                        "outer_wall": 0,
                        "mouth_rim": 0,
                        "throat_return": 0,
                        "rear_cap": 0,
                        "horn_wall": 0,
                        "throat_disc": 1,
                        "enc_front": 0,
                        "enc_side": 0,
                        "enc_rear": 0,
                        "enc_edge": 0,
                    }
                },
            },
        }

        fake_mesh = {"grid": type("G", (), {"vertices": [[0,0,0]], "elements": [[0]], "domain_indices": [2]})(), "surface_tags": [2]}

        async def fake_subprocess_solve(*_args, **_kwargs):
            return {"frequencies": [100.0], "directivity": {}}

        job_id = "test-hornlab-mesh-stats"
        _jrt.jobs[job_id] = {
            "status": "queued", "progress": 0.0, "stage": "queued",
            "stage_message": "", "results": None, "error": None,
        }
        try:
            with patch("services.simulation_runner.BEMSolver"), patch(
                "services.simulation_runner.HORNLAB_MESHER_AVAILABLE", True
            ), patch(
                "services.simulation_runner.HORNLAB_MESHER_RUNTIME_READY", True
            ), patch(
                "services.simulation_runner.build_waveguide_mesh", return_value=fake_mesher_result
            ), patch(
                "solver.mesh.load_msh_for_bem", return_value=fake_mesh
            ), patch(
                "services.simulation_runner._run_solve_in_subprocess",
                side_effect=fake_subprocess_solve,
            ):
                asyncio.run(_sim_runner.run_simulation(job_id, request))

            self.assertEqual(
                _jrt.jobs[job_id].get("mesh_stats"),
                {
                    "vertex_count": 5,
                    "triangle_count": 4,
                    "source": "hornlab_waveguide_mesher",
                    "tag_counts": {1: 1, 2: 1, 3: 1, 4: 1},
                    "identity_triangle_counts": {
                        "inner_wall": 1,
                        "outer_wall": 0,
                        "mouth_rim": 0,
                        "throat_return": 0,
                        "rear_cap": 0,
                        "horn_wall": 0,
                        "throat_disc": 1,
                        "enc_front": 0,
                        "enc_side": 0,
                        "enc_rear": 0,
                        "enc_edge": 0,
                    },
                },
            )
        finally:
            _jrt.jobs.pop(job_id, None)


class MeshArtifactEndpointTest(unittest.TestCase):
    def test_mesh_artifact_returns_404_for_unknown_job(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(get_mesh_artifact("nonexistent-job"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_mesh_artifact_returns_404_when_no_artifact(self):
        _jrt.jobs["test-no-artifact"] = {"status": "complete", "results": None}
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(get_mesh_artifact("test-no-artifact"))
            self.assertEqual(ctx.exception.status_code, 404)
            self.assertIn("No mesh artifact", str(ctx.exception.detail))
        finally:
            _jrt.jobs.pop("test-no-artifact", None)

    def test_mesh_artifact_returns_msh_text(self):
        msh_content = "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"
        _jrt.jobs["test-with-artifact"] = {
            "status": "complete",
            "results": None,
            "mesh_artifact": msh_content,
        }
        try:
            resp = asyncio.run(get_mesh_artifact("test-with-artifact"))
            self.assertEqual(resp.body.decode(), msh_content)
            self.assertIn("text/plain", resp.media_type)
        finally:
            _jrt.jobs.pop("test-with-artifact", None)


class StopSimulationLifecycleTest(unittest.TestCase):
    def test_stop_simulation_cancels_queued_job_immediately(self):
        job_id = "test-stop-queued"
        _jrt.jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0.0,
            "stage": "queued",
            "stage_message": "Job queued",
            "error_message": None,
            "cancellation_requested": False,
        }
        _jrt.job_queue.append(job_id)
        try:
            response = asyncio.run(stop_simulation(job_id))

            self.assertEqual(response["status"], "cancelled")
            self.assertEqual(_jrt.jobs[job_id]["status"], "cancelled")
            self.assertEqual(_jrt.jobs[job_id]["stage"], "cancelled")
            self.assertFalse(_jrt.jobs[job_id]["cancellation_requested"])
        finally:
            _jrt.jobs.pop(job_id, None)
            while job_id in _jrt.job_queue:
                _jrt.job_queue.remove(job_id)

    def test_stop_simulation_marks_running_job_as_cancelling_until_worker_acknowledges(self):
        job_id = "test-stop-running"
        _jrt.jobs[job_id] = {
            "id": job_id,
            "status": "running",
            "progress": 0.45,
            "stage": "bem_solve",
            "stage_message": "Solving frequency 2/5",
            "error_message": None,
            "completed_at": None,
            "cancellation_requested": False,
        }
        try:
            response = asyncio.run(stop_simulation(job_id))

            self.assertEqual(response["status"], "cancelling")
            self.assertEqual(_jrt.jobs[job_id]["status"], "running")
            self.assertEqual(_jrt.jobs[job_id]["stage"], "cancelling")
            self.assertTrue(_jrt.jobs[job_id]["cancellation_requested"])
            self.assertIsNone(_jrt.jobs[job_id].get("completed_at"))
        finally:
            _jrt.jobs.pop(job_id, None)


class CooperativeCancellationRunnerTest(unittest.TestCase):
    def _fake_mesher_result(self):
        return {
            "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            "stats": {"nodeCount": 3, "elementCount": 1},
            "canonical_mesh": {
                "vertices": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                "indices": [0, 1, 2],
                "surfaceTags": [2],
                "metadata": {},
            },
        }

    def _fake_solver_mesh(self):
        return {
            "grid": type(
                "G",
                (),
                {"vertices": [[0, 0, 0]], "elements": [[0]], "domain_indices": [2]},
            )(),
            "surface_tags": [2],
        }

    def _make_minimal_request(self):
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
            num_frequencies=2,
            sim_type="2",
            solver_backend="bempp",
            options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": {
                "formula_type": "OSSE",
                "wall_thickness": 6.0,
                "enc_depth": 0.0,
            }}},
        )

    def _make_job_entry(self, job_id, *, cancellation_requested=False):
        return {
            "id": job_id,
            "status": "running",
            "progress": 0.5,
            "stage": "bem_solve",
            "stage_message": "Solving",
            "results": None,
            "error": None,
            "error_message": None,
            "has_results": False,
            "has_mesh_artifact": False,
            "cancellation_requested": cancellation_requested,
        }

    def test_run_simulation_exits_cancelled_when_stop_was_requested_before_solver_work(self):
        job_id = "test-runner-cancelled-before-start"
        _jrt.jobs[job_id] = self._make_job_entry(job_id, cancellation_requested=True)

        class MockSolver:
            def __init__(self):
                self.created = True

            def prepare_mesh(self, *_args, **_kwargs):
                raise AssertionError("prepare_mesh must not run once cancellation is requested")

        try:
            with patch("services.simulation_runner.BEMSolver", MockSolver), \
                 patch("services.simulation_runner.HORNLAB_MESHER_AVAILABLE", True), \
                 patch("services.simulation_runner.HORNLAB_MESHER_RUNTIME_READY", True):
                asyncio.run(_sim_runner.run_simulation(job_id, self._make_minimal_request()))

            self.assertEqual(_jrt.jobs[job_id]["status"], "cancelled")
            self.assertEqual(_jrt.jobs[job_id]["stage"], "cancelled")
            self.assertFalse(_jrt.jobs[job_id]["cancellation_requested"])
        finally:
            _jrt.jobs.pop(job_id, None)

    def test_run_simulation_transitions_to_cancelled_when_solver_callback_acknowledges_stop(self):
        job_id = "test-runner-cancelled-during-solve"
        _jrt.jobs[job_id] = self._make_job_entry(job_id)

        class MockSolver:
            def prepare_mesh(self, *_args, **_kwargs):
                return {"grid": type("G", (), {"vertices": [[0,0,0]], "elements": [[0]], "domain_indices": [2]})(), "surface_tags": [2]}

        async def fake_subprocess_solve(*_args, **_kwargs):
            _jrt.jobs[job_id]["cancellation_requested"] = True
            raise _sim_runner.SimulationCancelled("Simulation cancelled by user")

        try:
            with patch("services.simulation_runner.BEMSolver", MockSolver), \
                 patch("services.simulation_runner.HORNLAB_MESHER_AVAILABLE", True), \
                 patch("services.simulation_runner.HORNLAB_MESHER_RUNTIME_READY", True), \
                 patch("services.simulation_runner.build_waveguide_mesh", return_value=self._fake_mesher_result()), \
                 patch("solver.mesh.load_msh_for_bem", return_value=self._fake_solver_mesh()), \
                 patch("services.simulation_runner._run_solve_in_subprocess", side_effect=fake_subprocess_solve):
                asyncio.run(_sim_runner.run_simulation(job_id, self._make_minimal_request()))

            self.assertEqual(_jrt.jobs[job_id]["status"], "cancelled")
            self.assertEqual(_jrt.jobs[job_id]["stage"], "cancelled")
            self.assertEqual(
                _jrt.jobs[job_id]["error_message"],
                "Simulation cancelled by user",
            )
        finally:
            _jrt.jobs.pop(job_id, None)

    def test_run_simulation_maps_internal_substages_to_core_job_stages(self):
        job_id = "test-runner-core-stage-contract"
        _jrt.jobs[job_id] = self._make_job_entry(job_id)

        class MockSolver:
            def prepare_mesh(self, *_args, **_kwargs):
                return {"grid": type("G", (), {"vertices": [[0,0,0]], "elements": [[0]], "domain_indices": [2]})(), "surface_tags": [2]}

        async def fake_subprocess_solve(*_args, **_kwargs):
            return {"frequencies": [100.0], "directivity": {}}

        try:
            with patch("services.simulation_runner.BEMSolver", MockSolver), \
                 patch("services.simulation_runner.HORNLAB_MESHER_AVAILABLE", True), \
                 patch("services.simulation_runner.HORNLAB_MESHER_RUNTIME_READY", True), \
                 patch("services.simulation_runner.build_waveguide_mesh", return_value=self._fake_mesher_result()), \
                 patch("solver.mesh.load_msh_for_bem", return_value=self._fake_solver_mesh()), \
                 patch("services.simulation_runner._run_solve_in_subprocess", side_effect=fake_subprocess_solve), \
                 patch("services.simulation_runner.update_job_stage") as update_stage_mock:
                asyncio.run(_sim_runner.run_simulation(job_id, self._make_minimal_request()))

            stages = [
                call.args[1]
                for call in update_stage_mock.call_args_list
                if len(call.args) >= 2
            ]
            self.assertIn("initializing", stages)
            self.assertIn("mesh_prepare", stages)
            self.assertIn("bem_solve", stages)
            self.assertEqual(_jrt.jobs[job_id]["status"], "complete")
        finally:
            _jrt.jobs.pop(job_id, None)


class JobPersistenceFailureSafetyTest(unittest.TestCase):
    """Verify that persistence failures do not leave jobs in false-complete state."""

    def _fake_mesher_result(self):
        return {
            "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            "stats": {"nodeCount": 3, "elementCount": 1},
            "canonical_mesh": {
                "vertices": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                "indices": [0, 1, 2],
                "surfaceTags": [2],
                "metadata": {},
            },
        }

    def _fake_solver_mesh(self):
        return {"grid": type("G", (), {"vertices": [[0,0,0]], "elements": [[0]], "domain_indices": [2]})(), "surface_tags": [2]}

    def _make_minimal_request(self):
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
            solver_backend="bempp",
            options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": {
                "formula_type": "OSSE",
                "wall_thickness": 6.0,
                "enc_depth": 0.0,
            }}},
        )

    def _make_job_entry(self, job_id):
        return {
            "id": job_id,
            "status": "running",
            "progress": 0.5,
            "stage": "running",
            "stage_message": "running",
            "results": None,
            "error": None,
            "error_message": None,
            "has_results": False,
            "has_mesh_artifact": False,
            "cancellation_requested": False,
        }

    def test_results_persistence_failure_leaves_error_not_complete(self):
        """Job must end in 'error' state (not 'complete') when db.store_results raises."""
        job_id = "test-persist-fail-status"
        _jrt.jobs[job_id] = self._make_job_entry(job_id)

        class MockSolver:
            def prepare_mesh(self, *args, **kwargs):
                return {"grid": type("G", (), {"vertices": [[0,0,0]], "elements": [[0]], "domain_indices": [2]})(), "surface_tags": [2]}

        async def fake_subprocess_solve(*_args, **_kwargs):
            return {"frequencies": [100.0], "directivity": {}}

        try:
            with patch("services.simulation_runner.BEMSolver", MockSolver), \
                 patch("services.simulation_runner.HORNLAB_MESHER_AVAILABLE", True), \
                 patch("services.simulation_runner.HORNLAB_MESHER_RUNTIME_READY", True), \
                 patch("services.simulation_runner.build_waveguide_mesh", return_value=self._fake_mesher_result()), \
                 patch("solver.mesh.load_msh_for_bem", return_value=self._fake_solver_mesh()), \
                 patch("services.simulation_runner._run_solve_in_subprocess", side_effect=fake_subprocess_solve), \
                 patch.object(_sim_runner.db, "store_results", side_effect=OSError("disk full")):
                asyncio.run(_sim_runner.run_simulation(job_id, self._make_minimal_request()))

            final_status = _jrt.jobs.get(job_id, {}).get("status")
            self.assertNotEqual(
                final_status, "complete",
                "Job must not be left in 'complete' state when results persistence fails.",
            )
            self.assertEqual(
                final_status, "error",
                "Job must be in 'error' state when results persistence fails.",
            )
        finally:
            _jrt.jobs.pop(job_id, None)

    def test_results_persistence_failure_error_message_is_safe(self):
        """Error message on persistence failure must not expose internal exception details."""
        job_id = "test-persist-fail-msg"
        _jrt.jobs[job_id] = self._make_job_entry(job_id)

        class MockSolver:
            def prepare_mesh(self, *args, **kwargs):
                return {"grid": type("G", (), {"vertices": [[0,0,0]], "elements": [[0]], "domain_indices": [2]})(), "surface_tags": [2]}

        async def fake_subprocess_solve(*_args, **_kwargs):
            return {"frequencies": [100.0], "directivity": {}}

        try:
            with patch("services.simulation_runner.BEMSolver", MockSolver), \
                 patch("services.simulation_runner.HORNLAB_MESHER_AVAILABLE", True), \
                 patch("services.simulation_runner.HORNLAB_MESHER_RUNTIME_READY", True), \
                 patch("services.simulation_runner.build_waveguide_mesh", return_value=self._fake_mesher_result()), \
                 patch("solver.mesh.load_msh_for_bem", return_value=self._fake_solver_mesh()), \
                 patch("services.simulation_runner._run_solve_in_subprocess", side_effect=fake_subprocess_solve), \
                 patch.object(_sim_runner.db, "store_results", side_effect=OSError("disk full")):
                asyncio.run(_sim_runner.run_simulation(job_id, self._make_minimal_request()))

            error_msg = _jrt.jobs.get(job_id, {}).get("error_message", "")
            self.assertIsNotNone(error_msg, "Error message must be set on persistence failure.")
            self.assertNotIn("Traceback", error_msg, "Error message must not contain Python traceback.")
            self.assertNotIn("OSError", error_msg, "Error message must not expose internal exception class.")
        finally:
            _jrt.jobs.pop(job_id, None)

    def test_mesh_artifact_persistence_failure_does_not_abort_simulation(self):
        """Simulation must complete even if db.store_mesh_artifact raises."""
        job_id = "test-artifact-persist-fail"
        _jrt.jobs[job_id] = self._make_job_entry(job_id)

        fake_msh = "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"
        fake_mesher_result = {
            "msh_text": fake_msh,
            "stats": {"nodeCount": 3, "elementCount": 1},
            "canonical_mesh": {
                "vertices": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                "indices": [0, 1, 2],
                "surfaceTags": [2],
            },
        }

        fake_mesh = {"grid": type("G", (), {"vertices": [[0,0,0]], "elements": [[0]], "domain_indices": [2]})(), "surface_tags": [2]}

        async def fake_subprocess_solve(*_args, **_kwargs):
            return {"frequencies": [100.0], "directivity": {}}

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
            num_frequencies=1,
            sim_type="2",
            solver_backend="bempp",
            options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": {
                "formula_type": "R-OSSE",
                "wall_thickness": 6.0,
                "enc_depth": 0.0,
            }}},
        )

        try:
            with patch("services.simulation_runner.BEMSolver"), \
                 patch("services.simulation_runner.HORNLAB_MESHER_AVAILABLE", True), \
                 patch("services.simulation_runner.HORNLAB_MESHER_RUNTIME_READY", True), \
                 patch("services.simulation_runner.build_waveguide_mesh", return_value=fake_mesher_result), \
                 patch("solver.mesh.load_msh_for_bem", return_value=fake_mesh), \
                 patch("services.simulation_runner._run_solve_in_subprocess", side_effect=fake_subprocess_solve), \
                 patch.object(_sim_runner.db, "store_mesh_artifact", side_effect=OSError("disk full")):
                asyncio.run(_sim_runner.run_simulation(job_id, request))

            final_status = _jrt.jobs.get(job_id, {}).get("status")
            self.assertEqual(
                final_status, "complete",
                "Simulation must complete even when mesh artifact persistence fails.",
            )
            self.assertFalse(
                _jrt.jobs.get(job_id, {}).get("has_mesh_artifact", True),
                "has_mesh_artifact must be False when artifact persistence fails.",
            )
        finally:
            _jrt.jobs.pop(job_id, None)


class HttpSemanticsTest(unittest.TestCase):
    """Verify that HTTP status codes follow the Gate A contract.

    - missing result resource  -> 404
    - validation failures       -> 422
    - dependency unavailable    -> 503
    - unexpected server error   -> 500
    """

    def test_get_results_missing_stored_results_returns_404(self):
        """get_results must return 404 when the DB has no stored results for a complete job."""
        job_id = "test-missing-stored-results"
        _jrt.jobs[job_id] = {"status": "complete", "results": None}
        try:
            with patch.object(_jrt.db, "get_results", return_value=None):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(get_results(job_id))
            self.assertEqual(ctx.exception.status_code, 404)
            self.assertIn("not available", str(ctx.exception.detail).lower())
        finally:
            _jrt.jobs.pop(job_id, None)

    def test_render_directivity_empty_input_returns_422(self):
        """render_directivity must return 422 (not 400) for missing request data."""
        request = DirectivityRenderRequest(frequencies=[], directivity={})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(render_directivity(request))
        self.assertEqual(ctx.exception.status_code, 422)

    def test_get_results_unknown_job_returns_404(self):
        """get_results must return 404 for a job ID that does not exist."""
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(get_results("nonexistent-job-id-xyz"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_job_status_unknown_job_returns_404(self):
        """get_job_status must return 404 for an unknown job."""
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(get_job_status("nonexistent-job-id-xyz"))
        self.assertEqual(ctx.exception.status_code, 404)


class SchedulerStateTest(unittest.TestCase):
    """Verify that the scheduler guard is consistent with queue state."""

    def test_scheduler_skips_when_already_running(self):
        """_drain_scheduler_queue must exit immediately if scheduler_loop_running is True."""
        original = _jrt.scheduler_loop_running
        sentinel = "test-sentinel-job-id"
        try:
            with _jrt.jobs_lock:
                _jrt.scheduler_loop_running = True
            _jrt.job_queue.append(sentinel)
            asyncio.run(_jrt._drain_scheduler_queue())
            # Sentinel job must still be in the queue — scheduler did not consume it
            self.assertIn(sentinel, _jrt.job_queue, "Scheduler must not process jobs when already running.")
        finally:
            with _jrt.jobs_lock:
                _jrt.scheduler_loop_running = original
            if sentinel in _jrt.job_queue:
                _jrt.job_queue.remove(sentinel)

    def test_scheduler_loop_running_resets_after_empty_queue(self):
        """scheduler_loop_running must be False after drain completes with empty queue."""
        with _jrt.jobs_lock:
            _jrt.scheduler_loop_running = False

        asyncio.run(_jrt._drain_scheduler_queue())

        with _jrt.jobs_lock:
            running = _jrt.scheduler_loop_running
        self.assertFalse(running, "scheduler_loop_running must be reset to False after drain finishes.")


if __name__ == "__main__":
    unittest.main()
