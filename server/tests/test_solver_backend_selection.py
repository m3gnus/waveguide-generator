import unittest
from unittest.mock import patch

from solver.metal_solver import (
    is_metal_fast_solve_ready,
    metal_fast_solve_unavailable_reason,
    normalize_solver_backend,
    resolve_solver_backend,
)


class SolverBackendSelectionTest(unittest.TestCase):
    def test_normalize_accepts_auto_backend_aliases(self):
        self.assertEqual(normalize_solver_backend(None), "auto")
        self.assertEqual(normalize_solver_backend("default"), "auto")
        self.assertEqual(normalize_solver_backend("native"), "auto")
        self.assertEqual(normalize_solver_backend("hornlab-metal-bem"), "metal")

    def test_normalize_accepts_bempp_backend_aliases(self):
        for legacy_value in ("bempp", "bempp-cl", "bempp_cl", "previous"):
            with self.subTest(legacy_value=legacy_value):
                self.assertEqual(normalize_solver_backend(legacy_value), "bempp")

    def test_auto_resolves_to_metal(self):
        with patch(
            "solver.metal_solver.metal_backend_status",
            return_value={"available": True},
        ), patch(
            "solver.bempp_solver.bempp_backend_status",
            return_value={"available": True},
        ):
            self.assertEqual(
                resolve_solver_backend("auto", mesh_strategy="hornlab_mesher"),
                "metal",
            )
            self.assertEqual(resolve_solver_backend("auto"), "metal")

    def test_auto_resolves_to_bempp_when_metal_is_unavailable(self):
        with patch(
            "solver.metal_solver.metal_backend_status",
            return_value={"available": False},
        ), patch(
            "solver.bempp_solver.bempp_backend_status",
            return_value={"available": True},
        ):
            self.assertEqual(resolve_solver_backend("auto"), "bempp")

    def test_auto_falls_back_to_metal_when_neither_backend_is_available(self):
        with patch(
            "solver.metal_solver.metal_backend_status",
            return_value={"available": False},
        ), patch(
            "solver.bempp_solver.bempp_backend_status",
            return_value={"available": False},
        ):
            self.assertEqual(resolve_solver_backend("auto"), "metal")

    def test_explicit_backends_are_preserved(self):
        self.assertEqual(resolve_solver_backend("metal"), "metal")
        self.assertEqual(resolve_solver_backend("bempp"), "bempp")

    def test_apple_silicon_fast_metal_readiness_requires_release_helper(self):
        status = {
            "available": True,
            "nativeHelperAvailable": True,
            "nativeHelperBuild": "debug",
            "nativeHelperPath": "/tmp/HornlabMetalBemNative",
            "reason": None,
        }
        with patch("solver.metal_solver.platform.system", return_value="Darwin"), patch(
            "solver.metal_solver.platform.machine", return_value="arm64"
        ):
            self.assertFalse(is_metal_fast_solve_ready(status))
            reason = metal_fast_solve_unavailable_reason(status)

        self.assertIn("fastest solve requires", reason)
        self.assertIn("build=debug", reason)
        self.assertIn("npm run build:metal-helper", reason)

    def test_non_apple_metal_readiness_uses_backend_availability(self):
        status = {
            "available": True,
            "nativeHelperAvailable": True,
            "nativeHelperBuild": "debug",
            "reason": None,
        }
        with patch("solver.metal_solver.platform.system", return_value="Linux"), patch(
            "solver.metal_solver.platform.machine", return_value="x86_64"
        ):
            self.assertTrue(is_metal_fast_solve_ready(status))
