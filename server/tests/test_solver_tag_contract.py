import unittest
from pathlib import Path


class SolverTagContractTest(unittest.TestCase):
    def test_source_tag_contract_is_enforced_by_active_solver_path(self):
        server_dir = Path(__file__).resolve().parents[1]
        runner_text = server_dir.joinpath("services", "simulation_runner.py").read_text()

        # The runner must reject canonical meshes without source-tagged (tag 2)
        # elements before any solve is attempted.
        self.assertIn("if 2 not in normalized_surface_tags", runner_text)
        self.assertIn("no source-tagged elements (tag 2)", runner_text)

        # The Metal adapter is the only solve dispatch path.
        self.assertIn("solve_metal_from_msh", runner_text)
        self.assertNotIn("BEMSolver", runner_text)


if __name__ == "__main__":
    unittest.main()
