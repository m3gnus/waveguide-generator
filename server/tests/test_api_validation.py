import asyncio
import unittest

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


if __name__ == "__main__":
    unittest.main()
