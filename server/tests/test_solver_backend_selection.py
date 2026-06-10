import unittest

from solver.metal_solver import normalize_solver_backend, resolve_solver_backend


class SolverBackendSelectionTest(unittest.TestCase):
    def test_normalize_accepts_auto_backend_aliases(self):
        self.assertEqual(normalize_solver_backend(None), "auto")
        self.assertEqual(normalize_solver_backend("default"), "auto")
        self.assertEqual(normalize_solver_backend("native"), "auto")
        self.assertEqual(normalize_solver_backend("hornlab-metal-bem"), "metal")

    def test_normalize_rejects_removed_bempp_backend(self):
        for legacy_value in ("bempp", "bempp-cl", "bempp_cl", "previous"):
            with self.assertRaises(ValueError):
                normalize_solver_backend(legacy_value)

    def test_auto_resolves_to_metal(self):
        self.assertEqual(
            resolve_solver_backend("auto", mesh_strategy="hornlab_mesher"),
            "metal",
        )
        self.assertEqual(resolve_solver_backend("auto"), "metal")

    def test_explicit_metal_backend_is_preserved(self):
        self.assertEqual(resolve_solver_backend("metal"), "metal")
