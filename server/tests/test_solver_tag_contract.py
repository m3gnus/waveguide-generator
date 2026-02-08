import unittest
from pathlib import Path


class SolverTagContractTest(unittest.TestCase):
    def test_source_segment_contract_is_tag_2(self):
        solver_dir = Path(__file__).resolve().parents[1].joinpath("solver")
        solve_text = solver_dir.joinpath("solve.py").read_text()
        optimized_text = solver_dir.joinpath("solve_optimized.py").read_text()
        self.assertIn("segments=[2]", solve_text)
        self.assertIn("segments=[2]", optimized_text)


if __name__ == "__main__":
    unittest.main()
