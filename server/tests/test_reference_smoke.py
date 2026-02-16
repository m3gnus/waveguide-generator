import os
import unittest
from pathlib import Path

try:
    import meshio
except ImportError:  # pragma: no cover
    meshio = None

from solver.mesh import prepare_mesh
from solver.solve_optimized import solve_optimized


@unittest.skipUnless(
    os.getenv("RUN_BEM_REFERENCE") == "1",
    "Set RUN_BEM_REFERENCE=1 to run reference BEM smoke tests.",
)
@unittest.skipIf(meshio is None, "meshio is required for reference smoke tests.")
class ReferenceSmokeTest(unittest.TestCase):
    def test_waveguide_reference_has_nonzero_solution(self):
        ref_path = (
            Path(__file__)
            .resolve()
            .parents[2]
            .joinpath("_references/BEM/BEMPP Ath4 Solver Prerelease/waveguide.msh")
        )
        self.assertTrue(ref_path.exists(), f"Missing reference mesh: {ref_path}")

        msh = meshio.read(ref_path)
        tri_key = "triangle" if "triangle" in msh.cells_dict else "triangle3"
        triangles = msh.cells_dict[tri_key]
        tags = None
        for key, by_type in msh.cell_data_dict.items():
            if tri_key in by_type and "physical" in key:
                tags = by_type[tri_key]
                break
        self.assertIsNotNone(tags, "Reference mesh must provide physical tags.")

        mesh = prepare_mesh(
            vertices=msh.points.reshape(-1).tolist(),
            indices=triangles.reshape(-1).tolist(),
            surface_tags=tags.tolist(),
            mesh_metadata={"units": "mm", "unitScaleToMeter": 0.001},
        )

        results = solve_optimized(
            mesh=mesh,
            frequency_range=[1000.0, 1000.0],
            num_frequencies=1,
            sim_type="2",
            enable_symmetry=False,
            verbose=False,
            mesh_validation_mode="off",
        )

        spl_value = results["spl_on_axis"]["spl"][0]
        self.assertIsNotNone(spl_value)
        self.assertGreater(float(spl_value), 0.0)
        self.assertEqual(results["metadata"]["failure_count"], 0)


if __name__ == "__main__":
    unittest.main()
