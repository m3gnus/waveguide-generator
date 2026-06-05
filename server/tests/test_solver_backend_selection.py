import unittest
from unittest.mock import patch

from solver.metal_solver import normalize_solver_backend, resolve_solver_backend


class SolverBackendSelectionTest(unittest.TestCase):
    def test_normalize_accepts_auto_backend_aliases(self):
        self.assertEqual(normalize_solver_backend(None), "auto")
        self.assertEqual(normalize_solver_backend("default"), "auto")
        self.assertEqual(normalize_solver_backend("native"), "auto")
        self.assertEqual(normalize_solver_backend("bempp-cl"), "bempp")
        self.assertEqual(normalize_solver_backend("hornlab-metal-bem"), "metal")

    def test_auto_prefers_metal_when_available(self):
        with patch("solver.metal_solver.is_metal_solver_available", return_value=True):
            self.assertEqual(
                resolve_solver_backend("auto", mesh_strategy="hornlab_mesher"),
                "metal",
            )

    def test_auto_uses_bempp_for_canonical_mesh_even_when_metal_available(self):
        with patch("solver.metal_solver.is_metal_solver_available", return_value=True):
            self.assertEqual(resolve_solver_backend("auto"), "bempp")

    def test_auto_falls_back_to_bempp_without_metal(self):
        with patch("solver.metal_solver.is_metal_solver_available", return_value=False):
            self.assertEqual(resolve_solver_backend("auto"), "bempp")

    def test_explicit_backend_is_preserved(self):
        with patch("solver.metal_solver.is_metal_solver_available", return_value=True):
            self.assertEqual(resolve_solver_backend("bempp"), "bempp")
            self.assertEqual(resolve_solver_backend("metal"), "metal")
