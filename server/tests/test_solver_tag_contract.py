import unittest
from pathlib import Path


class SolverTagContractTest(unittest.TestCase):
    def test_source_tag_contract_is_enforced_by_active_solver_path(self):
        solver_dir = Path(__file__).resolve().parents[1].joinpath("solver")
        bem_solver_text = solver_dir.joinpath("bem_solver.py").read_text()
        optimized_text = solver_dir.joinpath("solve.py").read_text()
        mesh_text = solver_dir.joinpath("mesh.py").read_text()

        # Runtime entrypoint should no longer depend on the legacy solve.py module.
        self.assertIn("from .solve import solve_optimized", bem_solver_text)
        self.assertNotIn("def _solve_frequency(", bem_solver_text)

        # solve_optimized.py (HornBEMSolver) uses full DP0 with throat DOFs zeroed.
        # The driver_dofs selection is the equivalent tag-2 contract.
        self.assertIn("tag_throat", optimized_text)
        self.assertIn("driver_dofs", optimized_text)

        # mesh.py must enforce tag-2 source presence during prepare_mesh.
        self.assertIn("np.count_nonzero(domain_indices == 2)", mesh_text)


if __name__ == "__main__":
    unittest.main()
