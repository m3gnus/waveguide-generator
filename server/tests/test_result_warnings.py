"""Solver-log failure flags must surface as response-level warnings.

bempp records per-frequency GMRES convergence (``converged``) and metal the
LAPACK return code (``lapack_info``) plus conditioning diagnostics
(``native_diagnostics.dense_solve_suspect``). These were embedded raw in
``metadata[backend]["solver_log"]`` and never read: a non-converged frequency
rendered as normal data with ``warnings=[]`` and ``partial_success=False``.
"""

import unittest

from solver.result_mapping import _apply_solver_log_warnings


class SolverLogWarningsTest(unittest.TestCase):
    def test_bempp_non_converged_frequency_warns_and_marks_partial(self):
        metadata = {
            "warnings": [],
            "partial_success": False,
            "bempp": {
                "solver_log": [
                    {"frequency_hz": 1000.0, "converged": True},
                    {"frequency_hz": 2000.0, "converged": False},
                ]
            },
        }
        _apply_solver_log_warnings(metadata)
        self.assertEqual(len(metadata["warnings"]), 1)
        self.assertIn("2000.0 Hz", metadata["warnings"][0])
        self.assertIn("GMRES", metadata["warnings"][0])
        self.assertTrue(metadata["partial_success"])
        self.assertEqual(metadata["warning_count"], 1)

    def test_metal_lapack_failure_warns_and_marks_partial(self):
        metadata = {
            "warnings": [],
            "partial_success": False,
            "metal": {
                "solver_log": [
                    {"frequency_hz": 700.0, "lapack_info": 3},
                    {"frequency_hz": 800.0, "lapack_info": 0},
                ]
            },
        }
        _apply_solver_log_warnings(metadata)
        self.assertEqual(len(metadata["warnings"]), 1)
        self.assertIn("LAPACK info=3", metadata["warnings"][0])
        self.assertTrue(metadata["partial_success"])

    def test_metal_dense_solve_suspect_warns_without_partial(self):
        metadata = {
            "warnings": [],
            "partial_success": False,
            "metal": {
                "solver_log": [
                    {
                        "frequency_hz": 1234.0,
                        "lapack_info": 0,
                        "native_diagnostics": {"dense_solve_suspect": True},
                    }
                ]
            },
        }
        _apply_solver_log_warnings(metadata)
        self.assertEqual(len(metadata["warnings"]), 1)
        self.assertIn("conditioning", metadata["warnings"][0])
        self.assertFalse(metadata["partial_success"])

    def test_clean_log_leaves_metadata_untouched(self):
        metadata = {
            "warnings": [],
            "partial_success": False,
            "bempp": {
                "solver_log": [
                    {"frequency_hz": 1000.0, "converged": True},
                ]
            },
        }
        _apply_solver_log_warnings(metadata)
        self.assertEqual(metadata["warnings"], [])
        self.assertFalse(metadata["partial_success"])
        self.assertEqual(metadata["warning_count"], 0)

    def test_missing_solver_log_is_a_no_op(self):
        metadata = {"warnings": [], "partial_success": False}
        _apply_solver_log_warnings(metadata)
        self.assertEqual(metadata["warnings"], [])
        self.assertFalse(metadata["partial_success"])


if __name__ == "__main__":
    unittest.main()
