import unittest
from pathlib import Path


class SolverTagContractTest(unittest.TestCase):
    def test_source_segment_contract_is_tag_2(self):
        solver_dir = Path(__file__).resolve().parents[1].joinpath("solver")
        solve_text = solver_dir.joinpath("solve.py").read_text()
        optimized_text = solver_dir.joinpath("solve_optimized.py").read_text()
        mesh_text = solver_dir.joinpath("mesh.py").read_text()

        # Legacy solve.py still uses restricted segments=[2] space.
        self.assertIn("segments=[2]", solve_text)

        # solve_optimized.py (HornBEMSolver) uses full DP0 with throat DOFs zeroed.
        # The driver_dofs selection is the equivalent tag-2 contract.
        self.assertIn("tag_throat", optimized_text)
        self.assertIn("driver_dofs", optimized_text)

        # mesh.py must enforce tag-2 source presence during prepare_mesh.
        self.assertIn("surface_tags[i] == 2", mesh_text)


if __name__ == "__main__":
    unittest.main()
